"""The ordered phase registry - THE single source of truth for phase order.

The old PowerShell app kept two things in sync by hand: `$script:VAF_PHASES`
(`D:\\AI_Video_Gen\\modules\\pipeline.ps1`) and a separate `$PHASE_ORDER`
resume-position map in `main.ps1`, the latter additionally aliasing legacy
key spellings onto the same ids. Nothing enforced that the two agreed, and
the module docstring there says so explicitly ("must match $PHASE_ORDER").
`REGISTRY` below is the only list of phases that exists in this codebase:
the runner iterates it, `avg.config.legacy_import.PHASE_ORDER` is derived
from it rather than declaring its own copy, and nothing else - not the API,
not the UI, not a test - may declare a second ordered list of phase keys.
Anything needing "what order do phases run in" imports `REGISTRY` (or the
`next_phase_id` helper below) rather than restating it.

Every phase here is a stub for this task (Task 1 of Slice 4): always ready,
does nothing. Tasks 3-5 replace each `_StubPhase` entry with a real
implementation of the same `avg.pipeline.phase.Phase` protocol - the id/key/
name triples themselves are not expected to change.
"""

from __future__ import annotations

from dataclasses import dataclass

from avg.core.models import ProjectDoc
from avg.pipeline.phase import PhaseContext


@dataclass(frozen=True, slots=True)
class _StubPhase:
    """A placeholder phase satisfying `avg.pipeline.phase.Phase`.

    Always reports its inputs as ready (there is nothing real to check
    yet) and does nothing on `run` - the runner alone is responsible for
    turning that into a recorded 'done' `PhaseRecord`. Task 3-5 authors:
    replace the entry in `REGISTRY` below with a real class of the same
    shape; nothing outside this module needs to change.
    """

    id: int
    key: str
    name: str

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return True

    def run(self, ctx: PhaseContext) -> None:
        return None


# Ids are contiguous 0-7, matching the reference app's phase order exactly
# (see `D:\AI_Video_Gen\modules\pipeline.ps1`'s `$script:VAF_PHASES` and this
# slice's plan doc) - Brief, Script, Director I, Visuals, Voice & Audio,
# Director II / AI Graphics, Captions & Takeover Text, Render.
#
# Phase 7 ("render") is deliberately still a stub here: the real render
# phase (Task 5/6's territory per the task brief) delegates to the already-
# built `avg.media.engine.MediaEngine.render()` rather than reimplementing
# rendering - this task only reserves its place in the order.
REGISTRY: tuple[_StubPhase, ...] = (
    _StubPhase(0, "brief", "Brief"),
    _StubPhase(1, "script", "Script"),
    _StubPhase(2, "director1", "Director I"),
    _StubPhase(3, "visuals", "Visuals"),
    _StubPhase(4, "voice", "Voice & Audio"),
    _StubPhase(5, "director2", "Director II / AI Graphics"),
    _StubPhase(6, "captions", "Captions & Takeover Text"),
    _StubPhase(7, "render", "Render"),
)

BY_KEY: dict[str, _StubPhase] = {phase.key: phase for phase in REGISTRY}


def next_phase_id(project: ProjectDoc) -> int:
    """Where a resume should start: the first phase (in `REGISTRY` order)
    whose `PhaseRecord` isn't `'done'` yet.

    This is how "the document says the next phase" is satisfied without a
    second pointer field: a phase's own status in `project.phases` IS the
    resume position, read fresh every time, rather than a `meta.phase`-style
    value the runner would have to remember to advance in lockstep. A
    brand-new project (`phases == {}`) resolves to `0` - Brief - matching
    this slice's "new-video start phase is 0" invariant. Once every phase
    has completed, resolves to `len(REGISTRY)` (8), one past the last valid
    id, which `avg.pipeline.runner.run`'s `start_from` filter simply selects
    nothing for.
    """
    for phase in REGISTRY:
        record = project.phases.get(phase.key)
        if record is None or record.status != "done":
            return phase.id
    return len(REGISTRY)
