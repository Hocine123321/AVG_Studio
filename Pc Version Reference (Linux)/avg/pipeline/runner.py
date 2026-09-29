"""Sequences `avg.pipeline.registry.REGISTRY`'s phases over one `ProjectDoc`.

Learned from the old app's `Invoke-PipelineLoop`
(`D:\\AI_Video_Gen\\modules\\pipeline.ps1`): it looped `$script:VAF_PHASES`,
timed each phase with a `Stopwatch`, logged/threw on a hard error, and ran an
interactive retry menu on soft (AI-output-validation) failures. This runner
keeps the shape that generalizes - iterate an ordered registry, time each
phase, stop the whole run on failure without losing what came before - and
drops what doesn't: there is no interactive menu here (this app has no
console to prompt on; a failed phase simply halts and reports why), and
"soft" output validation is a concern for the real phases in Tasks 3-5, not
this generic runner.

This module is a generator, deliberately mirroring
`avg.media.engine.MediaEngine.render()`: `avg.api.app` already knows how to
drive a generator of typed events from a background thread and broadcast
each one over `/ws/events` (see `create_app`'s `_worker`/`_run_render`) -
wiring the real pipeline into that same route in a later task is a drop-in
swap of one generator for another, not a new consumption pattern.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

from avg.config.channels import Channel
from avg.core.errors import AvgError, ErrorKind
from avg.core.events import (
    ActivityEvent,
    PhaseFailed,
    PhaseFinished,
    PhaseStarted,
    PipelineCancelled,
    PipelineFinished,
)
from avg.core.models import PhaseRecord, ProjectDoc
from avg.pipeline.phase import CancellationToken, Phase, PhaseContext
from avg.pipeline.registry import REGISTRY

# The events THIS module emits itself. A phase's own `ctx.emit(...)` calls
# (see `PhaseContext`) can surface any event type it chooses - e.g. a
# `ProgressEvent` mid-phase - so `run()`'s actual yield type is wider than
# this; this alias exists to name the runner's own five events at their
# call sites below, not to bound the generator's declared return type.
RunnerEvent = PhaseStarted | PhaseFinished | PhaseFailed | PipelineCancelled | PipelineFinished


class UnknownPhaseError(ValueError):
    """Raised by `run(..., only=<key>)` when no phase in `phases` has that key."""


def _select(phases: Sequence[Phase], start_from: int, only: str | None) -> list[Phase]:
    """Which of `phases` to run, and in what order - `phases` itself (in
    its given order, which for every real caller is `REGISTRY`'s own order)
    decides that; this function only slices it.

    `only`, when given, wins outright: exactly that one phase, ignoring
    `start_from` entirely - this is the app's fast per-phase iteration loop
    (the old "Preview a Phase"), not a one-phase-long resume.
    """
    if only is not None:
        for phase in phases:
            if phase.key == only:
                return [phase]
        raise UnknownPhaseError(f"unknown phase key {only!r}")
    return [phase for phase in phases if phase.id >= start_from]


def _mark_failed(record: PhaseRecord, t0: float, message: str) -> None:
    record.status = "failed"
    record.finished = datetime.now(timezone.utc)
    record.duration_ms = int((time.monotonic() - t0) * 1000)
    record.error = message


def run(
    project: ProjectDoc,
    *,
    channel: Channel,
    channel_values: dict[str, Any],
    router: object = None,
    start_from: int = 0,
    only: str | None = None,
    cancel_token: CancellationToken | None = None,
    phases: Sequence[Phase] = REGISTRY,
    on_phase_change: Callable[[], None] | None = None,
    log: Any = None,
    errors: Any = None,
    render_lease: Any = None,
) -> Iterator[Any]:
    """Run `phases` (`REGISTRY` by default - see that module's docstring for
    why nothing else should ever pass a different order in production)
    against `project`, in order, starting at phase id `start_from` (ignored
    when `only` is given - see `_select`).

    For every phase attempted, mutates `project.phases[phase.key]` in place:
    status moves `pending/failed -> running -> done` (or `-> failed`),
    `started`/`finished`/`duration_ms` are recorded, and `error` is set on
    failure, cleared otherwise. A phase's own `ctx.emit(event)` calls surface
    as ordinary items yielded from this generator, immediately before that
    phase's own `PhaseFinished`/`PhaseFailed`.

    `on_phase_change`, if given, is called with no arguments immediately
    after every one of those in-place mutations (pending/failed -> running,
    and running -> done/failed) - BEFORE the corresponding event is yielded,
    so the record on disk is never behind the record a caller's event stream
    just announced. This is Slice 5 Task 3.5's write-through persistence
    (`avg.pipeline.create.save_project`): a crash mid-pipeline must not lose
    the phases that already finished, which means the write has to happen
    the instant a phase's status changes, not whenever this generator is
    next drained.

    Deliberately a callback rather than this module importing `save_project`
    and calling it directly: `run()` has no notion of `project` vs. "the
    document that's actually supposed to land on disk", and for a take's
    re-run (`avg.api.routes_takes.create_take`) those are two different
    objects - the runner is handed a throwaway `model_copy` (`folder`
    swapped to the take's own folder, `phases` swapped to the take's own
    dict) so its in-place mutations land on `Take.phases`, but the file that
    needs writing is the REAL project's `project.json`, not a nonexistent
    one under the take's folder. Only the caller knows which object that is;
    baking a fixed `save_project(project)` call into this loop would persist
    the wrong file for every take re-run. A plain call embedded in the loop
    (rather than leaving it to the caller's own event-consuming loop to
    remember) is what makes this automatic for every current and future
    caller instead of a discipline each one has to reimplement - see this
    task's own report for the alternative considered and rejected.

    Stops the run - without raising - the first time:
      - a phase raises (an `AvgError` or anything else): that phase's record
        is marked `'failed'` with the error message, a `PhaseFailed` event is
        yielded, and the generator returns. Every phase's record BEFORE
        `start_from` is untouched, so the project stays valid and resumable
        from exactly where it stopped (this task's invariant #5). Records
        from `start_from` onward do not get this guarantee: the sweep above
        (`only is None`) already reset any of them that were `'done'` back to
        `'pending'` before the loop below ever ran, precisely because their
        output belongs to the run being replaced - see that block's own
        comment.
      - `cancel_token.cancelled` is true when about to start the next phase:
        a `PipelineCancelled` event is yielded (naming the phase that would
        have run next) and the generator returns. Cancellation is checked
        BETWEEN phases only - a phase already running is expected to finish
        or fail on its own, matching `CancellationToken`'s own contract.

    Consumed like `MediaEngine.render()`: iterate it to completion (a `for`
    loop that doesn't break early) so every phase attempted actually gets to
    update its `PhaseRecord` before the caller moves on.

    `channel_values` is every scope="channel" setting, already resolved
    (Slice 19 §2.3, `avg.core.settings_schema.resolve_channel_settings`) -
    passed straight onto each phase's `PhaseContext`. Required, not
    computed here: this module has no `channel_defaults_path` (that is an
    `app_state` concept, and the runner stays below the API layer), so it
    only carries what its caller already resolved.
    """
    cancel_token = cancel_token or CancellationToken()
    to_run = _select(phases, start_from, only)

    # One ActivityEvent identity for this whole run (Slice 17), separate
    # from the PhaseStarted/PhaseFinished/PhaseFailed/PipelineCancelled/
    # PipelineFinished events above, which already carry the phase-level
    # truth the rest of the app depends on. This is purely the status
    # pill's feed: same `activity_id` on every ActivityEvent below, so the
    # client's `activity` map folds them onto one row instead of one per
    # phase. Not sticky while running - only the terminal outcome is
    # (spec §3): a phase simply starting is "routine progress", not
    # something that needs acknowledgement.
    activity_id = f"pipeline:{project.id}"
    yield ActivityEvent(activity_id=activity_id, label="Running pipeline", state="running", sticky=False)

    # A phase after the restart point cannot still claim to be done: its
    # output was derived from the run being replaced, and leaving the record
    # alone is how a project ends up with Script `not started` next to a
    # green Visuals - and a render whose images belong to a script that no
    # longer exists. Records only; the FILES stay, exactly as they do for a
    # cloned take. `only=` is exempt because it is the deliberate
    # single-phase iteration loop and sweeps nothing.
    if only is None:
        swept = False
        for phase in phases:
            if phase.id >= start_from:
                record = project.phases.get(phase.key)
                if record is not None and record.status == "done":
                    record.status = "pending"
                    record.error = None
                    # `started`/`finished`/`duration_ms` are the same three
                    # fields a phase that actually starts resets at :171-173
                    # below. Leaving them here would let a swept phase keep
                    # last run's finish timestamp and elapsed time - the UI
                    # reads `duration_ms` for every non-live row regardless
                    # of status (`web/src/Studio.tsx`'s phase table) and
                    # sums it across all phases for the take's total
                    # (`Studio.tsx`, `TakeStrip.tsx`), so a stale value here
                    # keeps counting work the record now says never happened.
                    record.finished = None
                    record.duration_ms = None
                    swept = True
        if swept and on_phase_change is not None:
            on_phase_change()

    for phase in to_run:
        if cancel_token.cancelled:
            yield PipelineCancelled(project_id=project.id, phase=phase.key)
            # Sticky (spec §3: "a job cancelled") - the user asked for this
            # stop and is owed the confirmation that it actually happened,
            # not just a pill that quietly goes idle.
            yield ActivityEvent(
                activity_id=activity_id, label="Cancelled", state="cancelled", sticky=True
            )
            return

        record = project.phases.get(phase.key)
        if record is None:
            record = PhaseRecord(name=phase.name)
            project.phases[phase.key] = record
        record.status = "running"
        record.started = datetime.now(timezone.utc)
        record.finished = None
        record.duration_ms = None
        record.error = None
        if on_phase_change is not None:
            on_phase_change()

        buffered: list[Any] = []
        ctx = PhaseContext(
            project=project,
            channel=channel,
            channel_values=channel_values,
            router=router,
            emit=buffered.append,
            cancel_token=cancel_token,
            log=log,
            errors=errors,
            render_lease=render_lease,
        )

        yield PhaseStarted(project_id=project.id, phase=phase.key)
        # Updates the SAME activity row with which phase is running now -
        # the detail the pill's plain "Rendering" label could never show
        # (spec §1: "not what phase"). Still not sticky; still routine
        # progress.
        yield ActivityEvent(
            activity_id=activity_id,
            label="Running pipeline",
            state="running",
            detail=phase.name,
            sticky=False,
        )
        t0 = time.monotonic()
        try:
            phase.run(ctx)
        except AvgError as exc:
            _mark_failed(record, t0, exc.message)
            if on_phase_change is not None:
                on_phase_change()
            yield from buffered
            yield PhaseFailed(
                project_id=project.id,
                phase=phase.key,
                kind=exc.kind,
                message=exc.message,
                detail=exc.detail,
            )
            # Sticky (spec §3: "a pipeline or phase error") - the whole
            # reason this feature exists is that a failure that happened
            # while nobody was looking must not vanish once it stops
            # happening.
            yield ActivityEvent(
                activity_id=activity_id,
                label="Failed",
                state="failed",
                detail=exc.message,
                sticky=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 - any other bug still halts cleanly
            _mark_failed(record, t0, str(exc))
            if on_phase_change is not None:
                on_phase_change()
            yield from buffered
            yield PhaseFailed(
                project_id=project.id,
                phase=phase.key,
                kind=ErrorKind.bug,
                message=str(exc),
            )
            yield ActivityEvent(
                activity_id=activity_id,
                label="Failed",
                state="failed",
                detail=str(exc),
                sticky=True,
            )
            return

        record.status = "done"
        record.finished = datetime.now(timezone.utc)
        record.duration_ms = int((time.monotonic() - t0) * 1000)
        if on_phase_change is not None:
            on_phase_change()
        yield from buffered
        yield PhaseFinished(project_id=project.id, phase=phase.key, duration_ms=record.duration_ms)

    # Every phase in `to_run` was attempted and none failed or was cancelled
    # (both of those paths `return` above, before ever reaching here) - the
    # one unambiguous "this run is over, successfully" signal. See
    # `PipelineFinished`'s own docstring for why nothing else already means
    # this.
    yield PipelineFinished(project_id=project.id)
    # Sticky (spec §3: "a video completed successfully") - the outcome the
    # whole slice exists to make impossible to miss.
    yield ActivityEvent(activity_id=activity_id, label="Completed", state="done", sticky=True)
