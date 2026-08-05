"""Tests for phase 7 (`avg.pipeline.phases.render`).

The phase itself is a thin adapter onto `avg.media.engine.MediaEngine`, which
has its own tests - so these cover only what the adapter adds: translating the
engine's event stream into pipeline outcomes, and reaching the child process
when the pipeline is cancelled.

A real `MediaEngine` spawns a child process that runs moviepy/ffmpeg for
minutes, so every test here substitutes a fake that yields the same event
sequence the real one does.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import avg.pipeline.phases.render as render_mod
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import (
    CompositeFinished,
    PhaseFailed,
    ProgressEvent,
    RenderCancelled,
    RenderFinished,
)
from avg.core.models import ProjectDoc
from avg.pipeline.phase import CancellationToken


def _ctx(tmp_path: Path, token: CancellationToken | None = None):
    project = ProjectDoc(id="p1", title="T", folder=tmp_path, created="2026-07-26T00:00:00")
    emitted: list[object] = []
    ctx = SimpleNamespace(
        project=project,
        channel=None,
        router=None,
        emit=emitted.append,
        cancel_token=token or CancellationToken(),
    )
    return ctx, emitted


class _Engine:
    """Yields a scripted event sequence and records whether it was cancelled.

    `composite` (part 2 of a render) defaults to a clean, do-nothing finish so
    a test about part 1 does not have to script part 2 as well. Tests that
    care about part 2 pass `composite_for`.
    """

    def __init__(self, events_for, composite_for=None) -> None:
        self._events_for = events_for
        self._composite_for = composite_for
        self.cancelled = False
        self.composite_requests: list[object] = []

    def render(self, request, project_id, *, on_stderr=None):
        yield from self._events_for(request, project_id)

    def composite(self, request, project_id, *, on_stderr=None):
        self.composite_requests.append(request)
        if self._composite_for is not None:
            yield from self._composite_for(request, project_id)
            return
        yield CompositeFinished(project_id=project_id, overlays=0, captions="burned")

    def cancel(self) -> None:
        self.cancelled = True


def test_success_records_the_output_on_the_project(tmp_path, monkeypatch):
    def events(request, project_id):
        yield ProgressEvent(project_id=project_id, percent=50, message="halfway")
        out = request.project_folder / "master_output.mp4"
        out.write_bytes(b"video-bytes")
        yield RenderFinished(project_id=project_id, output=out, size_bytes=11)

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(events))
    ctx, emitted = _ctx(tmp_path)

    render_mod.RenderPhase().run(ctx)

    assert ctx.project.master_output == tmp_path / "master_output.mp4"
    assert any(isinstance(e, RenderFinished) for e in emitted)
    assert any(isinstance(e, ProgressEvent) for e in emitted)


def test_engine_failure_becomes_an_avg_error_carrying_the_kind(tmp_path, monkeypatch):
    """A dead render child must not surface as a bare crash.

    `MediaEngine` already classifies why the child died - an OOM is
    `resource`, not a bug in the phase - and that classification has to
    survive the trip into the pipeline, or the UI cannot tell the user
    anything useful.
    """

    def events(request, project_id):
        yield PhaseFailed(
            project_id=project_id, phase="render", kind=ErrorKind.resource,
            message="render child exited with code -9", detail="exit code -9",
        )

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(events))
    ctx, _ = _ctx(tmp_path)

    with pytest.raises(AvgError) as excinfo:
        render_mod.RenderPhase().run(ctx)

    assert excinfo.value.kind is ErrorKind.resource


def test_cancelling_the_pipeline_reaches_the_render_child(tmp_path, monkeypatch):
    """Cancellation must stop ffmpeg, not merely stop reading its events.

    A render is the one phase long enough that waiting for a phase boundary
    would feel broken, so the token is checked per event and forwarded to the
    engine. Without the forward, ffmpeg keeps burning CPU after the user
    asked it to stop.
    """
    token = CancellationToken()
    engines: list[_Engine] = []

    def events(request, project_id):
        yield ProgressEvent(project_id=project_id, percent=10, message="starting")
        token.cancel()
        yield ProgressEvent(project_id=project_id, percent=20, message="still going")
        yield RenderCancelled(project_id=project_id)

    def make_engine():
        engine = _Engine(events)
        engines.append(engine)
        return engine

    monkeypatch.setattr(render_mod, "MediaEngine", make_engine)
    ctx, _ = _ctx(tmp_path, token)

    with pytest.raises(AvgError):
        render_mod.RenderPhase().run(ctx)

    assert engines[0].cancelled, "cancelling the pipeline did not cancel the render child"


# ---------------------------------------------------------------------------
# render_threads


def test_render_threads_setting_reaches_the_request():
    """0 (the default) must mean auto, not one thread.

    Auto is expressed by NOT passing `threads`, so `RenderRequest`'s own
    `os.cpu_count()` default stands - passing a number this code guessed
    would be a second, wrong source of truth for "auto".
    """
    from avg.pipeline.phases.render import _thread_kwargs

    assert _thread_kwargs(4) == {"threads": 4}
    assert _thread_kwargs("4") == {"threads": 4}
    # Auto / unset / nonsense all defer to the model default.
    assert _thread_kwargs(0) == {}
    assert _thread_kwargs(None) == {}
    assert _thread_kwargs(-1) == {}
    assert _thread_kwargs("plenty") == {}


# ── the two-part render ───────────────────────────────────────────────────


def _finished_base(request, project_id):
    """Part 1's happy path: a base video large enough to count as real."""
    out = request.project_folder / "master_output.mp4"
    out.write_bytes(b"video-bytes" * 200)
    yield RenderFinished(project_id=project_id, output=out, size_bytes=2200)


def test_render_substate_is_read_off_disk(tmp_path):
    """Derived, never stored - the same choice `script_substate` already
    makes. A stored flag and a project folder can disagree, and when they do
    the user is shown a state their files do not support."""
    assert render_mod.render_substate(tmp_path) == "none"

    (tmp_path / "master_output.mp4").write_bytes(b"x" * 2048)
    assert render_mod.render_substate(tmp_path) == "base"

    (tmp_path / "render").mkdir()
    (tmp_path / "render" / "_composite.json").write_text('{"overlays": 2}', encoding="utf-8")
    assert render_mod.render_substate(tmp_path) == "done"


def test_a_truncated_output_is_not_a_finished_base(tmp_path):
    """The old app's guard: anything under 1KB never represents a real
    render, however cleanly the encoder returned."""
    (tmp_path / "master_output.mp4").write_bytes(b"tiny")
    assert render_mod.render_substate(tmp_path) == "none"


def test_part_two_runs_and_records_what_it_baked(tmp_path, monkeypatch):
    def composite(request, project_id):
        yield CompositeFinished(project_id=project_id, overlays=3, captions="burned")

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(_finished_base, composite))
    ctx, emitted = _ctx(tmp_path)

    render_mod.RenderPhase().run(ctx)

    recorded = json.loads((tmp_path / "render" / "_composite.json").read_text(encoding="utf-8"))
    assert recorded["overlays"] == 3
    assert recorded["captions"] == "burned"
    assert "at" in recorded
    assert render_mod.render_substate(tmp_path) == "done"
    assert any(isinstance(e, CompositeFinished) for e in emitted)


def test_a_skipped_caption_pass_still_finishes_the_phase(tmp_path, monkeypatch):
    """A video with overlays and no captions is a finished video. The phase
    records WHY in render/_composite.json rather than failing."""

    def composite(request, project_id):
        yield CompositeFinished(
            project_id=project_id, overlays=0, captions="skipped: no transcription was available"
        )

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(_finished_base, composite))
    ctx, _ = _ctx(tmp_path)

    render_mod.RenderPhase().run(ctx)  # must NOT raise

    recorded = json.loads((tmp_path / "render" / "_composite.json").read_text(encoding="utf-8"))
    assert recorded["captions"].startswith("skipped")


def test_progress_is_scaled_so_the_bar_never_restarts(tmp_path, monkeypatch):
    """Part 1 spans 0-70 and part 2 the rest. Two passes each reporting 0-100
    would read as the render having started over."""

    def events(request, project_id):
        yield ProgressEvent(project_id=project_id, percent=0, message="encoding")
        yield ProgressEvent(project_id=project_id, percent=100, message="encoded")
        yield from _finished_base(request, project_id)

    def composite(request, project_id):
        yield ProgressEvent(project_id=project_id, percent=0, message="compositing")
        yield ProgressEvent(project_id=project_id, percent=100, message="composited")
        yield CompositeFinished(project_id=project_id, overlays=1, captions="burned")

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(events, composite))
    ctx, emitted = _ctx(tmp_path)

    render_mod.RenderPhase().run(ctx)

    assert [e.percent for e in emitted if isinstance(e, ProgressEvent)] == [0, 70, 70, 100]


def test_part_two_is_told_which_provider_seeded_the_cache(tmp_path, monkeypatch):
    """The provider string is part of the vendored cache's key. Screen Text
    recorded it on disk; passing anything else re-transcribes and re-pays."""
    engines: list[_Engine] = []

    def make_engine():
        engine = _Engine(_finished_base)
        engines.append(engine)
        return engine

    monkeypatch.setattr(render_mod, "MediaEngine", make_engine)
    ctx, _ = _ctx(tmp_path)
    (tmp_path / "render").mkdir(exist_ok=True)
    (tmp_path / "render" / "_transcript_source.json").write_text(
        '{"provider": "assemblyai"}', encoding="utf-8"
    )

    render_mod.RenderPhase().run(ctx)

    assert engines[0].composite_requests[0].provider == "assemblyai"


def test_an_absent_transcript_source_falls_back_to_estimated(tmp_path, monkeypatch):
    """A legacy project, or one whose Screen Text phase never ran. The
    fallback matches what the estimate path would have written."""
    engines: list[_Engine] = []

    def make_engine():
        engine = _Engine(_finished_base)
        engines.append(engine)
        return engine

    monkeypatch.setattr(render_mod, "MediaEngine", make_engine)
    ctx, _ = _ctx(tmp_path)

    render_mod.RenderPhase().run(ctx)

    assert engines[0].composite_requests[0].provider == "estimated"


def test_a_failing_part_two_fails_the_phase(tmp_path, monkeypatch):
    def composite(request, project_id):
        yield PhaseFailed(
            project_id=project_id,
            phase="render",
            kind=ErrorKind.bug,
            message="composite pass failed (overlays=1, captions=0)",
            detail="",
        )

    monkeypatch.setattr(render_mod, "MediaEngine", lambda: _Engine(_finished_base, composite))
    ctx, _ = _ctx(tmp_path)

    with pytest.raises(AvgError):
        render_mod.RenderPhase().run(ctx)
