"""Phase 2: Director I - the "map-maker."

Runs after Script (Phase 1), before Visuals (Phase 3). Reads the FINISHED,
scene-segmented script and decides the video's visual STRUCTURE: how many
distinct images the story needs (`image_count`), and per existing scene, its
narrative role, delivery beat, and visual intent - the direction Visuals
(Phase 3) and Director II (Phase 5) read instead of guessing per-image.
Ported (simplified - no chapters/parts grouping, no per-scene filters; see
`avg.pipeline.prompts.build_director1_prompt`'s docstring) from the legacy
`Get-AiVideoMap` (`D:\\AI_Video_Gen\\modules\\ai.ps1`).

Writes `<project>/script/video_map.json`.

Unlike the two-pass script phase, a malformed per-scene entry here is NOT
the load-bearing invariant - the structure is guidance for later phases,
not something later phases mechanically verify against the narration. So
this phase is lenient about individual bad entries (an unrecognised role/
beat/visual_intent, an out-of-range scene number, a missing `scenes` array
entirely) and degrades each to a safe default rather than failing the whole
run over one bad field. It still fails hard - `AvgError(ErrorKind.bug, ...)`
- when the response has NO usable JSON at all, or when there is no finished
script to build a structure from in the first place.

`inputs_ready` checks that `scenes.json` (Phase 1's output) exists: unlike
Phases 0/1, Director I cannot do anything meaningful without it, so a lone
re-run of this phase needs it already on disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._parsing import extract_json_object
from avg.pipeline.phases._shared import creative_inputs, read_json, script_dir, write_json
from avg.pipeline.prompts import build_director1_prompt
from avg.providers.text.router import generate as generate_text

_VALID_ROLES = {"hook", "tension", "reveal", "proof", "example", "pause", "hit", "cta"}
_VALID_BEATS = {"none", "HIT", "PAUSE", "ZOOM", "SLOWDOWN"}
_VALID_INTENTS = {"BACKGROUND", "ASSET", "POP"}

# Sensible bounds regardless of what the model proposes - mirrors the
# legacy app's own min/max clamp (there derived from target length; kept as
# fixed, generous bounds here since this task has no format-spec input yet).
_MIN_IMAGES = 3
_MAX_IMAGES = 160


def _scenes_json_path(ctx: PhaseContext):
    return script_dir(ctx) / "scenes.json"


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize_scene_entry(entry: object) -> dict | None:
    """One `scenes[]` entry from the model, defensively coerced to a known
    shape, or `None` if it has no usable scene number at all."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num < 1:
        return None

    role = str(entry.get("role") or "").strip().lower()
    if role not in _VALID_ROLES:
        role = ""

    beat = str(entry.get("beat") or "none").strip()
    beat = "none" if beat.lower() == "none" else beat.upper()
    if beat not in _VALID_BEATS:
        beat = "none"

    intent = str(entry.get("visual_intent") or "BACKGROUND").strip().upper()
    if intent not in _VALID_INTENTS:
        intent = "BACKGROUND"

    return {"scene": scene_num, "role": role, "beat": beat, "visual_intent": intent}


def _normalize_zoom_word(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    word = str(entry.get("word") or "").strip()
    if scene_num is None or scene_num < 1 or not word:
        return None
    return {"scene": scene_num, "word": word}


@dataclass(frozen=True, slots=True)
class Director1Phase:
    id: int = 2
    key: str = "director1"
    name: str = "Director I"

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return _scenes_json_path(ctx).exists()

    def run(self, ctx: PhaseContext) -> None:
        scenes = read_json(_scenes_json_path(ctx))
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "director1 phase: no finished scenes.json to build a structure from")

        scene_texts = [str(scene.get("text") or "") for scene in scenes if isinstance(scene, dict)]
        current_count = len(scene_texts)
        inputs = creative_inputs(ctx)

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning visual structure..."))
        prompt = build_director1_prompt(inputs, scene_texts, min_images=_MIN_IMAGES, max_images=_MAX_IMAGES)
        result = generate_text(prompt, "director1", config=ctx.router)

        try:
            parsed = extract_json_object(result.text)
        except ValueError as exc:
            raise AvgError(ErrorKind.bug, f"director1 phase: {exc}") from exc

        # The lower bound must never force image_count ABOVE the script's
        # own scene count (one image per scene is the default and almost
        # always correct - see the prompt) - a short script with fewer
        # scenes than `_MIN_IMAGES` must not get inflated past what
        # actually exists. `_MIN_IMAGES` only matters once there ARE at
        # least that many scenes to potentially collapse away from.
        min_bound = min(_MIN_IMAGES, current_count) if current_count > 0 else _MIN_IMAGES
        image_count = _coerce_int(parsed.get("image_count"))
        if image_count is None:
            image_count = current_count
        image_count = max(min_bound, min(_MAX_IMAGES, image_count))

        scenes_out = [
            normalized
            for entry in (parsed.get("scenes") or [])
            if (normalized := _normalize_scene_entry(entry)) is not None
        ]
        if not scenes_out:
            # Nothing usable survived - degrade to one safe default entry
            # per existing scene rather than writing a structure that omits
            # scenes Visuals (Phase 3) will still need to iterate over.
            scenes_out = [
                {"scene": i + 1, "role": "", "beat": "none", "visual_intent": "BACKGROUND"}
                for i in range(current_count)
            ]

        zoom_words = [
            normalized
            for entry in (parsed.get("zoom_words") or [])
            if (normalized := _normalize_zoom_word(entry)) is not None
        ]

        video_map = {
            "version": 1,
            "image_count": image_count,
            "scenes": scenes_out,
            "zoom_words": zoom_words,
            "notes": str(parsed.get("notes") or "").strip(),
        }
        write_json(script_dir(ctx) / "video_map.json", video_map)
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Video map complete."))
