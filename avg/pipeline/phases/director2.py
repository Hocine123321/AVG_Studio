"""Phase 5: Director II / AI Graphics.

Runs after Voice & Audio (Phase 4), before Captions (Phase 6). Reads the
finished, scene-segmented script (`scenes.json`, written by
`avg.pipeline.phases.script.ScriptPhase`) and produces the two artifacts
the (already-built, protected) vendored render engine
(`avg.media.vendor.avg`) reads later: a per-scene camera-motion/transition
plan plus one overall colour grade (`render/direction_plan.json`), and a
plan of full-screen "takeover card" moments (`render/card_plan.json`).
These shapes are not a design choice - they were read directly off a real,
working legacy project (this slice's task brief) and are matched
field-for-field, not reinvented.

**`direction_plan.json`** comes from one AI call
(`avg.pipeline.prompts.build_director2_prompt`, itself opening with
`build_director_style_bible`). Ported (simplified - no brand motion-feel
table, no external filters catalogue, both a separate later concern, the
same cut `avg.pipeline.phases.director1` already made for chapters/parts)
from `Get-AiDirectionPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`). The reply
is sanitized against the exact same closed vocabularies the legacy app
validated against (`_VALID_MOTION`/`_VALID_EASE`/`_VALID_TRANSITIONS`/
`_VALID_GRADE_LOOKS`) - an unrecognised or missing field degrades to a safe
default per scene, mirroring Director I's own "one bad entry never fails
the whole plan" leniency, since this structure is guidance for the render
engine, not something later phases mechanically verify against the
narration.

**`card_plan.json`** is deterministic, not AI-driven: one "Focused" card
per `((question))` marker found in `scenes.json`'s own scene text (ported
from the legacy `Export-CardPlan`'s question-marker scan of a separate
`markers.json` this codebase never builds - the markers are read directly
off the scenes instead, see `avg.pipeline.phases._common.
extract_question_markers`). AI-selected "Explainer" concept cards (the
legacy app's `Get-AiEditPlan`) are deliberately NOT ported here - a second,
AI-driven overlay-planning call is a separate, later concern, matching this
task's "match the shapes, don't invent scope" brief. Cards carry only
`{element, scene, text, accent}` at this point - no `start`/`end` yet.
Those are added by Captions (Phase 6), which is the phase that actually
knows word-level timing; see `avg.pipeline.phases.captions`'s module
docstring.

Frequency-capped the same way the legacy planner was (~1 card per 3
scenes, at least 1 whenever any question marker exists at all) so cards
punctuate a video rather than saturating it, and deduplicated to at most
one card per scene (first question wins - a scene rarely needs two boards).
"""

from __future__ import annotations

from dataclasses import dataclass

from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import (
    creative_inputs,
    extract_json_object,
    extract_question_markers,
    read_json,
    render_dir,
    script_dir,
    write_json,
)
from avg.pipeline.prompts import build_director2_prompt
from avg.providers.text.router import generate as generate_text

_VALID_MOTION = {"zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "still"}
_VALID_EASE = {"ease_out_cubic", "ease_in_out", "ease_in", "ease_out", "linear"}
_VALID_TRANSITIONS = {"cut", "fade", "crossfade", "dip", "slide_left", "slide_right", "whip"}
_VALID_GRADE_LOOKS = {"none", "warm", "cool", "cinematic", "noir", "vibrant", "soft"}

_DEFAULT_MOTION = "zoom_in"
_DEFAULT_EASE = "ease_out_cubic"
_DEFAULT_TRANSITION = "cut"
_DEFAULT_GRADE_LOOK = "none"

# Roughly one Focused card per 3 scenes (legacy `Export-CardPlan`'s own
# frequency budget) so cards punctuate a video rather than saturating it.
_CARD_SCENE_DIVISOR = 3

_ACCENT = "accent1"


def _scenes_json_path(ctx: PhaseContext):
    return script_dir(ctx) / "scenes.json"


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize_direction_entry(entry: object, grade: dict, valid_scenes: set[int]) -> dict | None:
    """One `scenes[]` entry from the model, sanitized against the plan's
    closed vocabularies, or `None` if it names no scene that actually
    exists in the finished script. `grade` is shared by every scene (the
    plan sets it once at top level - see module docstring)."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num not in valid_scenes:
        return None

    motion_in = entry.get("motion") if isinstance(entry.get("motion"), dict) else {}
    mtype = str(motion_in.get("type") or _DEFAULT_MOTION).strip().lower()
    if mtype not in _VALID_MOTION:
        mtype = _DEFAULT_MOTION
    intensity = _coerce_float(motion_in.get("intensity"), 0.5)
    intensity = max(0.0, min(1.0, intensity))
    ease = str(motion_in.get("ease") or _DEFAULT_EASE).strip().lower()
    if ease not in _VALID_EASE:
        ease = _DEFAULT_EASE

    transition_in = str(entry.get("transition_in") or _DEFAULT_TRANSITION).strip().lower()
    if transition_in not in _VALID_TRANSITIONS:
        transition_in = _DEFAULT_TRANSITION

    return {
        "scene": scene_num,
        "motion": {"type": mtype, "intensity": intensity, "ease": ease},
        "transition_in": transition_in,
        "grade": grade,
    }


def _build_card_plan(scenes: list[dict]) -> dict:
    """Deterministic Focused takeover cards from `((question))` markers in
    each scene's text - see module docstring. No `start`/`end` yet;
    Captions (Phase 6) adds those."""
    cards: list[dict] = []
    for i, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        text = str(scene.get("text") or "")
        questions = extract_question_markers(text)
        if not questions:
            continue
        # One card per scene at most - a scene rarely needs two boards.
        cards.append({"element": "focused", "scene": i, "text": questions[0], "accent": _ACCENT})

    if cards:
        cap = max(1, len(scenes) // _CARD_SCENE_DIVISOR)
        cards = cards[:cap]

    return {"cards": cards}


@dataclass(frozen=True, slots=True)
class Director2Phase:
    id: int = 5
    key: str = "director2"
    name: str = "Director II / AI Graphics"

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return _scenes_json_path(ctx).exists()

    def run(self, ctx: PhaseContext) -> None:
        scenes = read_json(_scenes_json_path(ctx))
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "director2 phase: no finished scenes.json to plan direction from")

        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        valid_scenes = set(range(1, len(scene_texts) + 1))
        inputs = creative_inputs(ctx)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(inputs, scene_texts)
        result = generate_text(prompt, "director2", config=ctx.router)

        try:
            parsed = extract_json_object(result.text)
        except ValueError as exc:
            raise AvgError(ErrorKind.bug, f"director2 phase: {exc}") from exc

        grade_in = parsed.get("grade") if isinstance(parsed.get("grade"), dict) else {}
        look = str(grade_in.get("look") or _DEFAULT_GRADE_LOOK).strip().lower()
        if look not in _VALID_GRADE_LOOKS:
            look = _DEFAULT_GRADE_LOOK
        grade = {"look": look}

        scenes_out = [
            normalized
            for entry in (parsed.get("scenes") or [])
            if (normalized := _normalize_direction_entry(entry, grade, valid_scenes)) is not None
        ]
        if not scenes_out:
            # Nothing usable survived - degrade to one safe default entry per
            # existing scene, matching Director I's identical leniency (this
            # structure is guidance for the render engine, not something a
            # later phase mechanically verifies against the narration).
            scenes_out = [
                {
                    "scene": i + 1,
                    "motion": {"type": _DEFAULT_MOTION, "intensity": 0.5, "ease": _DEFAULT_EASE},
                    "transition_in": "fade" if i == 0 else _DEFAULT_TRANSITION,
                    "grade": grade,
                }
                for i in range(len(scene_texts))
            ]

        direction_plan = {"version": 1, "grade": grade, "scenes": scenes_out}

        rdir = render_dir(ctx)
        write_json(rdir / "direction_plan.json", direction_plan)
        write_json(rdir / "card_plan.json", _build_card_plan(scenes))

        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id,
                percent=100,
                message=f"Direction plan complete ({len(scenes_out)} scene(s), grade={look}).",
            )
        )
