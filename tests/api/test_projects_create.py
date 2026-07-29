"""Tests for `POST /api/projects` (Slice 4, Task 6 - the Create wizard's
backend half).

Every test in this file monkeypatches `avg.api.app.run_pipeline` (the
`avg.pipeline.runner.run` generator, imported by name into `avg.api.app`) -
the real `REGISTRY` phases call actual AI providers, and this suite must
never make a real network call. The autouse `_stub_pipeline` fixture below
installs an instant no-op fake for every test; individual tests that need
to observe *how* the route drives the generator (event broadcast, the
cancellation token, the one-job-at-a-time guard) install their own fake in
their own body, which simply re-patches the same attribute for that test.

Isolation mirrors `tests/api/test_routes_settings.py`'s `paths` fixture -
every path is tmp_path-scoped so this suite never touches the real user
profile's channels/settings/secrets.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.config.channels import get_channel
from avg.core.events import PhaseFinished, PhaseStarted
from avg.core.takes import clone_take, take_folder

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def paths(tmp_path):
    return {
        "settings_path": tmp_path / "settings.json",
        "channels_dir": tmp_path / "channels",
        "secrets_path": tmp_path / "secrets.dat",
        "output_root": tmp_path / "output",
        "thumb_cache_dir": tmp_path / "thumbs",
    }


@pytest.fixture
def client(paths):
    app = create_app(
        token=TOKEN,
        output_root=paths["output_root"],
        thumb_cache_dir=paths["thumb_cache_dir"],
        settings_path=paths["settings_path"],
        channels_dir=paths["channels_dir"],
        secrets_path=paths["secrets_path"],
    )
    return TestClient(app)


def _instant_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
    """A fake `run_pipeline` that does nothing and finishes immediately -
    never calls a phase, never makes a network call. Still a generator
    function (the lone `yield` below is unreachable) so calling it returns
    a generator object, matching `run_pipeline`'s real shape."""
    return
    yield  # pragma: no cover


@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch):
    monkeypatch.setattr("avg.api.app.run_pipeline", _instant_run)


def _activate_channel(client: TestClient, label: str = "My Channel") -> str:
    created = client.post("/api/channels", headers=AUTH, json={"label": label}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_requires_token(client):
    assert client.post("/api/projects", json={"topic": "x"}).status_code == 401


def test_create_without_active_channel_is_400(client):
    r = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"})
    assert r.status_code == 400
    assert "active channel" in r.json()["detail"].lower()


def test_create_rejects_an_empty_topic(client):
    _activate_channel(client)
    r = client.post("/api/projects", headers=AUTH, json={"topic": "   "})
    assert r.status_code == 400


def test_create_rejects_an_unknown_override_id(client):
    _activate_channel(client)
    r = client.post(
        "/api/projects", headers=AUTH, json={"topic": "x", "overrides": {"not_a_real_setting": 1}}
    )
    assert r.status_code == 400
    assert "not_a_real_setting" in r.json()["detail"]


def test_create_rejects_an_app_scoped_override(client):
    # `theme` is scope="app" - only a per-video (channel-scoped) setting may
    # be overridden here.
    _activate_channel(client)
    r = client.post("/api/projects", headers=AUTH, json={"topic": "x", "overrides": {"theme": "neon"}})
    assert r.status_code == 400


def test_create_rejects_an_invalid_choice_override(client):
    _activate_channel(client)
    r = client.post(
        "/api/projects", headers=AUTH, json={"topic": "x", "overrides": {"video_form": "not_a_real_form"}}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_create_returns_201_with_the_new_project(client):
    _activate_channel(client)
    r = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "How Rome Fell"
    assert body["phases"] == {}
    assert body["id"]


def test_create_writes_the_project_folder_and_input_subfolders(client):
    _activate_channel(client)
    body = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"}).json()
    folder = Path(body["folder"])
    assert folder.is_dir()
    for sub in ("script", "images", "audio", "render"):
        assert (folder / sub).is_dir()


def test_create_appears_in_a_subsequent_list(client):
    _activate_channel(client)
    created = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"}).json()
    listed = client.get("/api/projects", headers=AUTH).json()["projects"]
    assert any(p["id"] == created["id"] for p in listed)


def test_list_projects_derives_script_substate_from_current_artifacts(client):
    _activate_channel(client)
    created = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"}).json()
    project = client.app.state.projects[created["id"]]
    project_root = Path(created["folder"])
    script = project_root / "script"

    def substate() -> str:
        listed = client.get("/api/projects", headers=AUTH).json()["projects"]
        return next(project for project in listed if project["id"] == created["id"])["script_substate"]

    assert substate() == "pending"
    (script / "narration.txt").write_text("Narration", encoding="utf-8")
    assert substate() == "partial"

    # Take 1 stores its artifacts AT the project root (Slice 8:
    # `Take.storage == "root"`), so the root files are its files - this
    # half used to assert the opposite, back when take 1 was created
    # lazily by `migrate_to_takes` with a folder of its own.
    (script / "scenes.json").write_text("[]", encoding="utf-8")
    assert substate() == "done"

    # What still has to hold: a take that OWNS a folder reads only its own
    # files, and the root's artifacts must not leak into it.
    own = clone_take(project, 1, [])
    project.current_take_id = own.id
    assert own.storage == "own"
    assert substate() == "pending"

    own_script = take_folder(project, own.id) / "script"
    own_script.mkdir(parents=True, exist_ok=True)
    (own_script / "narration.txt").write_text("Narration", encoding="utf-8")
    assert substate() == "partial"


def test_create_override_never_persists_to_the_channel_document(client, paths):
    channel_id = _activate_channel(client)
    r = client.post(
        "/api/projects", headers=AUTH, json={"topic": "x", "overrides": {"video_form": "longform"}}
    )
    assert r.status_code == 201

    on_disk = get_channel(channel_id, paths["channels_dir"])
    # The starter channel's own settings dict must be untouched - the
    # override lived only on a throwaway copy this one request built.
    assert "video_form" not in on_disk.settings


def test_two_creates_from_the_same_topic_get_distinct_projects(client):
    _activate_channel(client)
    a = client.post("/api/projects", headers=AUTH, json={"topic": "Same Topic"}).json()
    b = client.post("/api/projects", headers=AUTH, json={"topic": "Same Topic"}).json()
    assert a["id"] != b["id"]


# ---------------------------------------------------------------------------
# The pipeline actually runs (via the stubbed generator) and streams events
# ---------------------------------------------------------------------------


def test_create_starts_the_pipeline_and_streams_its_events_over_the_socket(client, monkeypatch):
    def fake_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        yield PhaseStarted(project_id=project.id, phase="brief")
        yield PhaseFinished(project_id=project.id, phase="brief", duration_ms=5)

    monkeypatch.setattr("avg.api.app.run_pipeline", fake_run)
    _activate_channel(client)

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"

        r = client.post("/api/projects", headers=AUTH, json={"topic": "How Rome Fell"})
        assert r.status_code == 201

        # The job announces itself first (Slice 8) - before this, nothing
        # reached a client until a phase emitted, which for a render meant
        # minutes of silence while the workspace was prepared.
        started = ws.receive_json()
        assert started["type"] == "job_started"
        assert started["kind"] == "pipeline"

        first = ws.receive_json()
        assert first["type"] == "phase_started"
        assert first["phase"] == "brief"

        second = ws.receive_json()
        assert second["type"] == "phase_finished"
        assert second["phase"] == "brief"


def test_a_second_create_while_one_is_running_is_409(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        started.set()
        release.wait(timeout=5)
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.app.run_pipeline", blocking_run)
    _activate_channel(client)

    try:
        r1 = client.post("/api/projects", headers=AUTH, json={"topic": "First"})
        assert r1.status_code == 201
        assert started.wait(timeout=5)

        r2 = client.post("/api/projects", headers=AUTH, json={"topic": "Second"})
        assert r2.status_code == 409
    finally:
        release.set()


def test_cancel_route_cancels_a_running_pipelines_token(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        started.set()
        release.wait(timeout=5)
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.app.run_pipeline", blocking_run)
    _activate_channel(client)

    try:
        created = client.post("/api/projects", headers=AUTH, json={"topic": "x"}).json()
        assert started.wait(timeout=5)

        client.post(f"/api/projects/{created['id']}/cancel", headers=AUTH)
        assert client.app.state.pipeline_cancel_token is not None
        assert client.app.state.pipeline_cancel_token.cancelled is True
    finally:
        release.set()


# ---------------------------------------------------------------------------
# Per-phase text routing (Slice 6B)


def test_create_hands_the_runner_a_real_router_config(client, monkeypatch):
    """The runner must receive a built `RouterConfig`, never `None`.

    This is a regression test for a real defect, not a shape assertion:
    until Slice 6B, neither this route nor the take re-run passed `router=`
    at all, so `PhaseContext.router` was `None` and every text phase died
    on `None.phases` the moment it called `generate_text`. The whole
    per-phase routing layer was unreachable from the app. Asserting the
    concrete type is what makes a future refactor that drops the argument
    fail here instead of at runtime, minutes into a user's video.
    """
    from avg.providers.text.router import RouterConfig

    seen: dict[str, object] = {}

    def capturing_run(project, *, channel, router=None, start_from=0, cancel_token=None, **kwargs):
        seen["router"] = router
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.app.run_pipeline", capturing_run)
    _activate_channel(client)

    assert client.post("/api/projects", headers=AUTH, json={"topic": "x"}).status_code == 201
    for _ in range(50):
        if "router" in seen:
            break
        threading.Event().wait(0.02)

    router = seen.get("router")
    assert isinstance(router, RouterConfig)
    assert router.default.provider == "groq"


def test_create_with_a_malformed_model_setting_is_a_400_and_starts_nothing(client, monkeypatch):
    started = threading.Event()

    def poison_run(*args, **kwargs):
        started.set()
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.app.run_pipeline", poison_run)
    _activate_channel(client)

    response = client.post(
        "/api/projects",
        headers=AUTH,
        json={"topic": "x", "overrides": {"brief_model": "not-a-pair"}},
    )
    assert response.status_code == 400
    assert "provider/model" in response.json()["detail"]
    # A bad setting must be rejected before any work begins.
    assert not started.is_set()


# ---------------------------------------------------------------------------
# Archive (Slice 6C)


def test_archive_hides_projects_and_survives_a_reload(client, paths):
    _activate_channel(client)
    first = client.post("/api/projects", headers=AUTH, json={"topic": "One"}).json()
    second = client.post("/api/projects", headers=AUTH, json={"topic": "Two"}).json()

    listed = client.get("/api/projects", headers=AUTH).json()["projects"]
    assert all(p["archived"] is False for p in listed)

    r = client.post("/api/projects/archive", headers=AUTH, json={"ids": [first["id"]], "archived": True})
    assert r.status_code == 200

    by_id = {p["id"]: p for p in client.get("/api/projects", headers=AUTH).json()["projects"]}
    assert by_id[first["id"]]["archived"] is True
    assert by_id[second["id"]]["archived"] is False

    # Persisted, not just held in memory - a fresh app over the same output
    # root must still see it archived.
    from avg.api.app import create_app

    reloaded = TestClient(
        create_app(
            token=TOKEN,
            output_root=paths["output_root"],
            thumb_cache_dir=paths["thumb_cache_dir"],
            settings_path=paths["settings_path"],
            channels_dir=paths["channels_dir"],
            secrets_path=paths["secrets_path"],
        )
    )
    again = {p["id"]: p for p in reloaded.get("/api/projects", headers=AUTH).json()["projects"]}
    assert again[first["id"]]["archived"] is True


def test_archiving_is_reversible(client):
    _activate_channel(client)
    created = client.post("/api/projects", headers=AUTH, json={"topic": "One"}).json()

    client.post("/api/projects/archive", headers=AUTH, json={"ids": [created["id"]], "archived": True})
    client.post("/api/projects/archive", headers=AUTH, json={"ids": [created["id"]], "archived": False})

    listed = {p["id"]: p for p in client.get("/api/projects", headers=AUTH).json()["projects"]}
    assert listed[created["id"]]["archived"] is False


def test_archive_applies_to_all_ids_or_none(client):
    """A bulk action must not half-apply - that is the whole reason this is
    one route rather than N per-project calls."""
    _activate_channel(client)
    created = client.post("/api/projects", headers=AUTH, json={"topic": "One"}).json()

    r = client.post(
        "/api/projects/archive",
        headers=AUTH,
        json={"ids": [created["id"], "no-such-project"], "archived": True},
    )
    assert r.status_code == 404
    assert "no-such-project" in r.json()["detail"]

    listed = {p["id"]: p for p in client.get("/api/projects", headers=AUTH).json()["projects"]}
    assert listed[created["id"]]["archived"] is False


def test_storage_bytes_sums_the_whole_library(client, paths):
    """Home's Storage card needs a library-wide figure, and it must be the
    same measurement the Studio header reports per project - a full walk of
    each project folder, not just the current take."""
    _activate_channel(client)
    created = client.post("/api/projects", headers=AUTH, json={"topic": "Sized"}).json()

    empty = client.get("/api/projects", headers=AUTH).json()
    assert empty["storage_bytes"] == 0

    (Path(created["folder"]) / "images").mkdir(parents=True, exist_ok=True)
    (Path(created["folder"]) / "images" / "a.png").write_bytes(b"x" * 2048)

    # Summed by the startup scan, so a fresh app is what re-counts it.
    from avg.api.app import create_app

    reloaded = TestClient(
        create_app(
            token=TOKEN,
            output_root=paths["output_root"],
            thumb_cache_dir=paths["thumb_cache_dir"],
            settings_path=paths["settings_path"],
            channels_dir=paths["channels_dir"],
            secrets_path=paths["secrets_path"],
        )
    )
    # Polled, not read once: the size walk deliberately runs AFTER the
    # library is released for reading, so the project list is never held up
    # by a full recursive stat. That makes the figure eventually-correct by
    # design, and a single read would be a race.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        total = reloaded.get("/api/projects", headers=AUTH).json()["storage_bytes"]
        if total >= 2048:
            break
        time.sleep(0.05)
    assert total >= 2048
