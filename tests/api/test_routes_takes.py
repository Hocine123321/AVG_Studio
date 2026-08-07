"""Tests for the takes routes (Slice 5, Task 3): `avg/api/routes_takes.py`.

Every test that would otherwise start a real pipeline monkeypatches
`avg.api.routes_takes.run_pipeline` (the name `avg.pipeline.runner.run` is
imported under, inside that module) - mirroring `tests/api/test_projects_
create.py`'s own convention for the same reason: the real `REGISTRY` phases
call actual AI providers, and this suite must never make a network call.

Projects are built directly (`ProjectDoc(...)` + a hand-seeded take 1, via
`_project`/`_seed_take1` below) and injected straight into `app.state.
projects`, rather than routed through `POST /api/projects` - that keeps
every test's fixture data exact and avoids depending on `POST /api/projects`'s
own background thread finishing before a test's assertions run. Nothing here
touches `D:\\AI_Video_Gen\\output` (the ten real reference projects) - every
path is `tmp_path`-scoped, matching `test_routes_settings.py`'s `paths`
fixture.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.api.routes_takes import _folder_size_bytes
from avg.config.channels import get_channel
from avg.core.errors import ErrorKind
from avg.core.events import PhaseFailed
from avg.core.models import PhaseRecord, ProjectDoc, Take
from avg.core.takes import PHASE_ARTIFACTS, take_folder

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


def _activate_channel(client: TestClient, label: str = "My Channel") -> str:
    created = client.post("/api/channels", headers=AUTH, json={"label": label}).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


def _project(paths, project_id: str = "proj1") -> ProjectDoc:
    folder = paths["output_root"] / project_id
    for sub in ("script", "images", "audio", "render"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return ProjectDoc(
        id=project_id,
        title="A Topic",
        folder=folder,
        created=datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc),
    )


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_take1(project: ProjectDoc) -> Take:
    """Populate take 1's own folder with a real artifact per phase, and
    register it as the project's one (current) take - the fixture every
    test needing "an existing project with real history" starts from."""
    t1 = take_folder(project, 1)
    for phase_key, rel_paths in PHASE_ARTIFACTS.items():
        for rel in rel_paths:
            target = t1 / rel
            if phase_key == "visuals":
                _write(target / "001.png", "fake-png-bytes")
            else:
                _write(target, f"content for {phase_key}")

    take = Take(
        id=1,
        label="Take 1",
        created=project.created,
        settings_snapshot={},
        phases={key: PhaseRecord(name=key, status="done", duration_ms=1000) for key in PHASE_ARTIFACTS},
        master_output=None,
    )
    project.takes = [take]
    project.current_take_id = 1
    return take


def _add_second_take(project: ProjectDoc) -> Take:
    t2 = take_folder(project, 2)
    t2.mkdir(parents=True, exist_ok=True)
    output = t2 / "master_output.mp4"
    output.write_bytes(b"fake-mp4-bytes")
    take2 = Take(id=2, label="Take 2", created=project.created, phases={}, master_output=output)
    project.takes.append(take2)
    return take2


def _instant_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
    """A fake `run_pipeline` that finishes immediately without touching
    anything - for tests only interested in the synchronous side effects a
    route already produced before starting its background thread (label,
    `clone_take`'s file copies, the 400/409/404 validation paths)."""
    return
    yield  # pragma: no cover


# ---------------------------------------------------------------------------
# GET /api/projects/{id}/takes
# ---------------------------------------------------------------------------


def test_get_takes_of_unknown_project_is_404(client):
    assert client.get("/api/projects/nope/takes", headers=AUTH).status_code == 404


def test_get_takes_requires_token(client, paths):
    project = _project(paths)
    client.app.state.projects[project.id] = project
    assert client.get(f"/api/projects/{project.id}/takes").status_code == 401


def test_get_takes_migrates_a_legacy_project_into_take_one(client, paths):
    project = _project(paths)  # no takes yet - a legacy/never-touched project
    client.app.state.projects[project.id] = project

    r = client.get(f"/api/projects/{project.id}/takes", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["current_take_id"] == 1
    assert [t["id"] for t in body["takes"]] == [1]


def test_get_takes_lists_existing_takes(client, paths):
    project = _project(paths)
    _seed_take1(project)
    _add_second_take(project)
    client.app.state.projects[project.id] = project

    r = client.get(f"/api/projects/{project.id}/takes", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert {t["id"] for t in body["takes"]} == {1, 2}
    assert body["current_take_id"] == 1


def test_get_takes_includes_disk_usage_in_bytes(client, paths):
    """Task 4: the Studio header's disk-usage figure - computed server-side
    from the WHOLE project folder (every take's own files), not walked by
    the browser."""
    project = _project(paths)
    _seed_take1(project)
    _add_second_take(project)
    client.app.state.projects[project.id] = project

    expected = sum(f.stat().st_size for f in project.folder.rglob("*") if f.is_file())
    assert expected > 0  # the fixtures above really do write real files

    r = client.get(f"/api/projects/{project.id}/takes", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["disk_bytes"] == expected


def test_folder_size_bytes_of_a_nonexistent_folder_is_zero(tmp_path):
    assert _folder_size_bytes(tmp_path / "does-not-exist") == 0


def test_folder_size_bytes_sums_nested_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_text("12345", encoding="utf-8")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "two.txt").write_text("1234567890", encoding="utf-8")
    assert _folder_size_bytes(tmp_path) == 15


def test_get_takes_disk_usage_of_an_empty_project_matches_what_migration_wrote(client, paths):
    """A brand-new (legacy, never-touched) project has no take artifacts of
    its own - this route's own `_migrate_and_persist` call is what puts
    anything on disk at all (take 1's empty folder plus the freshly written
    `project.json`), so `disk_bytes` should match exactly that, not 0 and
    not some larger stale figure."""
    project = _project(paths, project_id="empty")
    client.app.state.projects[project.id] = project

    r = client.get(f"/api/projects/{project.id}/takes", headers=AUTH)
    assert r.status_code == 200
    expected = sum(f.stat().st_size for f in project.folder.rglob("*") if f.is_file())
    assert r.json()["disk_bytes"] == expected
    assert expected > 0  # migrate_to_takes + _migrate_and_persist's save_project both just ran


# ---------------------------------------------------------------------------
# POST .../takes/preview - computes only
# ---------------------------------------------------------------------------


def test_preview_computes_without_creating_or_starting_anything(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes/preview",
        headers=AUTH,
        json={"overrides": {"voice_id": "new-voice"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rerun"] == ["voice", "director2", "screen_text", "render"]
    assert body["keeps"] == ["brief", "script", "director1", "visuals"]
    # voice/director2/screen_text have 1000ms of history each in take 1;
    # "render" has none (it's deliberately absent from `PHASE_ARTIFACTS` -
    # its one artifact, master_output.mp4, is never copied between takes -
    # see `avg.core.takes`'s own docstring), so it's named in `unknown`.
    assert body["estimate_seconds"] == 3
    assert body["unknown"] == ["render"]

    # Nothing created, nothing started.
    assert len(project.takes) == 1
    assert project.current_take_id == 1
    assert not (project.folder / "takes" / "2").exists()
    assert client.app.state.jobs.get(project.id) is None


def test_preview_of_unknown_project_is_404(client):
    r = client.post("/api/projects/nope/takes/preview", headers=AUTH, json={"reroll_from": "script"})
    assert r.status_code == 404


def test_preview_with_no_overrides_and_no_reroll_is_400(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(f"/api/projects/{project.id}/takes/preview", headers=AUTH, json={})
    assert r.status_code == 400


def test_preview_rejects_an_unknown_override_id(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes/preview",
        headers=AUTH,
        json={"overrides": {"not_a_real_setting": 1}},
    )
    assert r.status_code == 400


def test_preview_rejects_an_app_scoped_override(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes/preview", headers=AUTH, json={"overrides": {"theme": "neon"}}
    )
    assert r.status_code == 400


def test_preview_rejects_an_unknown_reroll_phase(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes/preview", headers=AUTH, json={"reroll_from": "not_a_phase"}
    )
    assert r.status_code == 400


def test_preview_combined_override_and_reroll_picks_the_earlier_phase(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    # ken_burns invalidates director2 (id 5); reroll_from="script" is id 1 -
    # the earlier of the two, so the whole pipeline from script on reruns.
    r = client.post(
        f"/api/projects/{project.id}/takes/preview",
        headers=AUTH,
        json={"overrides": {"ken_burns": False}, "reroll_from": "script"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["keeps"] == ["brief"]
    assert body["rerun"][0] == "script"
    assert body["rerun"][-1] == "render"


# ---------------------------------------------------------------------------
# POST .../takes - the real create + re-run
# ---------------------------------------------------------------------------


def test_create_requires_token(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project
    r = client.post(f"/api/projects/{project.id}/takes", json={"reroll_from": "script"})
    assert r.status_code == 401


def test_create_with_no_overrides_and_no_reroll_is_400(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(f"/api/projects/{project.id}/takes", headers=AUTH, json={})
    assert r.status_code == 400
    assert len(project.takes) == 1  # nothing cloned


def test_create_without_active_channel_is_400(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "x"}}
    )
    assert r.status_code == 400
    assert len(project.takes) == 1


def test_voice_id_override_reruns_voice_onward_and_keeps_images(client, paths, monkeypatch):
    calls = []
    started = threading.Event()
    release = threading.Event()

    def blocking_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        calls.append({"start_from": start_from, "folder": project.folder})
        started.set()
        release.wait(timeout=5)
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", blocking_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    try:
        r = client.post(
            f"/api/projects/{project.id}/takes",
            headers=AUTH,
            json={"overrides": {"voice_id": "new-voice"}},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == 2
        assert body["changed_ids"] == ["voice_id"]
        assert set(body["phases"].keys()) == {"brief", "script", "director1", "visuals"}

        take2_folder = project.folder / "takes" / "2"
        # Kept phase's artifact copied forward...
        assert (take2_folder / "images" / "001.png").exists()
        # ...but voice's own artifact (not kept) was not.
        assert not (take2_folder / "audio").exists()

        assert started.wait(timeout=5)
        assert len(calls) == 1
        assert calls[0]["start_from"] == 4  # voice's phase id
        assert calls[0]["folder"] == take2_folder
    finally:
        release.set()


def test_second_concurrent_take_is_409(client, paths, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        started.set()
        release.wait(timeout=5)
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", blocking_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    try:
        r1 = client.post(
            f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "x"}}
        )
        assert r1.status_code == 201
        assert started.wait(timeout=5)

        r2 = client.post(
            f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "y"}}
        )
        assert r2.status_code == 409
    finally:
        release.set()


def test_override_never_persists_to_the_channel_document(client, paths, monkeypatch):
    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", _instant_run)
    channel_id = _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "super-secret-voice"}}
    )
    assert r.status_code == 201

    on_disk = get_channel(channel_id, paths["channels_dir"])
    assert "voice_id" not in on_disk.settings


def test_reroll_dominant_over_a_later_override(client, paths, monkeypatch):
    """reroll_from is at/earlier than the override's phase -> the stored
    `rerolled_from` records the reroll, and the label reads "re-roll <name>"."""
    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", _instant_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    # ken_burns invalidates director2 (id 5); reroll_from="script" is id 1.
    r = client.post(
        f"/api/projects/{project.id}/takes",
        headers=AUTH,
        json={"overrides": {"ken_burns": False}, "reroll_from": "script"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["rerolled_from"] == "script"
    assert body["label"] == "Take 2 · re-roll script"


def test_override_dominant_over_an_earlier_reroll_target(client, paths, monkeypatch):
    """The override's phase is earlier than reroll_from's -> the settings
    change is what actually happened; `rerolled_from` stays None even
    though a reroll_from was requested (the asymmetry the brief calls out)."""
    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", _instant_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    # content_shape invalidates brief (id 0); reroll_from="render" is id 7.
    r = client.post(
        f"/api/projects/{project.id}/takes",
        headers=AUTH,
        json={"overrides": {"content_shape": "explainer"}, "reroll_from": "render"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["rerolled_from"] is None
    assert body["label"] == "Take 2 · content shape"


def test_auto_label_names_earliest_setting_and_counts_the_rest(client, paths, monkeypatch):
    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", _instant_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    # visual_tone -> director1 (id 2), voice_id -> voice (id 4),
    # render_codec -> render (id 7). Earliest is visual_tone.
    r = client.post(
        f"/api/projects/{project.id}/takes",
        headers=AUTH,
        json={"overrides": {"visual_tone": "vibrant", "voice_id": "x", "render_codec": "libx265"}},
    )
    assert r.status_code == 201
    assert r.json()["label"] == "Take 2 · visual tone +2 more"


def test_failed_take_does_not_become_current(client, paths, monkeypatch):
    release = threading.Event()

    def failing_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        release.wait(timeout=5)
        yield PhaseFailed(project_id=project.id, phase="voice", kind=ErrorKind.bug, message="boom")

    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", failing_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "x"}}
    )
    assert r.status_code == 201
    new_take_id = r.json()["id"]

    # The background thread is parked on `release` right now, so this
    # reference is guaranteed to still be the live thread - no race.
    job = client.app.state.jobs.get(project.id)
    thread = job.thread if job is not None else None
    release.set()
    if thread is not None:
        thread.join(timeout=5)

    assert project.current_take_id == 1  # unchanged - the new take never became current
    assert new_take_id != 1
    assert client.app.state.jobs.get(project.id) is None


def test_successful_take_becomes_current_and_updates_master_output(client, paths, monkeypatch):
    release = threading.Event()

    def succeeding_run(project, *, channel, start_from=0, cancel_token=None, **kwargs):
        release.wait(timeout=5)
        # Simulate the render phase producing a finished video, the same
        # way `avg.pipeline.phases.render.RenderPhase.run` writes it: onto
        # the shadow project (this take's own folder), not the original.
        output = Path(project.folder) / "master_output.mp4"
        output.write_bytes(b"fake-mp4")
        project.master_output = output
        return
        yield  # pragma: no cover

    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", succeeding_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(
        f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "x"}}
    )
    assert r.status_code == 201
    new_take_id = r.json()["id"]

    job = client.app.state.jobs.get(project.id)
    thread = job.thread if job is not None else None
    release.set()
    if thread is not None:
        thread.join(timeout=5)

    assert project.current_take_id == new_take_id
    assert project.master_output == project.folder / "takes" / str(new_take_id) / "master_output.mp4"


# ---------------------------------------------------------------------------
# DELETE .../takes/{id}
# ---------------------------------------------------------------------------


def test_delete_take_removes_it(client, paths):
    project = _project(paths)
    _seed_take1(project)
    _add_second_take(project)
    client.app.state.projects[project.id] = project

    r = client.delete(f"/api/projects/{project.id}/takes/2", headers=AUTH)
    assert r.status_code == 200
    assert [t.id for t in project.takes] == [1]


def test_delete_unknown_take_is_404(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.delete(f"/api/projects/{project.id}/takes/99", headers=AUTH)
    assert r.status_code == 404


def test_delete_last_remaining_take_is_400(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.delete(f"/api/projects/{project.id}/takes/1", headers=AUTH)
    assert r.status_code == 400
    assert len(project.takes) == 1


def test_delete_of_unknown_project_is_404(client):
    assert client.delete("/api/projects/nope/takes/1", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# POST .../takes/{id}/select
# ---------------------------------------------------------------------------


def test_select_take_pins_it_as_current_and_syncs_master_output(client, paths):
    project = _project(paths)
    _seed_take1(project)
    take2 = _add_second_take(project)
    client.app.state.projects[project.id] = project

    r = client.post(f"/api/projects/{project.id}/takes/2/select", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["current_take_id"] == 2
    assert project.current_take_id == 2
    assert project.master_output == take2.master_output


def test_select_unknown_take_is_404(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.post(f"/api/projects/{project.id}/takes/99/select", headers=AUTH)
    assert r.status_code == 404


def test_select_of_unknown_project_is_404(client):
    assert client.post("/api/projects/nope/takes/1/select", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# PATCH .../takes/{id} - rename only
# ---------------------------------------------------------------------------


def test_rename_take_sets_the_label(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.patch(f"/api/projects/{project.id}/takes/1", headers=AUTH, json={"label": "My Favorite Cut"})
    assert r.status_code == 200
    assert r.json()["label"] == "My Favorite Cut"
    assert project.takes[0].label == "My Favorite Cut"


def test_rename_take_rejects_an_empty_label(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.patch(f"/api/projects/{project.id}/takes/1", headers=AUTH, json={"label": "   "})
    assert r.status_code == 400
    assert project.takes[0].label == "Take 1"


def test_rename_unknown_take_is_404(client, paths):
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    r = client.patch(f"/api/projects/{project.id}/takes/99", headers=AUTH, json={"label": "x"})
    assert r.status_code == 404


def test_renamed_take_survives_a_later_take_creation(client, paths, monkeypatch):
    """A user rename is never clobbered by `_auto_label` running for some
    later, unrelated take - nothing in this module recomputes a label after
    creation."""
    monkeypatch.setattr("avg.api.routes_takes.run_pipeline", _instant_run)
    _activate_channel(client)
    project = _project(paths)
    _seed_take1(project)
    client.app.state.projects[project.id] = project

    client.patch(f"/api/projects/{project.id}/takes/1", headers=AUTH, json={"label": "My Favorite Cut"})

    r = client.post(
        f"/api/projects/{project.id}/takes", headers=AUTH, json={"overrides": {"voice_id": "x"}}
    )
    assert r.status_code == 201

    assert project.takes[0].label == "My Favorite Cut"
