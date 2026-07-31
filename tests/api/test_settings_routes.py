import json

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.config.settings import load_settings


@pytest.fixture
def client(tmp_path):
    settings_path = tmp_path / "settings.json"
    app = create_app(
        token="t0k",
        output_root=tmp_path / "output",
        thumb_cache_dir=tmp_path / "thumbs",
        settings_path=settings_path,
    )
    return TestClient(app), settings_path


def test_get_settings_reports_not_onboarded_when_absent(client):
    c, _ = client
    r = c.get("/api/settings", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"] is False
    assert body["output_root"] is None
    assert "suggestion" in body
    assert "path" in body["suggestion"] and "project_count" in body["suggestion"]


def test_get_settings_requires_token(client):
    c, _ = client
    assert c.get("/api/settings").status_code == 401


def test_post_settings_saves_atomically_and_marks_onboarded(client, tmp_path):
    c, settings_path = client
    new_root = tmp_path / "my_library"
    r = c.post("/api/settings", headers={"X-AVG-Token": "t0k"}, json={"output_root": str(new_root)})
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"] is True
    assert body["output_root"] == str(new_root)

    # Written to disk, atomically, at the path this app instance was given.
    on_disk = load_settings(settings_path)
    assert on_disk.onboarded is True
    assert on_disk.output_root == str(new_root)

    # The chosen folder is created (suggestion #2's default is "created on
    # accept", and this exercises the same code path for any chosen path).
    assert new_root.is_dir()


def test_post_settings_adopts_new_root_without_restart(client, tmp_path):
    c, _ = client
    new_root = tmp_path / "library_two"
    new_root.mkdir()
    (new_root / "proj_one").mkdir()
    (new_root / "proj_one" / "project.json").write_text(
        json.dumps({"meta": {"topic": "Hi", "form": "shortform", "phase": "complete", "created": "2026-01-01T00:00:00Z"}}),
        encoding="utf-8",
    )

    c.post("/api/settings", headers={"X-AVG-Token": "t0k"}, json={"output_root": str(new_root)})

    r = c.get("/api/projects", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["projects"]]
    assert "proj_one" in ids


def test_scan_route_counts_projects_in_an_arbitrary_folder(tmp_path, client):
    c, _ = client
    empty = tmp_path / "empty_dir"
    r = c.get("/api/settings/scan", headers={"X-AVG-Token": "t0k"}, params={"path": str(empty)})
    assert r.status_code == 200
    assert r.json() == {"project_count": 0}


def test_scan_route_counts_real_legacy_projects(client):
    # Exercises the route against the real legacy library rather than a
    # synthetic fixture (this project's dev machine has one, read-only - see
    # tests/media/test_thumbnails.py's docstring for the same convention).
    # Asserts only "found something", not an exact count: the folder's
    # contents are real user data outside this test suite's control, and a
    # hardcoded count would make the suite brittle against that changing.
    # The exact count ("10 projects") is verified manually - see the task
    # report - not pinned here.
    c, _ = client
    r = c.get(
        "/api/settings/scan",
        headers={"X-AVG-Token": "t0k"},
        params={"path": r"D:\AI_Video_Gen\output"},
    )
    assert r.status_code == 200
    assert r.json()["project_count"] > 0


# ── Slice 12 Task 3: a channel reads its own document, or the app's ────────

AUTH = {"X-AVG-Token": "t0k"}


@pytest.fixture
def client_full(tmp_path):
    """A richer fixture than `client` above: a `channels_dir` so channels
    can actually be created, and `channel_defaults_path` so the either/or
    resolution has its second document to read from."""
    app = create_app(
        token="t0k",
        output_root=tmp_path / "output",
        thumb_cache_dir=tmp_path / "thumbs",
        settings_path=tmp_path / "settings.json",
        channels_dir=tmp_path / "channels",
        channel_defaults_path=tmp_path / "channel_defaults.json",
    )
    return TestClient(app)


def _make_channel(client, label="Docs"):
    created = client.post("/api/channels", json={"label": label}, headers=AUTH).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


def test_a_channel_reads_its_own_document_by_default(client_full):
    _make_channel(client_full)
    client_full.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    body = client_full.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "30s"
    assert body["channel"]["settings_source"] == "channel"


def test_switching_to_global_reads_the_defaults_document(client_full):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client_full)
    client_full.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({"length_preset": "90s"}, client_full.app.state.channel_defaults_path)

    client_full.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)

    body = client_full.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "90s"
    # The switch itself is ALWAYS read from the channel's own document -
    # otherwise a channel on "global" could never be switched back.
    assert body["channel"]["settings_source"] == "global"


def test_switching_back_returns_the_channels_own_value_unchanged(client_full):
    """The round trip must not write through. A channel's own value has to
    survive a visit to the global document untouched."""
    from avg.config.channel_defaults import save_defaults

    _make_channel(client_full)
    client_full.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({"length_preset": "90s"}, client_full.app.state.channel_defaults_path)

    client_full.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)
    client_full.post("/api/settings/values", json={"settings_source": "channel"}, headers=AUTH)

    assert client_full.get("/api/settings/values", headers=AUTH).json()["channel"]["length_preset"] == "30s"


def test_a_field_absent_from_the_selected_document_never_falls_back(client_full):
    """The one assertion that guards CLAUDE.md's deleted mechanism. On
    `global`, a field the defaults document does not hold resolves to the
    SettingSpec default - NOT to the channel's own value."""
    from avg.config.channel_defaults import save_defaults
    from avg.core.settings_schema import BY_ID

    _make_channel(client_full)
    client_full.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({}, client_full.app.state.channel_defaults_path)
    client_full.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)

    resolved = client_full.get("/api/settings/values", headers=AUTH).json()["channel"]["length_preset"]
    assert resolved == BY_ID["length_preset"].default
    assert resolved != "30s"


def test_porting_copies_channel_values_and_no_secret(client_full):
    from avg.config.channel_defaults import load_defaults

    _make_channel(client_full)
    client_full.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    client_full.post("/api/keys/groq", json={"key": "sk-should-never-be-copied"}, headers=AUTH)

    res = client_full.post("/api/settings/port-to-global", headers=AUTH)
    assert res.status_code == 200

    stored = load_defaults(client_full.app.state.channel_defaults_path)
    assert stored["length_preset"] == "30s"
    assert "groq" not in stored
    assert "sk-should-never-be-copied" not in str(stored)
    # App-scope settings belong to the app, not to a channel snapshot.
    assert "theme" not in stored


def test_porting_without_an_active_channel_is_refused(client_full):
    assert client_full.post("/api/settings/port-to-global", headers=AUTH).status_code == 400
