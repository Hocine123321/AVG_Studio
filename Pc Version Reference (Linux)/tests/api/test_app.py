import json
import threading
from pathlib import Path
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

def test_channel_defaults_path_resolves_when_the_override_is_omitted(client):
    """Slice 12 review, Important 5: `channel_defaults_path` used to be the
    one path `create_app` left as bare `None` when a caller (a test, most
    dangerously) did not pass an override - every other document
    (`settings_path` aside, which the config layer itself defaults) gets
    resolved here. A `None` slot is useless to any future reader and, worse,
    is exactly what `avg.config.channel_defaults.load_defaults`/`save_defaults`
    would have silently defaulted to the REAL `%APPDATA%` for. This only
    checks the resolved path, never touches the filesystem, so it stays
    honest under the no-real-profile-writes rule."""
    from avg.config.channel_defaults import default_channel_defaults_path

    assert client.app.state.channel_defaults_path == default_channel_defaults_path()

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

    job = client.app.state.jobs.get(project_id)
    if job is not None:
        job.thread.join(timeout=5)

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


def test_a_phase_failure_is_recorded_in_the_error_store(tmp_path, monkeypatch):
    """It was not.

    Two call sites fed the error store - the HTTP middleware and the UI's own
    POST - so a phase that failed mid-run left nothing in the panel the user
    opens when something breaks. A real example sat unlogged on disk for days:
    `brief failed 'NoneType' object has no attribute 'phases'`.
    """
    import avg.api.app as app_module
    from avg.core.events import PhaseFailed

    def failing_run(project, **kwargs):
        yield PhaseFailed(
            project_id=project.id, phase="brief", kind="bug", message="boom", detail="a traceback"
        )

    monkeypatch.setattr(app_module, "run_pipeline", failing_run)

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
    assert created.status_code == 201
    job = client.app.state.jobs.get(created.json()["id"])
    if job is not None:
        job.thread.join(timeout=10)

    errors = client.get("/api/errors", headers={"X-AVG-Token": "t0k"}).json()["errors"]
    entry = next(e for e in errors if "boom" in e["summary"])
    assert entry["context"]["project_id"] == created.json()["id"]
    assert entry["context"]["phase"] == "brief"
    assert entry["detail"] == "a traceback"
    # `ErrorKind` is a str-Enum, so `str()` on it yields "ErrorKind.bug" and
    # `classify` matches none of its names - which filed every failure as
    # `low`, next to the recovered degradations it is meant to outrank.
    assert entry["severity"] == "critical"


def _pipeline_client(tmp_path, monkeypatch, run):
    """A client with an active channel and a stubbed pipeline."""
    import avg.api.app as app_module

    monkeypatch.setattr(app_module, "run_pipeline", run)
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
    return client


def _join_job(client, project_id):
    job = client.app.state.jobs.get(project_id)
    if job is not None:
        job.thread.join(timeout=10)


def _wait_until_gone(client, project_id, timeout=10.0):
    """Poll until a job has been removed from the registry.

    A queued job's `Job.thread` has never been started - only the promoter
    (`_start_next_queued`) calls `.start()` on it, from another thread,
    whenever it likes. Calling `.join()` on it before that happens raises
    `RuntimeError: cannot join thread before it is started`, so a job that
    might still be queued has to be waited on by polling the registry
    instead of joining its thread directly.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.app.state.jobs.get(project_id) is None:
            return
        time.sleep(0.01)


def test_resuming_does_not_create_a_take(tmp_path, monkeypatch):
    """It created one per press.

    A project on disk carries takes 2 through 6, each holding the same four
    script files and nothing else - the same Continue button pressed five
    times. A take is for re-running something that FINISHED; a resume is the
    same attempt, continuing.
    """
    client = _pipeline_client(tmp_path, monkeypatch, lambda project, **kwargs: iter(()))
    project_id = client.post(
        "/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"}
    ).json()["id"]
    _join_job(client, project_id)

    before = client.get(f"/api/projects/{project_id}/takes", headers={"X-AVG-Token": "t0k"}).json()
    res = client.post(
        f"/api/projects/{project_id}/resume",
        headers={"X-AVG-Token": "t0k"},
        json={"from_phase": "visuals"},
    )
    assert res.status_code == 202
    _join_job(client, project_id)

    after = client.get(f"/api/projects/{project_id}/takes", headers={"X-AVG-Token": "t0k"}).json()
    assert [t["id"] for t in after["takes"]] == [t["id"] for t in before["takes"]]
    assert res.json()["take_id"] == after["current_take_id"]


def test_resume_starts_from_the_phase_it_was_given(tmp_path, monkeypatch):
    """`from_phase` is a REGISTRY key; the runner takes an id. A resume that
    quietly restarted at zero would redo - and re-pay for - the whole video."""
    seen: list[int] = []

    def run(project, **kwargs):
        seen.append(kwargs["start_from"])
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, run)
    project_id = client.post(
        "/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"}
    ).json()["id"]
    _join_job(client, project_id)

    client.post(
        f"/api/projects/{project_id}/resume",
        headers={"X-AVG-Token": "t0k"},
        json={"from_phase": "screen_text"},
    )
    _join_job(client, project_id)

    assert seen == [0, 6]  # the create started at Brief, the resume at Screen Text


def test_resume_rejects_a_phase_that_is_not_in_the_registry(tmp_path, monkeypatch):
    client = _pipeline_client(tmp_path, monkeypatch, lambda project, **kwargs: iter(()))
    project_id = client.post(
        "/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"}
    ).json()["id"]
    _join_job(client, project_id)

    res = client.post(
        f"/api/projects/{project_id}/resume",
        headers={"X-AVG-Token": "t0k"},
        json={"from_phase": "nonsense"},
    )
    assert res.status_code == 400
    assert "nonsense" in res.json()["detail"]


def test_resume_409s_while_a_job_runs(tmp_path, monkeypatch):
    """The same project running twice is still refused - `app.state.jobs`
    tracks it by project id now, but two runs of ONE project would still
    fight over the same folder and the same project.json, and this route
    must not be the exception that quietly allows it."""
    release = threading.Event()

    def slow_run(project, **kwargs):
        release.wait(timeout=10)
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, slow_run)
    project_id = client.post(
        "/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"}
    ).json()["id"]

    res = client.post(
        f"/api/projects/{project_id}/resume",
        headers={"X-AVG-Token": "t0k"},
        json={"from_phase": "visuals"},
    )
    assert res.status_code == 409

    release.set()
    _join_job(client, project_id)


def test_resume_404s_for_an_unknown_project(tmp_path, monkeypatch):
    client = _pipeline_client(tmp_path, monkeypatch, lambda project, **kwargs: iter(()))
    res = client.post(
        "/api/projects/nope/resume", headers={"X-AVG-Token": "t0k"}, json={"from_phase": "visuals"}
    )
    assert res.status_code == 404


def test_resume_requires_a_token(tmp_path, monkeypatch):
    client = _pipeline_client(tmp_path, monkeypatch, lambda project, **kwargs: iter(()))
    assert client.post("/api/projects/x/resume", json={"from_phase": "visuals"}).status_code == 401


# ---------------------------------------------------------------------------
# Slice 16: several jobs at once, capped, with the same project still refused
# ---------------------------------------------------------------------------


def test_the_same_project_twice_is_still_a_conflict(tmp_path, monkeypatch):
    """Unchanged at any cap: two runs of one project would fight over the
    same folder and the same project.json - that stays a 409 regardless of
    `max_parallel_jobs`. This 409 has nothing to do with capacity; it is the
    identity guard `app.state.jobs.is_running(project_id)` enforces on its
    own, and it is checked from a second route (`/render`) here to show it
    is not merely the resume-vs-resume case `test_resume_409s_while_a_job_
    runs` already covers."""
    started = threading.Event()
    release = threading.Event()

    def slow_run(project, **kwargs):
        started.set()
        release.wait(timeout=5)
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, slow_run)
    try:
        created = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"})
        assert created.status_code == 201
        project_id = created.json()["id"]
        assert started.wait(timeout=5)

        again = client.post(f"/api/projects/{project_id}/render", headers={"X-AVG-Token": "t0k"})
        assert again.status_code == 409
        assert project_id in again.json()["detail"]
    finally:
        release.set()
        _join_job(client, project_id)


def test_a_cap_of_one_never_runs_two_jobs_at_once(tmp_path, monkeypatch):
    """What `max_parallel_jobs=1` actually preserves from the old single-slot
    behaviour is that two jobs never run AT THE SAME TIME - not the literal
    409 status code. A second, DIFFERENT project submitted while the first
    is still running is accepted (201) and QUEUED rather than refused, then
    starts only once the first job's slot frees. See the report for why this
    reads "including the 409" in the brief and does not literally do that -
    a queued job surviving instead of a lost button press is the intended
    trade, made explicit and resolved before this test was written."""
    running: list[str] = []
    max_concurrent: list[int] = []
    release_first = threading.Event()
    first_started = threading.Event()

    def run(project, **kwargs):
        running.append(project.id)
        max_concurrent.append(len(running))
        if len(running) == 1:
            first_started.set()
            release_first.wait(timeout=5)
        try:
            return iter(())
        finally:
            running.remove(project.id)

    client = _pipeline_client(tmp_path, monkeypatch, run)
    r = client.post(
        "/api/settings/values", headers={"X-AVG-Token": "t0k"}, json={"values": {"max_parallel_jobs": "1"}}
    )
    assert r.status_code == 200

    try:
        first = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "First"})
        assert first.status_code == 201
        assert first_started.wait(timeout=5)

        second = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "Second"})
        # Accepted, not refused - it is queued, not started.
        assert second.status_code == 201
        second_id = second.json()["id"]
        job = client.app.state.jobs.get(second_id)
        assert job is not None
        assert job.queued is True

        release_first.set()
        _join_job(client, first.json()["id"])
        _join_job(client, second_id)
    finally:
        release_first.set()

    # The two jobs were never both "running" at once, whatever order the
    # scheduler happened to interleave them in.
    assert max(max_concurrent) == 1


def test_a_job_beyond_the_cap_is_queued_not_refused(tmp_path, monkeypatch):
    """The 409 now means only "this project is already running". A third
    project at a cap of 2 waits its turn instead of being turned away."""
    release = threading.Event()
    started = threading.Event()
    start_count = {"n": 0}

    def run(project, **kwargs):
        start_count["n"] += 1
        started.set()
        release.wait(timeout=5)
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, run)
    r = client.post(
        "/api/settings/values", headers={"X-AVG-Token": "t0k"}, json={"values": {"max_parallel_jobs": "2"}}
    )
    assert r.status_code == 200

    try:
        a = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "A"})
        b = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "B"})
        assert a.status_code == 201 and b.status_code == 201
        assert client.app.state.jobs.count_running() == 2

        c = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "C"})
        assert c.status_code == 201
        c_id = c.json()["id"]
        job = client.app.state.jobs.get(c_id)
        assert job is not None
        assert job.queued is True

        release.set()
        _join_job(client, a.json()["id"])
        _join_job(client, b.json()["id"])
        # Not `_join_job` for C: at the instant A and B's OWN joins return,
        # one of their `finally` blocks has already run `_start_next_queued`
        # (it runs synchronously, on that same worker thread, before the
        # thread function returns) - but which of the two promotes C, and
        # how soon after this test's own thread resumes from `join()` that
        # promotion's `thread.start()` call actually happens, is scheduling,
        # not guaranteed by anything this test controls. `_join_job` requires
        # the thread to have been started already or raises `RuntimeError:
        # cannot join thread before it is started` - exactly the hazard
        # `_wait_until_gone` exists for.
        _wait_until_gone(client, c_id)
    finally:
        release.set()

    # The third job was promoted once a slot freed, without a second request.
    assert client.app.state.jobs.get(c_id) is None


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_failed_job_frees_its_slot(tmp_path, monkeypatch):
    """A leaked registry entry would permanently consume a slot - the worst
    failure this refactor could introduce.

    Both jobs started here are made to fail on purpose, and the worker's
    `except BaseException: ... raise` (deliberate - it observes, it does not
    swallow) means each one's exception really does cross the thread
    boundary and reach pytest's thread-exception hook. That is expected
    noise from this test's own premise, not a leak to fix in the worker -
    the filter above says so, and both jobs are joined below so that noise
    is attributed to this test's own window rather than surfacing while an
    unrelated, later test happens to be running.
    """

    def failing_run(project, **kwargs):
        raise RuntimeError("boom")

    client = _pipeline_client(tmp_path, monkeypatch, failing_run)
    r = client.post(
        "/api/settings/values", headers={"X-AVG-Token": "t0k"}, json={"values": {"max_parallel_jobs": "1"}}
    )
    assert r.status_code == 200

    created = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    _join_job(client, project_id)

    # The failed job's slot was freed, not leaked - a second, different
    # project starts cleanly with no manual cleanup.
    assert client.app.state.jobs.get(project_id) is None
    again = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T2"})
    assert again.status_code == 201
    # Joined too, for the same reason as the first job: a started-but-not-
    # joined job here would still be running (and still fail) after this
    # test function returns, and its exception would then surface inside
    # whichever later test happened to be executing when the background
    # thread actually finished.
    _join_job(client, again.json()["id"])


def test_the_jobs_route_shows_running_and_queued_work(client):
    """A queued job emits no events of its own, so without this route the
    screen cannot show it at all - and 'queued' is an ordinary state now that
    work over the cap waits instead of being refused."""
    from avg.core.jobs import Job

    class _Thread:
        def is_alive(self) -> bool:
            return True

    client.app.state.jobs.add(Job(project_id="a", kind="pipeline", thread=_Thread(), cancel_token=None))
    client.app.state.jobs.add(Job(project_id="b", kind="pipeline", thread=None, cancel_token=None, queued=True))

    r = client.get("/api/jobs", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200
    rows = {row["project_id"]: row for row in r.json()["jobs"]}
    assert rows["a"]["queued"] is False and rows["a"]["position"] is None
    assert rows["b"]["queued"] is True and rows["b"]["position"] == 1


def test_the_jobs_route_needs_the_token(client):
    assert client.get("/api/jobs").status_code == 401


def test_every_render_route_hands_the_phase_the_app_s_lease():
    """The wiring test whose absence let a whole render path go unserialised.

    `tests/pipeline/test_phase_render.py` proves the lease works, but it builds
    its own local lock and assigns `ctx.render_lease` by hand - so it passed
    happily while `routes_takes` forwarded no lease at all and take runs, the
    most common way this app renders, span their ffmpeg child with nothing
    holding them back.

    This asserts the connection instead of the mechanism: every module that
    starts a pipeline must pass the app's OWN lease object down to the phase.
    """
    import inspect

    from avg.api import app as app_mod
    from avg.api import routes_takes

    for module in (app_mod, routes_takes):
        source = inspect.getsource(module)
        assert "render_lease=" in source, f"{module.__name__} never passes a render lease"
        assert "render_lease: Any = None" in source, (
            f"{module.__name__}'s pipeline wrapper does not accept a render lease, "
            "so anything it starts renders unserialised"
        )


def test_the_take_wrapper_forwards_the_lease_it_is_given():
    """`routes_takes` has its own `_default_run_pipeline` wrapper, separate
    from `app.py`'s. It accepted no lease and forwarded none, so every take
    run reached `RenderPhase` with `ctx.render_lease is None` and took the
    "nothing to wait for" branch."""
    import threading

    from avg.api import routes_takes
    from avg.core.models import ProjectDoc

    lease = threading.Lock()
    captured: dict[str, object] = {}

    def fake_run(project, **kwargs):
        captured.update(kwargs)
        return iter(())

    import avg.pipeline.runner as runner_mod

    original = runner_mod.run
    runner_mod.run = fake_run
    try:
        routes_takes._default_run_pipeline(
            ProjectDoc(id="p", title="T", folder=Path("."), created="2026-08-07T00:00:00"),
            channel=None,
            channel_values={},
            render_lease=lease,
        )
    finally:
        runner_mod.run = original

    assert captured.get("render_lease") is lease


def test_a_second_submission_cannot_destroy_a_queued_job(tmp_path, monkeypatch):
    """`add` is keyed by project id, so a submission for a project that was
    merely QUEUED used to pass an `is_running` guard and overwrite the queued
    entry - the first job's thread never started, its worker's `finally` never
    ran, and for a take the row existed on disk having never run a phase."""
    release = threading.Event()

    def run(project, **kwargs):
        release.wait(timeout=5)
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, run)
    r = client.post(
        "/api/settings/values", headers={"X-AVG-Token": "t0k"}, json={"values": {"max_parallel_jobs": "1"}}
    )
    assert r.status_code == 200

    try:
        client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "A"})
        b = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "B"})
        b_id = b.json()["id"]
        queued = client.app.state.jobs.get(b_id)
        assert queued is not None and queued.queued is True

        again = client.post(f"/api/projects/{b_id}/resume", headers={"X-AVG-Token": "t0k"}, json={"from_phase": "render"})
        assert again.status_code == 409
        assert "already has work in flight" in again.json()["detail"]
        # The queued job is still the same object, not silently replaced.
        assert client.app.state.jobs.get(b_id) is queued
    finally:
        release.set()
        _wait_until_gone(client, b_id)


def test_cancelling_a_waiting_render_does_not_kill_the_active_one(tmp_path, monkeypatch):
    """At the default cap two `/render` jobs can exist at once, one encoding
    and one blocked on the lease. Cancelling the waiting one used to call
    `engine.cancel()` anyway and terminate the ACTIVE project's ffmpeg child -
    deterministically, not as a race."""
    release = threading.Event()

    def run(project, **kwargs):
        release.wait(timeout=5)
        return iter(())

    client = _pipeline_client(tmp_path, monkeypatch, run)
    from avg.core.jobs import Job

    class _Thread:
        def is_alive(self) -> bool:
            return True

    # Two real projects, so the route's own existence check passes.
    ids = []
    for topic in ("A", "B"):
        created = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": topic})
        assert created.status_code == 201
        ids.append(created.json()["id"])
    release.set()
    for pid in ids:
        _wait_until_gone(client, pid)
    active, waiting = ids

    cancels = {"n": 0}
    monkeypatch.setattr(client.app.state.engine, "cancel", lambda: cancels.__setitem__("n", cancels["n"] + 1))

    # "active" owns the engine; "waiting" is a render job blocked on the lease.
    client.app.state.engine_project_id = active
    client.app.state.jobs.add(Job(project_id=active, kind="render", thread=_Thread(), cancel_token=None))
    client.app.state.jobs.add(Job(project_id=waiting, kind="render", thread=_Thread(), cancel_token=None))

    r = client.post(f"/api/projects/{waiting}/cancel", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200
    assert cancels["n"] == 0, "cancelling the waiting render killed the active one's child"

    r = client.post(f"/api/projects/{active}/cancel", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200
    assert cancels["n"] == 1, "cancelling the active render did not reach the engine"
