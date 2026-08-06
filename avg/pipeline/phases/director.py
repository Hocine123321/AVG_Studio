"""The two directing phases: Director I (id 2) and Director II (id 5).

Director I plans structure - how many images, which scene gets which role
and beat - before anything is generated. Director II plans motion, grade and
takeover cards over the images and narration that now exist. They sit at
opposite ends of the media phases (Visuals and Voice run between them) but
read and write the same `scenes.json`, and the normalisation rules for that
file are easiest to keep honest side by side.

`_scenes_json_path` and `_coerce_int` were byte-identical in both files and
are now defined once, above both sections.

── Director I ──────────────────────────────────────

Phase 2: Director I - the "map-maker."

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

── Director II ────────────────────────────────────

Phase 5: Director II / AI Graphics.

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
from pathlib import Path
from typing import Any

from avg.core.errors import AvgError, ErrorKind, report_degradation
from avg.core.events import ProgressEvent
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import (
    _FOCUS_MARKER_RE,
    _PAUSE_MARKER_RE,
    creative_inputs,
    extract_json_object,
    extract_question_markers,
    read_json,
    render_dir,
    resolve_font_family,
    script_dir,
    write_json,
)
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt
from avg.providers.text.router import generate as generate_text


# ── Shared ─────────────────────────────────────────

def _scenes_json_path(ctx: PhaseContext):
    return script_dir(ctx) / "scenes.json"


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ── Director I ───────────────────────────────────

_VALID_ROLES = {"hook", "tension", "reveal", "proof", "example", "pause", "hit", "cta"}
_VALID_BEATS = {"none", "HIT", "PAUSE", "ZOOM", "SLOWDOWN"}
_VALID_INTENTS = {"BACKGROUND", "ASSET", "POP"}

# Sensible bounds regardless of what the model proposes - mirrors the
# legacy app's own min/max clamp (there derived from target length; kept as
# fixed, generous bounds here since this task has no format-spec input yet).
_MIN_IMAGES = 3
_MAX_IMAGES = 160




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


#: A takeover card holds the screen this long when the plan does not say.
#: Matches the vendored engine's own default (`avg/media/vendor/avg.py:1992`),
#: so a node without a duration times identically here and there.
_TAKEOVER_SECONDS = 1.8


def build_timeline(scenes: list[Any], video_map: dict[str, Any]) -> list[dict[str, Any]]:
    """`script/timeline.json` - the scene and takeover structure.

    Consumed by `avg.media.vendor.avg.compute_scene_timings`, which reads
    `type`, a scene's `scene`, and a takeover's `headline`/`sub`/
    `duration_sec`. Takeovers come from the video map's `parts` (chapter
    boundaries); a map with no parts yields scenes only, because a takeover
    node nobody asked for is nearly two seconds of card inserted into the
    middle of the video.
    """
    parts_after: dict[int, list[dict[str, Any]]] = {}
    for part in video_map.get("parts") or []:
        if not isinstance(part, dict):
            continue
        after = _coerce_int(part.get("after_scene"))
        if after is not None and after > 0:
            parts_after.setdefault(after, []).append(part)

    timeline: list[dict[str, Any]] = []
    for position, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        number = _coerce_int(scene.get("index")) or position
        timeline.append({"type": "scene", "scene": number})
        for part in parts_after.get(number, ()):
            timeline.append(
                {
                    "type": "takeover",
                    "id": f"part-{number}",
                    "headline": str(part.get("title") or ""),
                    "sub": str(part.get("sub") or ""),
                    "duration_sec": float(part.get("duration_sec") or _TAKEOVER_SECONDS),
                }
            )
    return timeline


def build_markers(scenes: list[Any]) -> dict[str, Any]:
    """The three marker files, from the markers the writer left inline.

    The script drops `((question))`, `[[focus]]` and `[pause]` into its
    prose; `strip_delivery_markers` removes them again before anything is
    spoken or captioned, and until now that was the end of them. Extracting
    them here is what gives a Focused card a source, an emphasis window a
    position, and `remove_silences_core` a beat map saying which gaps are
    deliberate.

    `beat_map` uses the vendored engine's own vocabulary: a scene holding a
    deliberate pause is `PAUSE`, everything else `HIT`. Its keys are strings
    because the file is written as JSON and read back as JSON.
    """
    text_markers: list[dict[str, Any]] = []
    dynamic_pauses: list[dict[str, Any]] = []
    beat_map: dict[str, str] = {}

    for position, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        number = _coerce_int(scene.get("index")) or position
        text = str(scene.get("text") or "")

        for question in extract_question_markers(text):
            text_markers.append({"scene": number, "kind": "question", "text": question})
        for match in _FOCUS_MARKER_RE.finditer(text):
            text_markers.append({"scene": number, "kind": "focus", "text": match.group(1).strip()})

        pauses = len(_PAUSE_MARKER_RE.findall(text))
        for index in range(1, pauses + 1):
            dynamic_pauses.append({"scene": number, "index": index})
        beat_map[str(number)] = "PAUSE" if pauses else "HIT"

    return {"text_markers": text_markers, "dynamic_pauses": dynamic_pauses, "beat_map": beat_map}


@dataclass(frozen=True, slots=True)
class Director1Phase:
    id: int = 2
    key: str = "director1"
    name: str = "Director I"
    description: str = (
        "Plans what each scene should show and how many images it needs. Writes video_map.json."
    )

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
        sdir = script_dir(ctx)
        write_json(sdir / "video_map.json", video_map)

        # Structure and markers, both derived from the finished script plus
        # the map just written. Non-fatal in the old app and non-fatal here:
        # a missing marker file costs a card or an emphasis, a missing
        # timeline costs takeovers, and this phase's real output -
        # video_map.json - is already on disk by now.
        write_json(sdir / "timeline.json", build_timeline(scenes, video_map))
        markers = build_markers(scenes)
        write_json(sdir / "text_markers.json", markers["text_markers"])
        write_json(sdir / "dynamic_pauses.json", markers["dynamic_pauses"])
        write_json(sdir / "beat_map.json", markers["beat_map"])

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Video map complete."))


# ── Director II ─────────────────────────────────

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


#: How long a Focused card holds the screen when its own window is unknown.
_CARD_SECONDS = 2.5


def build_edit_plan(card_plan: dict[str, Any], direction_plan: dict[str, Any]) -> dict[str, Any]:
    """`render/edit_plan.json` - the same cards, in the shape the overlay
    compositor reads.

    `cmd_overlay_render` reads `plan["overlays"]` and, per entry, `element`,
    `scene`, `duration`, `transition_in`/`transition_out` and `ride_motion`
    (`avg/media/vendor/avg.py:3178-3190`). This is a TRANSLATION of
    `card_plan.json`, never a second source: that file stays the one place a
    card's text and accent are decided, so the panel the user reads and the
    overlay the engine draws cannot disagree.

    A card that already carries `start`/`end` (Screen Text resolves those)
    gets its real length; otherwise the default stands. `ride_motion` is on
    because a card pinned to a still frame while the image behind it moves
    looks like a bug - the engine only honours it when that scene actually
    zooms (`avg.py:3143`).
    """
    transitions = {
        int(scene["scene"]): str(scene.get("transition_in") or "fade")
        for scene in (direction_plan.get("scenes") or [])
        if isinstance(scene, dict) and _coerce_int(scene.get("scene")) is not None
    }

    overlays: list[dict[str, Any]] = []
    for card in card_plan.get("cards") or []:
        if not isinstance(card, dict):
            continue
        scene = _coerce_int(card.get("scene"))
        if scene is None:
            continue
        start, end = card.get("start"), card.get("end")
        duration = (
            float(end) - float(start)
            if isinstance(start, (int, float)) and isinstance(end, (int, float))
            else _CARD_SECONDS
        )
        overlays.append(
            {
                "element": str(card.get("element") or "focused"),
                "scene": scene,
                "text": str(card.get("text") or ""),
                "accent": str(card.get("accent") or ""),
                "duration": round(duration, 3),
                "transition_in": transitions.get(scene, "fade"),
                "transition_out": "fade",
                "ride_motion": True,
            }
        )
    return {"overlays": overlays}


def _write_scene_timings(ctx: PhaseContext, rdir: Path) -> None:
    """Write `render/scene_timings.json` from the timeline and audio map.

    The vendored timing pass is REUSED rather than reimplemented: it is the
    same function Render would otherwise run blind at encode time
    (`avg/media/vendor/avg.py:1964`), and a second implementation here would
    put images on one clock and captions on another. Writing it now is what
    lets Screen Text and the overlay pass plan without a frame existing.

    Non-fatal in every direction. Missing inputs, or a pass that raises,
    means Render derives the timings itself - exactly what happened before
    this file existed - and this phase's real output is already on disk.
    """
    from avg.media.vendor.avg import _load_audio_map, compute_scene_timings

    timeline = read_json(script_dir(ctx) / "timeline.json")
    audio_map_path = Path(ctx.project.folder) / "audio" / "scene_audio_map.json"
    if not isinstance(timeline, list) or not timeline or not audio_map_path.is_file():
        return

    try:
        audio_map = _load_audio_map(str(audio_map_path))  # type: ignore[no-untyped-call]
        if not audio_map:
            return
        total = float(sum(float(entry.get("duration") or 0.0) for entry in audio_map.values()))
        timings, _takeovers, _skipped = compute_scene_timings(  # type: ignore[no-untyped-call]
            timeline, audio_map, total, {},
            {}, {}, {}, {}, {},
            100, "flat",
        )
        write_json(rdir / "scene_timings.json", timings)
    except Exception as exc:  # noqa: BLE001 - non-fatal, see docstring
        message = f"Scene timings could not be precomputed ({exc}); the render will derive them."
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message=message))
        report_degradation(ctx, message)


def _draw_overlay_assets(ctx: PhaseContext, rdir: Path, edit_plan: dict[str, Any]) -> int:
    """Part 2: draw the cards and overlays as real PNGs.

    This is the step whose absence meant every finished video carried no
    card text - `card_plan.json` existed and nothing rendered it. Their
    existence (`render/_final_layers.json`) is also what lets the render's
    second part bake overlays and captions in ONE encode.

    Non-fatal: a card that cannot be drawn costs that card. The direction
    plan is already on disk by here, and it is the phase's real output.
    """
    from avg.media.overlays import write_layers
    from avg.media.vendor.avg import _target_size

    try:
        width, height = _target_size(ctx.project.aspect_ratio, ctx.project.short_edge)  # type: ignore[no-untyped-call]
        settings = ctx.channel.settings
        return write_layers(
            Path(ctx.project.folder),
            edit_plan,
            read_json(rdir / "scene_timings.json") or [],
            int(width),
            int(height),
            resolve_font_family(ctx),
            str(settings.get("overlay_accent1") or ""),
            str(settings.get("overlay_accent2") or ""),
            str(settings.get("overlay_highlight") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        message = f"Overlay assets could not be drawn ({exc}); the render will composite without cards."
        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message=message))
        report_degradation(ctx, message)
        return 0


@dataclass(frozen=True, slots=True)
class Director2Phase:
    id: int = 5
    key: str = "director2"
    name: str = "Director II / AI Graphics"
    description: str = (
        "Plans motion, grade and any takeover cards over the images. Writes edit_plan.json."
    )

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
        card_plan = _build_card_plan(scenes)
        write_json(rdir / "card_plan.json", card_plan)

        # ── Part 1 tail: exact per-scene windows, and the overlay
        # compositor's own view of the cards just planned.
        _write_scene_timings(ctx, rdir)
        edit_plan = build_edit_plan(card_plan, direction_plan)
        write_json(rdir / "edit_plan.json", edit_plan)

        drawn = _draw_overlay_assets(ctx, rdir, edit_plan)

        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id,
                percent=100,
                message=(
                    f"Direction plan complete ({len(scenes_out)} scene(s), grade={look}); "
                    f"{drawn} overlay asset(s) drawn."
                ),
            )
        )
