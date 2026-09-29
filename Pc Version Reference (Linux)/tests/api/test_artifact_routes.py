"""Tests for the take-scoped artifact routes.

These serve files from a user's disk, so the test that matters most is the
one proving a caller cannot ask for a file the table does not name.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.core.models import PhaseRecord, ProjectDoc, Take
from avg.pipeline.create import save_project

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def client(tmp_path):
    output_root = tmp_path / "output"
    folder = output_root / "proj-artifacts"
    (folder / "script").mkdir(parents=True)
    (folder / "audio").mkdir(parents=True)
    (folder / "images").mkdir(parents=True)
    (folder / "script" / "brief.json").write_text(json.dumps({"angle": "x"}), encoding="utf-8")
    (folder / "audio" / "raw_voice.mp3").write_bytes(b"0123456789" * 50)
    (folder / "images" / "scene_1.png").write_bytes(b"png-bytes")

    now = datetime.now(timezone.utc)
    project = ProjectDoc(
        id="proj-artifacts",
        title="P",
        folder=folder,
        created=now,
        phases={"brief": PhaseRecord(name="brief", status="done")},
        takes=[Take(id=1, label="Take 1", created=now, phases={}, storage="root")],
        current_take_id=1,
    )
    save_project(project)

    return TestClient(
        create_app(
            token=TOKEN,
            output_root=output_root,
            thumb_cache_dir=tmp_path / "thumbs",
            settings_path=tmp_path / "settings.json",
            channels_dir=tmp_path / "channels",
            secrets_path=tmp_path / "secrets.dat",
            errors_path=tmp_path / "errors.json",
        )
    )


def test_artifacts_listing_reports_what_exists(client):
    body = client.get("/api/projects/proj-artifacts/takes/1/artifacts", headers=AUTH).json()
    assert body["files"]["brief"] > 0
    assert body["files"]["images"] == 1
    assert "scenes" not in body["files"]


def test_a_known_artifact_is_served(client):
    res = client.get("/api/projects/proj-artifacts/takes/1/artifact/brief", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["angle"] == "x"


def test_an_unknown_key_is_404(client):
    assert client.get("/api/projects/proj-artifacts/takes/1/artifact/nope", headers=AUTH).status_code == 404


def test_a_path_cannot_be_smuggled_in_as_a_key(client):
    """The key space is a table, so there is nothing to traverse."""
    for attempt in ("script%2Fbrief.json", "..%2F..%2Fproject.json", "%2E%2E%2Fproject.json"):
        res = client.get(f"/api/projects/proj-artifacts/takes/1/artifact/{attempt}", headers=AUTH)
        assert res.status_code == 404, attempt


def test_audio_honours_range(client):
    res = client.get(
        "/api/projects/proj-artifacts/takes/1/artifact/raw_voice",
        headers={**AUTH, "Range": "bytes=0-9"},
    )
    assert res.status_code == 206
    assert res.headers["content-range"].startswith("bytes 0-9/")
    assert len(res.content) == 10


def test_a_directory_artifact_lists_and_serves_its_files(client):
    listing = client.get(
        "/api/projects/proj-artifacts/takes/1/artifact-list/images", headers=AUTH
    ).json()
    assert listing["files"] == ["scene_1.png"]

    res = client.get("/api/projects/proj-artifacts/takes/1/artifact/images/scene_1.png", headers=AUTH)
    assert res.status_code == 200
    assert res.content == b"png-bytes"


def test_a_file_not_in_the_listing_is_404(client):
    """`name` is checked against the directory's own listing, not
    sanitised - a name that is not there does not exist."""
    res = client.get(
        "/api/projects/proj-artifacts/takes/1/artifact/images/../../project.json", headers=AUTH
    )
    assert res.status_code == 404


def test_an_unknown_take_is_404(client):
    assert client.get("/api/projects/proj-artifacts/takes/9/artifacts", headers=AUTH).json()["files"] == {}
    assert (
        client.get("/api/projects/proj-artifacts/takes/9/artifact/brief", headers=AUTH).status_code == 404
    )


def test_artifacts_require_a_token(client):
    assert client.get("/api/projects/proj-artifacts/takes/1/artifacts").status_code == 401
    assert client.get("/api/projects/proj-artifacts/takes/1/artifact/brief").status_code == 401


def test_the_log_route_serves_the_tail(client):
    """A verbose log of a long run is megabytes, and the question is nearly
    always what happened at the end."""
    from avg.core.take_log import TakeLog

    # The library scan is asynchronous, so the log is written straight to the
    # take's own folder - which for a root-storage take is the project folder.
    folder = Path(client.app.state.output_root) / "proj-artifacts"
    log = TakeLog(folder / "take.log")
    for i in range(10):
        log.info(f"line {i}")
    log.close()

    body = client.get(
        "/api/projects/proj-artifacts/takes/1/log?tail=3", headers=AUTH
    ).json()

    assert len(body["lines"]) == 3
    assert "line 9" in body["lines"][-1]
    assert body["truncated"] is True
    assert body["total"] == 10


def test_the_log_route_404s_for_a_take_with_no_log(client):
    """Nothing has run in it, or it predates the log. Either way there is
    honestly nothing to show."""
    assert (
        client.get("/api/projects/proj-artifacts/takes/1/log", headers=AUTH).status_code
        == 404
    )


def test_the_log_route_requires_a_token(client):
    assert client.get("/api/projects/proj-artifacts/takes/1/log").status_code == 401
