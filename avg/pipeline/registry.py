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

Every phase started as a `_StubPhase` in Task 1 of this slice (always ready,
does nothing). Task 3 replaced ids 0-2 (`BriefPhase`/`ScriptPhase`/
`Director1Phase`) with real implementations; Task 4 replaced ids 3-4
(`VisualsPhase`/`VoicePhase`) the same way; Task 5 replaces the remaining
`_StubPhase` entries identically - one real class per id, of the same
`avg.pipeline.phase.Phase` protocol shape, id/key/name unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from avg.core.models import ProjectDoc
from avg.pipeline.phase import Phase, PhaseContext
from avg.pipeline.phases.captions import CaptionsPhase
from avg.pipeline.phases.director import Director1Phase, Director2Phase
from avg.pipeline.phases.render import RenderPhase
from avg.pipeline.phases.visuals import VisualsPhase
from avg.pipeline.phases.voice import VoicePhase
from avg.pipeline.phases.writing import BriefPhase, ScriptPhase


@dataclass(frozen=True, slots=True)
class _StubPhase:
    """A placeholder phase satisfying `avg.pipeline.phase.Phase`.

    Always reports its inputs as ready (there is nothing real to check
    yet) and does nothing on `run` - the runner alone is responsible for
    turning that into a recorded 'done' `PhaseRecord`. Task 4-5 authors:
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
# Ids 0-2 (Task 3 of this slice) are real implementations
# (`avg.pipeline.phases.writing` for Brief and Script, `.director` for
# Director I) - see those modules for the two-pass script's
# verbatim-partition invariant and Director I's structure planning. Ids 3-4 (Task 4) are `avg.pipeline.phases.visuals/voice` - one
# image per scene via a RAM-capped worker pool, and narration synthesis with
# real `[pause]` silence, respectively. Ids 5-6 (Task 5) are
# `avg.pipeline.phases.director` (Director II) and `.captions` - the motion/grade + takeover-card
# plan, and transcription-to-`.ass` (AssemblyAI or a derived estimate),
# respectively. Id 7 is `avg.pipeline.phases.render` - an adapter onto the
# already-built `avg.media.engine.MediaEngine.render()`, which runs the
# vendored render engine in a child process. It deliberately owns no
# rendering logic of its own.
#
# Every phase began as a `_StubPhase` and was swapped for a real class of the
# same id and key, nothing changing here but this tuple. `_StubPhase` is kept
# because the runner's tests still build stub sequences with it.
REGISTRY: tuple[Phase, ...] = (
    BriefPhase(),
    ScriptPhase(),
    Director1Phase(),
    VisualsPhase(),
    VoicePhase(),
    Director2Phase(),
    CaptionsPhase(),
    RenderPhase(),
)

BY_KEY: dict[str, Phase] = {phase.key: phase for phase in REGISTRY}


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
