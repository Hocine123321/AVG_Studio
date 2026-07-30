"""Parent-side lifecycle for a render running in a spawned child process.

The dev machine has 7.9 GB of RAM and rendering is what exhausts it. Running
the render in a child process (see `avg.media.child.run_child`) means an
out-of-memory kill takes down the render, not the app: `MediaEngine.render`
turns a dead child into an honest `PhaseFailed` event instead of crashing the
caller, and `classify_exit` is what decides whether that death was a bug in
the render code or a resource exhaustion.

Uses `multiprocessing.get_context("spawn")` throughout: Windows has no fork,
and spawn is what the packaged executable will use, so it is exercised here
rather than only on whatever platform happens to run the tests.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue as queue_mod
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from avg.core.errors import ErrorKind
from avg.core.events import (
    CompositeFinished,
    PhaseFailed,
    ProgressEvent,
    RenderCancelled,
    RenderFinished,
)
from avg.core.models import CompositeRequest, RenderRequest
from avg.media.child import run_child, run_composite_child

# How often the parent wakes up to check whether the child is still alive
# while waiting for the next queue message. Small enough that tests (which
# finish almost instantly) don't feel it, large enough not to busy-spin.
_POLL_SECONDS = 0.1

# How long cancel() (and the end-of-render cleanup) waits for a terminated
# child to actually exit before escalating to kill().
_JOIN_TIMEOUT = 5

_CTX = mp.get_context("spawn")

# avg/media/vendor/avg.py inserts its own directory at sys.path[0] as soon as
# it's imported anywhere in this process (a legacy shim from when it lived as
# a flat sibling script next to overlay_design.py — see its top-of-file
# sys.path.insert). If that happens in THIS (parent) process — e.g. anything
# here imports the vendor module directly, as tests exercising PROGRESS_SINK
# do — multiprocessing's spawn snapshots sys.path when a child starts, and
# ships the polluted list to the child. There, `avg/media/vendor` sits ahead
# of the real package, so `import avg` inside the child resolves to
# vendor/avg.py itself (a bare module, not a package) instead of the real
# avg package, and everything downstream (including finding run_child)
# fails before any of our own code runs. Stripped out, scoped tightly around
# proc.start(), so the fix lives here rather than touching the vendored file.
_VENDOR_DIR = os.path.normcase(os.path.abspath(str(Path(__file__).resolve().parent / "vendor")))


@contextmanager
def _clean_sys_path() -> Iterator[None]:
    original = sys.path[:]
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _VENDOR_DIR]
    try:
        yield
    finally:
        sys.path[:] = original


def classify_exit(exitcode: int, saw_result: bool) -> ErrorKind | None:
    """Turn a dead child's exit code into an ErrorKind, or None for success.

    `render_project` (the vendored render entry point) may call
    `sys.exit(1)` on hard errors — that is not a bug to fix, it's why
    exitcode 1 with no result classifies as `bug` here rather than being
    treated as some other failure mode.

    - exitcode < 0: killed by a signal. On Windows this is what both an OOM
      kill and an external TerminateProcess (our own cancel()/kill()) look
      like — `resource`.
    - exitcode >= 0 with a result seen: clean success — `None`.
    - exitcode >= 0 without a result: either `render_project` called
      `sys.exit(1)`, or the child returned normally without producing
      output. Both are a `bug` — the render code should never exit clean
      without either raising/exiting non-zero or reporting a result.
    """
    if exitcode < 0:
        return ErrorKind.resource
    if saw_result:
        return None
    return ErrorKind.bug


def _request_to_kwargs(req: RenderRequest) -> dict[str, Any]:
    """Translate a RenderRequest into render_project's kwargs.

    Field names mirror render_project's parameters exactly except
    project_folder -> project_path. Converted to a plain dict of
    primitives (Path -> str) rather than sent as the pydantic model itself:
    the child process is a fresh spawned interpreter, and a plain dict is
    unambiguously picklable and needs nothing beyond stdlib to unpack, so
    child.py has no reason to import pydantic at all.
    """
    return {
        "project_path": str(req.project_folder),
        "fps": req.fps,
        "codec": req.codec,
        "threads": req.threads,
        "aspect_ratio": req.aspect_ratio,
        "ken_burns": req.ken_burns,
        "scene_count": req.scene_count,
        "brief_path": req.brief_path,
        "timeline_path": req.timeline_path,
        "scene_map_path": req.scene_map_path,
        "short_edge": req.short_edge,
        "kb_intensity": req.kb_intensity,
        "kb_mode": req.kb_mode,
        "render_engine": req.render_engine,
        "render_opts": req.render_opts,
    }


def _composite_to_kwargs(req: CompositeRequest) -> dict[str, Any]:
    """Translate a CompositeRequest into `run_composite_child`'s kwargs.

    Same reasoning as `_request_to_kwargs`: the child is a fresh spawned
    interpreter, and a plain dict of primitives is unambiguously picklable
    and needs nothing beyond stdlib to unpack.

    `project_folder` becomes `project_path` because that is the name the
    vendored `cmd_*` argv builders use for it - having the child translate
    would put the vendor's vocabulary on both sides of the process boundary.
    """
    return {
        "project_path": str(req.project_folder),
        "provider": req.provider,
        "font": req.font,
        "font_size": req.font_size,
        "font_color": req.font_color,
        "position": req.position,
        "max_words": req.max_words,
        "animation": req.animation,
        "animation_speed": req.animation_speed,
        "render_engine": req.render_engine,
        "captions_enabled": req.captions_enabled,
    }


class MediaEngine:
    """Runs one render at a time in a spawned child process."""

    def __init__(self) -> None:
        self._proc: mp.process.BaseProcess | None = None
        self._cancelled = False

    def render(
        self,
        req: RenderRequest,
        project_id: str,
        *,
        on_stderr: Callable[[str], None] | None = None,
    ) -> Iterator[ProgressEvent | PhaseFailed | RenderCancelled | RenderFinished]:
        """Run a render in a child process, yielding events as it goes.

        Yields a ProgressEvent per progress message the child reports, then
        exactly one final event: RenderFinished on clean success, or
        PhaseFailed (never a raised exception) on any other outcome — a
        crashed or killed child becomes an event the caller can handle, not
        something that takes the caller down with it.
        """
        q: mp.queues.Queue[Any] = _CTX.Queue()
        proc = _CTX.Process(target=run_child, args=(_request_to_kwargs(req), q), daemon=True)
        self._proc = proc
        self._cancelled = False
        with _clean_sys_path():
            proc.start()

        result: dict[str, Any] = {"output": None, "size": 0, "seen": False, "error": None}

        def _consume(msg: tuple[Any, ...]) -> ProgressEvent | None:
            kind = msg[0]
            if kind == "progress":
                _, percent, message = msg
                return ProgressEvent(project_id=project_id, percent=percent, message=message)
            if kind == "stderr":
                # The vendored engine's own diagnosis. Handed to the caller's
                # log rather than yielded: it is not an event the UI has any
                # decision to make about.
                if on_stderr is not None:
                    with suppress(Exception):
                        on_stderr(msg[1])
                return None
            if kind == "result":
                _, output, size = msg
                result["output"] = output
                result["size"] = size
                result["seen"] = True
            elif kind == "error":
                # The child ran to completion but the render is not usable.
                # Carries a reason the bare exit code cannot.
                result["error"] = msg[1]
            return None

        try:
            # Drain until the child is dead AND the queue has nothing left
            # waiting — checking is_alive() first would race a child that
            # exits between the check and the get().
            while True:
                try:
                    msg = q.get(timeout=_POLL_SECONDS)
                except queue_mod.Empty:
                    if proc.is_alive():
                        continue
                    break
                event = _consume(msg)
                if event is not None:
                    yield event

            # Final non-blocking drain: child.py closes and joins its queue's
            # feeder thread before exiting, so by the time is_alive() is
            # False every message it put has already been flushed to the
            # pipe — this catches anything that arrived in the gap between
            # the last get() timeout and the is_alive() check above.
            while True:
                try:
                    msg = q.get_nowait()
                except queue_mod.Empty:
                    break
                event = _consume(msg)
                if event is not None:
                    yield event
        finally:
            proc.join(_JOIN_TIMEOUT)
            if proc.is_alive():
                proc.kill()
                proc.join(_JOIN_TIMEOUT)

        # -1 (treated as "killed") only if the process still won't report an
        # exit code after terminate+join+kill+join above — should not
        # happen, but a render must never look like a clean success by
        # accident.
        exitcode = proc.exitcode if proc.exitcode is not None else -1

        # A cancelled render is terminated, which on Windows yields the same
        # negative exit code as an OOM kill. Only the parent knows the
        # difference, so it must be decided here rather than in classify_exit.
        if self._cancelled:
            yield RenderCancelled(project_id=project_id)
            return

        kind = classify_exit(exitcode, saw_result=result["seen"])
        if kind is None:
            yield RenderFinished(
                project_id=project_id,
                output=Path(result["output"]),
                size_bytes=result["size"],
            )
        else:
            yield PhaseFailed(
                project_id=project_id,
                phase="render",
                kind=kind,
                message=result["error"] or f"render child exited with code {exitcode}",
                detail=f"exit code {exitcode}",
            )

    def composite(
        self,
        req: CompositeRequest,
        project_id: str,
        *,
        on_stderr: Callable[[str], None] | None = None,
    ) -> Iterator[ProgressEvent | PhaseFailed | CompositeFinished]:
        """Run part 2 of a render - overlays, cards and captions - in its own
        child process.

        Its OWN child, not a continuation of the base render's: a moviepy
        out-of-memory kill during the base encode cannot poison this pass,
        and a retry of this pass reuses the finished `master_output.mp4`
        instead of re-encoding it.

        Yields progress, then exactly one terminal event - `CompositeFinished`
        carrying what was baked, or `PhaseFailed`. A vendored pass answering
        "skipped" (exit 2) is a finish, not a failure: the base video exists,
        and reporting a failure would throw away a finished encode.

        The drain loop is never broken out of early, for the same reason
        `render`'s is not - running it to exhaustion is what guarantees the
        child gets joined.
        """
        q: mp.queues.Queue[Any] = _CTX.Queue()
        proc = _CTX.Process(target=run_composite_child, args=(_composite_to_kwargs(req), q), daemon=True)
        self._proc = proc
        self._cancelled = False
        with _clean_sys_path():
            proc.start()

        result: dict[str, Any] = {"overlays": 0, "captions": "", "seen": False, "error": None}

        def _consume(msg: tuple[Any, ...]) -> ProgressEvent | None:
            kind = msg[0]
            if kind == "progress":
                _, percent, message = msg
                return ProgressEvent(project_id=project_id, percent=percent, message=message)
            if kind == "stderr":
                if on_stderr is not None:
                    with suppress(Exception):
                        on_stderr(msg[1])
                return None
            if kind == "composite":
                _, overlays, captions = msg
                result["overlays"] = overlays
                result["captions"] = captions
                result["seen"] = True
            elif kind == "error":
                result["error"] = msg[1]
            return None

        try:
            while True:
                try:
                    msg = q.get(timeout=_POLL_SECONDS)
                except queue_mod.Empty:
                    if proc.is_alive():
                        continue
                    break
                event = _consume(msg)
                if event is not None:
                    yield event

            while True:
                try:
                    msg = q.get_nowait()
                except queue_mod.Empty:
                    break
                event = _consume(msg)
                if event is not None:
                    yield event
        finally:
            proc.join(_JOIN_TIMEOUT)
            if proc.is_alive():
                proc.kill()
                proc.join(_JOIN_TIMEOUT)

        exitcode = proc.exitcode if proc.exitcode is not None else -1

        if result["error"] is not None:
            yield PhaseFailed(
                project_id=project_id,
                phase="render",
                kind=ErrorKind.bug,
                message=str(result["error"]),
                detail=f"exit code {exitcode}",
            )
            return

        kind = classify_exit(exitcode, saw_result=bool(result["seen"]))
        if kind is not None:
            yield PhaseFailed(
                project_id=project_id,
                phase="render",
                kind=kind,
                message=f"composite child exited with code {exitcode}",
                detail=f"exit code {exitcode}",
            )
            return

        yield CompositeFinished(
            project_id=project_id,
            overlays=int(result["overlays"]),
            captions=str(result["captions"]),
        )

    def cancel(self) -> None:
        """Stop a running render. Safe to call even if nothing is running."""
        proc = self._proc
        if proc is None or not proc.is_alive():
            return
        # Set before terminating: render() reads this to tell a user-requested
        # stop apart from a crash, and the child dies as soon as we terminate.
        self._cancelled = True
        proc.terminate()
        proc.join(_JOIN_TIMEOUT)
        if proc.is_alive():
            proc.kill()
            proc.join(_JOIN_TIMEOUT)
