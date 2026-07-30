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


# ── the composite pass (part 2 of a render) ───────────────────────────────

from avg.core.events import CompositeFinished
from avg.core.models import CompositeRequest


def _base_video(tmp_path):
    """A project folder with a finished base video, which is the state part
    2 always starts from."""
    project = tmp_path / "proj"
    (project / "render").mkdir(parents=True)
    (project / "master_output.mp4").write_bytes(b"x" * 2048)
    return project


def test_a_skipped_caption_pass_is_not_a_failed_render(tmp_path, monkeypatch):
    """The vendored passes answer 0 applied / 2 skipped / 1 fatal
    (`avg/media/vendor/avg.py:3096`, `:3329`). A 2 means "there was nothing I
    could do" - no transcription available, captions switched off - and the
    base video is still there. Reporting that as a failed render would throw
    away a finished encode and tell the user their video does not exist."""
    monkeypatch.setenv("AVG_COMPOSITE_FAKE", "overlays=0,captions=2")

    events = list(MediaEngine().composite(CompositeRequest(project_folder=_base_video(tmp_path)), "p1"))

    finished = [e for e in events if isinstance(e, CompositeFinished)]
    assert len(finished) == 1
    assert finished[0].captions.startswith("skipped")
    assert not [e for e in events if isinstance(e, PhaseFailed)]


def test_a_fatal_composite_pass_fails_the_job(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_COMPOSITE_FAKE", "overlays=0,captions=1")

    events = list(MediaEngine().composite(CompositeRequest(project_folder=_base_video(tmp_path)), "p1"))

    assert [e for e in events if isinstance(e, PhaseFailed)]
    assert not [e for e in events if isinstance(e, CompositeFinished)]


def test_a_clean_composite_pass_reports_what_it_baked(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_COMPOSITE_FAKE", "overlays=0,captions=0")

    project = _base_video(tmp_path)
    (project / "render" / "_final_layers.json").write_text(
        '[{"png": "a.png", "start": 0.0, "end": 1.0}, {"png": "b.png", "start": 2.0, "end": 3.0}]',
        encoding="utf-8",
    )

    events = list(MediaEngine().composite(CompositeRequest(project_folder=project), "p1"))

    finished = next(e for e in events if isinstance(e, CompositeFinished))
    assert finished.captions == "burned"
    assert finished.overlays == 2


def test_the_overlay_pass_is_skipped_when_layers_were_pre_rendered(tmp_path):
    """`_final_layers.json` present means the caption pass bakes the overlays
    in the SAME encode (`avg.py:3399-3408`). Running cmd_overlay_render first
    would encode them a second time - slower, and one generation of quality
    lost for nothing.

    Calls the child function DIRECTLY: the real child runs in another
    process, where a list it appended to could not be observed from here.
    """
    from avg.media.child import _VENDOR_CALLS, run_composite_child
    from avg.media.engine import _CTX

    project = _base_video(tmp_path)
    (project / "render" / "_final_layers.json").write_text('[{"png": "a.png"}]', encoding="utf-8")

    _VENDOR_CALLS.clear()
    run_composite_child({"project_path": str(project), "provider": "estimated"}, _CTX.Queue())

    assert "overlay_render" not in _VENDOR_CALLS
    assert "caption_burn" in _VENDOR_CALLS


def test_the_overlay_pass_runs_when_nothing_was_pre_rendered(tmp_path):
    """No layers file means Director II drew nothing, so the overlays have to
    be composited onto the base before the captions burn."""
    from avg.media.child import _VENDOR_CALLS, run_composite_child
    from avg.media.engine import _CTX

    _VENDOR_CALLS.clear()
    run_composite_child({"project_path": str(_base_video(tmp_path)), "provider": "estimated"}, _CTX.Queue())

    assert _VENDOR_CALLS == ["overlay_render", "caption_burn"]


def test_captions_switched_off_skips_the_burn_without_calling_it(tmp_path):
    """"Off" must not mean "attempted and failed": the pass is not run at
    all, and the outcome says so."""
    from avg.media.child import _VENDOR_CALLS, run_composite_child
    from avg.media.engine import _CTX

    project = _base_video(tmp_path)
    (project / "render" / "_final_layers.json").write_text('[{"png": "a.png"}]', encoding="utf-8")

    _VENDOR_CALLS.clear()
    run_composite_child(
        {"project_path": str(project), "provider": "estimated", "captions_enabled": False},
        _CTX.Queue(),
    )

    assert "caption_burn" not in _VENDOR_CALLS
