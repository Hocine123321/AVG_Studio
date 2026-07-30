"""The phase contract every pipeline phase implements, and the context
threaded through it.

A phase is a `Protocol`, not a base class, on purpose: `avg.pipeline.registry`
owns the concrete phase objects (stubs today, real implementations from Task
3 onward), and nothing here needs to know which one is running. `PhaseContext`
is the ONLY channel between a phase and the runner (`avg.pipeline.runner`) -
a phase must never hold a reference back to the runner, the registry, or any
other phase. If a phase needs something beyond what's on this context, that
something belongs here, not as a new back-reference.

`CancellationToken` is deliberately a thin wrapper around `threading.Event`
rather than a bare `Event`: the runner only ever checks it BETWEEN phases
(see that module's docstring for why), and giving it a named type here keeps
that "cooperative, not preemptive" contract self-documenting at every call
site instead of a bare `.is_set()` that reads like it could mean anything.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from avg.config.channels import Channel
from avg.core.models import ProjectDoc


class CancellationToken:
    """A cooperative stop signal shared across one pipeline run.

    `cancel()` is safe to call from any thread at any time - matching
    `avg.media.engine.MediaEngine.cancel()`'s "safe even if nothing is
    running" contract - and is typically called from the request-handling
    thread while the run itself executes on a background worker thread (see
    `avg.api.app`'s existing render-thread pattern, which the pipeline
    runner is designed to be driven the same way).
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class PhaseContext:
    """Everything one phase needs to do its work, and nothing more.

    `project` is the live `ProjectDoc` - a phase reads its inputs from it and
    (for real phases, from Task 3 on) writes its outputs onto disk under
    `project.folder`; it must NOT mutate `project.phases` itself, that
    bookkeeping belongs to the runner alone. `channel` is the active
    channel's resolved settings (`avg.config.channels.Channel`) - the only
    place creative settings live (see that module's docstring). `router` is
    whatever provider router this phase needs (Slice 3's text/image/voice
    routers all differ in shape, so this is intentionally untyped here -
    a real phase narrows it to what it expects). `emit` reports a
    progress/status event without the phase knowing who's listening or how
    (the runner is what turns these into yielded events - see
    `avg.pipeline.runner`). `cancel_token` lets a long-running phase check
    for a requested stop; today's stub phases never touch it.
    """

    project: ProjectDoc
    channel: Channel
    router: Any
    emit: Callable[[Any], None]
    cancel_token: CancellationToken
    #: The take's run log (`avg.core.take_log.TakeLog`), or `None` in unit
    #: contexts that build a bare context. A phase logs the DETAIL behind its
    #: events here - a model it fell back to, a file it could not read -
    #: without that detail having to become an event the UI would then need to
    #: decide what to do about.
    log: Any = None


@runtime_checkable
class Phase(Protocol):
    """One pipeline phase. `id`/`key`/`name` mirror the old app's
    `$script:VAF_PHASES` entries (`D:\\AI_Video_Gen\\modules\\pipeline.ps1`)
    but here they live on the phase object itself rather than a parallel
    `[PSCustomObject]` row an ad-hoc registry keeps in sync by hand -
    `avg.pipeline.registry.REGISTRY` is the only place instances of this
    protocol are ever ordered.

    `id` is this phase's fixed position in `REGISTRY` (0-7, contiguous) -
    the runner uses it to compute the resume pointer
    (`avg.pipeline.registry.next_phase_id`), but a phase itself should never
    assume anything about a neighbour beyond its own `id`/`key`.
    """

    # Exposed as read-only properties, not plain field annotations: a plain
    # `id: int` here would make the Protocol require a SETTABLE attribute,
    # which a frozen dataclass (every real phase, stub or otherwise) can
    # never satisfy under strict structural typing - mypy rejects it with
    # "expected settable variable, got read-only attribute". Properties
    # match both a frozen dataclass's fields and a hand-written class's
    # read-only attributes.
    @property
    def id(self) -> int: ...

    @property
    def key(self) -> str: ...

    @property
    def name(self) -> str: ...

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        """Whether this phase's required inputs already exist for
        `ctx.project` - what a bare `only=<key>` re-run (this app's fast
        iteration loop, the old "Preview a Phase") checks before doing any
        work. Every stub phase in this task returns `True` unconditionally;
        a real phase (Tasks 3-5) checks for the actual files/fields it
        reads.
        """
        ...

    def run(self, ctx: PhaseContext) -> None:
        """Do the phase's work.

        Raise `avg.core.errors.AvgError` for a classified provider/media
        failure, or let any other exception propagate - either way the
        runner turns it into a `PhaseFailed` event and halts the run rather
        than crashing its caller. Must not return a value: progress and any
        other reporting go through `ctx.emit`, never a return channel.
        """
        ...
