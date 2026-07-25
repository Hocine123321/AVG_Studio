import pytest
from avg.core.errors import ErrorKind
from avg.media.engine import classify_exit

def test_sys_exit_one_is_a_bug():
    assert classify_exit(1, saw_result=False) is ErrorKind.bug

def test_signal_kill_is_a_resource_failure():
    assert classify_exit(-9, saw_result=False) is ErrorKind.resource

def test_clean_exit_with_a_result_is_not_a_failure():
    assert classify_exit(0, saw_result=True) is None

def test_clean_exit_without_a_result_is_a_bug():
    assert classify_exit(0, saw_result=False) is ErrorKind.bug

from avg.media.engine import MediaEngine
from avg.core.models import RenderRequest
from avg.core.events import ProgressEvent, PhaseFailed

def test_a_crashing_child_becomes_an_event_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_CHILD_CRASH", "1")     # child.py honours this in tests
    events = list(MediaEngine().render(RenderRequest(project_folder=tmp_path), "p1"))
    assert isinstance(events[-1], PhaseFailed)
    assert events[-1].kind in (ErrorKind.bug, ErrorKind.resource)

def test_progress_events_reach_the_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_CHILD_FAKE_PROGRESS", "1")
    events = list(MediaEngine().render(RenderRequest(project_folder=tmp_path), "p1"))
    assert [e.percent for e in events if isinstance(e, ProgressEvent)] == [0, 50, 100]


def test_missing_output_is_reported_as_an_error_not_a_success(tmp_path, monkeypatch):
    """A render that returns cleanly but produces nothing is a failure.

    The old app guarded this explicitly (missing or <1KB master_output.mp4
    was a failure); without the guard the child puts a size-0 result and the
    parent reports RenderFinished for an empty render.
    """
    import avg.media.vendor.avg as vendor_avg
    from avg.media.child import run_child

    monkeypatch.setattr(vendor_avg, "render_project", lambda **kw: None)

    sent = []

    class FakeQueue:
        def put(self, msg):
            sent.append(msg)

        def close(self):
            pass

        def join_thread(self):
            pass

    run_child({"project_path": str(tmp_path)}, FakeQueue())

    assert sent, "child reported nothing at all"
    assert sent[-1][0] == "error"
    assert "missing" in sent[-1][1]
    assert not any(m[0] == "result" for m in sent)


def test_cancel_is_safe_when_nothing_is_running():
    from avg.media.engine import MediaEngine

    engine = MediaEngine()
    engine.cancel()          # must not raise
    engine.cancel()          # twice, still fine
