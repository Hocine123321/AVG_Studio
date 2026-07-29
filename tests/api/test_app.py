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
    assert r.status_code == 200 and r.json() == {"projects": [], "loading": False, "storage_bytes": 0}

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


def _project(folder) -> "ProjectDoc":
    from avg.core.models import ProjectDoc

    return ProjectDoc(
        id="p1",
        title="P",
        folder=folder,
        created=datetime.now(timezone.utc),
    )


def test_render_request_merges_channel_settings_over_the_legacy_config(tmp_path):
    """Regression: POST /render died with a duplicate keyword argument.

    `_build_render_request` spread the channel's render kwargs after
    passing the same fields positionally from the legacy config.json, so
    any channel that had `render_engine`, `fps`, `render_codec` or
    `ken_burns` set produced

        TypeError: RenderRequest() got multiple values for keyword
        argument 'render_engine'

    and every render returned 500. No test caught it because every test
    pointed `output_root` at an empty tmp_path, where the config load
    fails and the function returns from its fallback branch - the branch
    that never had the collision.
    """
    from avg.api.app import _build_render_request

    output_root = tmp_path / "output"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"video": {"fps": 24, "render_engine": "auto"}}), encoding="utf-8"
    )

    request = _build_render_request(
        _project(output_root / "p1"),
        output_root,
        channel_settings={"render_engine": "moviepy", "fps": 30, "render_codec": "libx265"},
    )

    # The channel wins over the legacy file, which the user cannot edit.
    assert request.render_engine == "moviepy"
    assert request.fps == 30
    assert request.codec == "libx265"
