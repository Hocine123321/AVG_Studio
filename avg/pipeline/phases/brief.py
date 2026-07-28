"""Phase 0: the Creative Brief.

The first AI call of a run: high-level creative intention (emotional arc,
tone, angle) that Script (Phase 1) and Director I (Phase 2) read as
context, not a per-scene plan - see `avg.pipeline.prompts.build_brief_prompt`
for why (ported from the legacy app's B2.3 "brief is creative-intention
only" rewrite). Writes `<project>/script/brief.json`.

Nothing downstream *requires* the brief to exist (see `ScriptPhase`'s own
docstring: brief context is optional enrichment, matching the legacy app's
"legacy blind mode"), so `inputs_ready` has nothing to check - it can always
run, needing only the project's own topic.
"""

from __future__ import annotations

from dataclasses import dataclass

from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import creative_inputs, extract_json_object, script_dir, write_json
from avg.pipeline.prompts import build_brief_prompt
from avg.providers.text.router import generate as generate_text


@dataclass(frozen=True, slots=True)
class BriefPhase:
    id: int = 0
    key: str = "brief"
    name: str = "Brief"

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return True

    def run(self, ctx: PhaseContext) -> None:
        inputs = creative_inputs(ctx)
        prompt = build_brief_prompt(inputs)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Writing creative brief..."))
        result = generate_text(prompt, "brief", config=ctx.router)

        try:
            parsed = extract_json_object(result.text)
        except ValueError as exc:
            raise AvgError(ErrorKind.bug, f"brief phase: {exc}") from exc

        # A brief with no emotional_arc is useless direction for every later
        # phase (matches the legacy `Invoke-CreativeBrief`'s own "$hasArc"
        # check) - treat it as a failed generation rather than writing an
        # empty/meaningless brief.json that would look like a success.
        emotional_arc = str(parsed.get("emotional_arc") or "").strip()
        if not emotional_arc:
            raise AvgError(ErrorKind.bug, "brief phase: model response had no emotional_arc")

        brief = {
            "emotional_arc": emotional_arc,
            "tone": str(parsed.get("tone") or "").strip(),
            "angle": str(parsed.get("angle") or "").strip(),
            "notes": str(parsed.get("notes") or "").strip(),
        }
        write_json(script_dir(ctx) / "brief.json", brief)
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Creative brief complete."))
