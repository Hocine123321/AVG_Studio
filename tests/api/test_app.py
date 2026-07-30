import json
import threading
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


def test_a_pipeline_run_advances_the_current_takes_records(tmp_path, monkeypatch):
    """The regression test for the frozen phase rail.

    Runs one fake phase and asserts the CURRENT TAKE - what Studio reads -
    saw it finish, not just `project.phases`. Before Slice 8 these were two
    dicts: `GET /takes` snapshotted `project.phases` into take 1 lazily and
    the runner carried on mutating the original.
    """
    import avg.api.app as app_module
    from avg.core.models import PhaseRecord

    def fake_run(project, **kwargs):
        project.phases["brief"] = PhaseRecord(name="brief", status="done", duration_ms=5)
        kwargs["on_phase_change"]()
        return iter(())

    monkeypatch.setattr(app_module, "run_pipeline", fake_run)

    client = TestClient(
        create_app(
            token="t0k",
            output_root=tmp_path / "output",
            thumb_cache_dir=tmp_path / "thumbs",
            settings_path=tmp_path / "settings.json",
            channels_dir=tmp_path / "channels",
            secrets_path=tmp_path / "secrets.dat",
            errors_path=tmp_path / "errors.json",
        )
    )
    channel = client.post("/api/channels", headers={"X-AVG-Token": "t0k"}, json={"label": "C"}).json()
    client.post(f"/api/channels/{channel['id']}/activate", headers={"X-AVG-Token": "t0k"})

    created = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    thread = client.app.state.render_thread
    if thread is not None:
        thread.join(timeout=5)

    takes = client.get(f"/api/projects/{project_id}/takes", headers={"X-AVG-Token": "t0k"}).json()
    current = next(t for t in takes["takes"] if t["id"] == takes["current_take_id"])
    assert current["phases"]["brief"]["status"] == "done"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_job_started_is_the_first_event_a_render_emits(tmp_path):
    """A render was silent until the encoder's first progress line.

    The workspace preparation happens before the child process starts and
    takes as long as the project is big, so the app looked hung for
    minutes. The job now announces itself before a single file is touched.
    """
    output_root = tmp_path / "output"
    _make_legacy_project_with_video(output_root, "proj-jobstart")

    client = TestClient(
        create_app(token="t0k", output_root=output_root, thumb_cache_dir=tmp_path / "thumbs")
    )

    with client.websocket_connect("/ws/events?token=t0k") as ws:
        assert ws.receive_json()["type"] == "hello"
        started = client.post("/api/projects/proj-jobstart/render", headers={"X-AVG-Token": "t0k"})
        assert started.status_code == 202

        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "job_started"
    assert first["kind"] == "render"
    # The workspace preparation is named rather than given a fake
    # percentage - it has no measurable denominator.
    assert second["type"] == "job_step"
    assert second["step"] == "preparing"


def test_a_broadcast_from_a_worker_thread_reaches_a_socket_on_another_loop():
    """The regression test for silently dropped job events.

    Both job workers used to schedule their broadcasts onto
    `asyncio.get_running_loop()` as captured inside the ROUTE - the loop of
    that one request, which closes when the response returns. Every event a
    job emitted afterwards raised "Event loop is closed" into a
    `suppress(Exception)` and vanished; it only looked like it worked because
    a job's first events usually won a race against the response.

    So the manager now remembers the loop each socket was accepted on. This
    drives that directly: a socket registered on one loop, a broadcast from a
    plain thread, and the payload has to arrive.
    """
    import asyncio
    import threading

    from avg.api.app import _ConnectionManager

    received: list[dict] = []
    ready = threading.Event()
    loop_holder: list[asyncio.AbstractEventLoop] = []

    class _FakeSocket:
        async def send_json(self, payload: dict) -> None:
            received.append(payload)

    manager = _ConnectionManager()

    async def _serve() -> None:
        loop_holder.append(asyncio.get_running_loop())
        manager.add(_FakeSocket())  # registers THIS loop, as the real route does
        ready.set()
        await asyncio.sleep(0.5)

    thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
    thread.start()
    assert ready.wait(timeout=5)

    # From a thread that is not the loop's - exactly a job worker's position.
    manager.broadcast_threadsafe({"type": "job_started"})
    thread.join(timeout=5)

    assert received == [{"type": "job_started"}]


def test_a_socket_whose_loop_died_is_dropped_not_retried():
    """A closed loop is a gone client. Dropping it keeps a dead socket from
    costing every later broadcast an exception."""
    import asyncio
    import threading

    from avg.api.app import _ConnectionManager

    manager = _ConnectionManager()

    class _FakeSocket:
        async def send_json(self, payload: dict) -> None:  # pragma: no cover
            raise AssertionError("must not be sent to")

    def _register() -> None:
        asyncio.run(_register_async())

    async def _register_async() -> None:
        manager.add(_FakeSocket())

    thread = threading.Thread(target=_register, daemon=True)
    thread.start()
    thread.join(timeout=5)
    # The loop that socket was registered on is now closed.

    manager.broadcast_threadsafe({"type": "job_started"})

    assert manager._sockets == {}
