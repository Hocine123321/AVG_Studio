import json
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from avg.api.app import create_app

@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(token="t0k", output_root=tmp_path, thumb_cache_dir=tmp_path / "thumbs"))

def test_requests_without_a_token_are_rejected(client):
    assert client.get("/api/projects").status_code == 401

def test_requests_with_the_token_succeed(client):
    r = client.get("/api/projects", headers={"X-AVG-Token": "t0k"})
    # `loading` reports whether the startup library scan has finished. The
    # route waits for that scan, so by the time it answers it is always
    # False - the flag exists for the UI, which polls before this route can
    # be sure, not to describe this response.
    assert r.status_code == 200 and r.json() == {"projects": [], "loading": False}

def test_render_of_an_unknown_project_is_404(client):
    r = client.post("/api/projects/nope/render", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 404

def test_websocket_streams_events(client):
    with client.websocket_connect("/ws/events?token=t0k") as ws:
        assert ws.receive_json()["type"] == "hello"

def test_thumbnail_of_an_unknown_project_is_404(client):
    r = client.get("/api/projects/nope/thumbnail", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 404

def test_thumbnail_requires_the_token(client):
    assert client.get("/api/projects/nope/thumbnail").status_code == 401

def test_video_of_an_unknown_project_is_404(client):
    r = client.get("/api/projects/nope/video", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 404


def _make_legacy_project_with_video(output_root, project_id: str):
    """Writes a minimal legacy project folder `import_all` will pick up,
    with a real tiny mp4 at its root - just enough for the video route's
    Range/206 behaviour to be exercised against an actual media file rather
    than an arbitrary binary blob."""
    folder = output_root / project_id
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(
        json.dumps({
            "meta": {
                "topic": "Range test", "form": "shortform", "phase": "complete",
                "created": datetime.now(timezone.utc).isoformat(),
            },
        }),
        encoding="utf-8",
    )
    video_path = folder / "master_output.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1", str(video_path)],
        capture_output=True, check=True,
    )
    return video_path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_video_route_honours_range_and_returns_206(tmp_path):
    output_root = tmp_path / "output"
    video_path = _make_legacy_project_with_video(output_root, "proj-range")
    file_size = video_path.stat().st_size

    app_client = TestClient(create_app(token="t0k", output_root=output_root, thumb_cache_dir=tmp_path / "thumbs"))
    r = app_client.get(
        "/api/projects/proj-range/video",
        headers={"X-AVG-Token": "t0k", "Range": "bytes=0-99"},
    )

    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{file_size}"
    assert r.headers["content-length"] == "100"
    assert len(r.content) == 100

    whole = video_path.read_bytes()
    assert r.content == whole[:100]
