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

from pathlib import Path
from types import SimpleNamespace

import pytest

import avg.pipeline.phases.render as render_mod
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import PhaseFailed, ProgressEvent, RenderCancelled, RenderFinished
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
    """Yields a scripted event sequence and records whether it was cancelled."""

    def __init__(self, events_for) -> None:
        self._events_for = events_for
        self.cancelled = False

    def render(self, request, project_id):
        yield from self._events_for(request, project_id)

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
