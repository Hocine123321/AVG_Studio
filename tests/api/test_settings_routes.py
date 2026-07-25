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
