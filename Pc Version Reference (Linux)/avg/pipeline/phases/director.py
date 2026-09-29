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
from avg.media.environment import ensure_filters_dir_env
from avg.media.vendor.avg import load_filter_catalog
from avg.pipeline.prompts import build_director1_prompt, build_director2_prompt, build_explainer_prompt, load_catalog
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


def _normalize_part_entry(entry: object, valid_scenes: set[int]) -> dict | None:
    """One `parts[]` entry from Director I's chapters-mode response,
    reduced to the flat shape `build_timeline` already reads
    (`{after_scene, title, sub, duration_sec}`) - or `None` if it names
    no valid scene, or its divider would land before scene 1. "Never
    split scene 1 away from its part" means part 1 never gets a takeover
    card - there is nothing before the video to insert one into."""
    if not isinstance(entry, dict):
        return None
    scenes_in = entry.get("scenes")
    if not isinstance(scenes_in, list):
        return None
    part_scenes = sorted({s for raw in scenes_in if (s := _coerce_int(raw)) is not None and s in valid_scenes})
    if not part_scenes:
        return None
    after_scene = part_scenes[0] - 1
    if after_scene <= 0:
        return None
    return {
        "after_scene": after_scene,
        "title": str(entry.get("title") or "").strip(),
        "sub": str(entry.get("sub") or "").strip(),
        "duration_sec": _coerce_float(entry.get("duration_sec"), _TAKEOVER_SECONDS),
    }


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
        chapters_enabled = bool(ctx.channel_values.get("chapters_enabled", False))

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning visual structure..."))
        prompt = build_director1_prompt(
            inputs,
            scene_texts,
            min_images=_MIN_IMAGES,
            max_images=_MAX_IMAGES,
            chapters_enabled=chapters_enabled,
        )
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

        parts_out: list[dict] = []
        if chapters_enabled:
            valid_scenes = set(range(1, current_count + 1))
            parts_out = [
                normalized
                for entry in (parsed.get("parts") or [])
                if (normalized := _normalize_part_entry(entry, valid_scenes)) is not None
            ]

        video_map = {
            "version": 1,
            "image_count": image_count,
            "scenes": scenes_out,
            "zoom_words": zoom_words,
            "parts": parts_out,
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

# Same anti-clutter budget `Get-AiDirectionPlan` applied to filter overlays:
# at most a quarter of the video's scenes may carry one, and no single
# filter name may cover more than two scenes, so a model that likes one
# filter can't saturate the whole video with it.
_FILTER_SCENE_FRACTION = 0.25
_FILTER_MAX_PER_NAME = 2

_ACCENT = "accent1"


def _filter_catalog() -> dict[str, str]:
    """`{name: description}` for every cataloged filter clip, or `{}` if
    none exist yet (empty catalogue is a clean no-op - see
    `avg.pipeline.prompts._filters_section`).

    `force=True` on every call: `load_filter_catalog`'s module-level cache
    lives in the protected vendor file and would otherwise return a stale
    catalogue read under a different `AVG_FILTERS_DIR` (a real risk across
    tests, which routinely monkeypatch the env var to a temp catalogue).
    """
    ensure_filters_dir_env()
    return {entry["name"]: entry["desc"] for entry in load_filter_catalog(force=True).values()}


def _eligible_filters(catalog: dict[str, str], allow: str, block: str) -> dict[str, str]:
    """`catalog` narrowed by the channel's `filters_allow`/`filters_block`
    CSV settings (case-insensitive; blank allow means "everything not
    blocked")."""
    allow_set = {n.strip().lower() for n in allow.split(",") if n.strip()}
    block_set = {n.strip().lower() for n in block.split(",") if n.strip()}
    eligible = {name: desc for name, desc in catalog.items() if name.lower() not in block_set}
    if allow_set:
        eligible = {name: desc for name, desc in eligible.items() if name.lower() in allow_set}
    return eligible




def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize_direction_entry(
    entry: object, grade: dict, valid_scenes: set[int], valid_filters: frozenset[str] = frozenset()
) -> dict | None:
    """One `scenes[]` entry from the model, sanitized against the plan's
    closed vocabularies, or `None` if it names no scene that actually
    exists in the finished script. `grade` is shared by every scene (the
    plan sets it once at top level - see module docstring).

    `filter` is CATALOGUE data, not a closed vocabulary like motion/ease/
    transition/grade - `valid_filters` is the eligible catalogue's own key
    set (`avg.pipeline.phases.director._eligible_filters`), not a hardcoded
    constant. An empty `valid_filters` (feature off, or nothing eligible)
    means the key is never added regardless of what the model said - the
    anti-clutter cap in `Director2Phase.run` still runs afterward and may
    drop it further."""
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

    result = {
        "scene": scene_num,
        "motion": {"type": mtype, "intensity": intensity, "ease": ease},
        "transition_in": transition_in,
        "grade": grade,
    }
    if valid_filters:
        filt = str(entry.get("filter") or "").strip().lower()
        if filt and filt in valid_filters:
            result["filter"] = filt
    return result


def _normalize_explainer_card(entry: object, valid_scenes: set[int]) -> dict | None:
    """One `cards[]` entry from the Explainer AI response, or `None` if it
    names no valid scene or has no text. Same shape `_build_card_plan`
    already uses for a Focused card - `element` fixed to `"explainer"`,
    since that is the only kind this planner ever produces."""
    if not isinstance(entry, dict):
        return None
    scene_num = _coerce_int(entry.get("scene"))
    if scene_num is None or scene_num not in valid_scenes:
        return None
    text = str(entry.get("text") or "").strip()
    if not text:
        return None
    return {"element": "explainer", "scene": scene_num, "text": text, "accent": _ACCENT}


def _cap_filter_use(scenes_out: list[dict], total_scenes: int) -> None:
    """Enforce the anti-clutter budget in place, walking scenes in order:
    at most `_FILTER_SCENE_FRACTION` of all scenes keep a filter, and at
    most `_FILTER_MAX_PER_NAME` scenes keep any one filter name. Excess
    entries lose the key entirely (not set to `None`) - matches every
    other optional per-scene field in this codebase, which is absent, not
    null, when there is nothing to say.

    `max(1, ...)`: a bare `int(total_scenes * _FILTER_SCENE_FRACTION)`
    floors to 0 for any video under 4 scenes, which would silently forbid
    a filter on every short video regardless of what Director II or the
    channel's filters_intensity asked for - a real conflict this plan's
    own test suite caught (test_direction_plan_carries_a_valid_per_scene_
    filter uses a single scene and expects its one valid filter to
    survive). The floor is a soft budget against clutter on longer videos,
    not a hard ban on shorter ones."""
    max_scenes = max(1, int(total_scenes * _FILTER_SCENE_FRACTION))
    per_name: dict[str, int] = {}
    used = 0
    for scene in scenes_out:
        name = scene.get("filter")
        if not name:
            continue
        if used >= max_scenes or per_name.get(name, 0) >= _FILTER_MAX_PER_NAME:
            del scene["filter"]
            continue
        per_name[name] = per_name.get(name, 0) + 1
        used += 1


def _build_card_plan(
    scenes: list[dict],
    *,
    takeover_cards_enabled: bool = True,
    explainer_cards: list[dict] | None = None,
) -> dict:
    """Deterministic Focused takeover cards from `((question))` markers,
    merged with any AI-planned Explainer cards the caller already fetched
    (`avg.pipeline.phases.director.Director2Phase.run`, via
    `avg.pipeline.prompts.build_explainer_prompt` - a separate AI call,
    not made here, so this stays a pure function). See module docstring.

    `takeover_cards_enabled=False` is the master switch: no card of any
    kind. The caller should skip the Explainer AI call entirely when this
    is off, rather than fetch cards this function would just discard -
    `Director2Phase.run` already does.

    One card per scene: when a Focused and an Explainer candidate land on
    the same scene, Focused wins - a deterministic, author-placed marker
    is a stronger editorial signal than an AI guess. Ported from
    `Export-CardPlan`'s own dedup (`D:\\AI_Video_Gen\\modules\\ai.ps1`).
    The frequency cap applies to the MERGED set, in insertion order
    (Focused cards are inserted first, so a Focused card always survives
    the cap over an Explainer one on a different scene, matching
    `Export-CardPlan`'s own ordered-dict behaviour). No `start`/`end` yet;
    Captions (Phase 6) adds those.
    """
    if not takeover_cards_enabled:
        return {"cards": []}

    by_scene: dict[int, dict] = {}
    for i, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        text = str(scene.get("text") or "")
        questions = extract_question_markers(text)
        if not questions:
            continue
        # One card per scene at most - a scene rarely needs two boards.
        by_scene[i] = {"element": "focused", "scene": i, "text": questions[0], "accent": _ACCENT}

    for card in explainer_cards or []:
        scene_num = card.get("scene")
        if scene_num not in by_scene:
            by_scene[scene_num] = card

    cards = list(by_scene.values())
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
        settings = ctx.channel_values
        brand_preset_id = str(settings.get("brand_preset") or "")
        preset_payload = None
        if brand_preset_id:
            option = load_catalog().get("brand_presets", {}).get(brand_preset_id)
            if option is not None:
                preset_payload = option.payload
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
            preset_payload,
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
        settings = ctx.channel_values

        filters_intensity = _coerce_int(settings.get("filters_intensity"))
        if filters_intensity is None:
            filters_intensity = 50
        filters_intensity = max(0, min(100, filters_intensity))
        eligible_filters = _eligible_filters(
            _filter_catalog(),
            str(settings.get("filters_allow") or ""),
            str(settings.get("filters_block") or ""),
        )

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Planning motion and grade..."))
        prompt = build_director2_prompt(
            inputs, scene_texts, filter_catalog=eligible_filters, filters_intensity=filters_intensity
        )
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

        valid_filters = frozenset(eligible_filters)
        scenes_out = [
            normalized
            for entry in (parsed.get("scenes") or [])
            if (normalized := _normalize_direction_entry(entry, grade, valid_scenes, valid_filters)) is not None
        ]
        if scenes_out:
            _cap_filter_use(scenes_out, len(scene_texts))
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
        card_plan_settings = ctx.channel_values
        takeover_cards_enabled = bool(card_plan_settings.get("takeover_cards_enabled", True))
        explainer_enabled = bool(card_plan_settings.get("explainer_enabled", False))

        explainer_cards: list[dict] = []
        if takeover_cards_enabled and explainer_enabled:
            max_cards = max(0, min(4, len(scene_texts) // 6))
            if max_cards > 0:
                try:
                    explainer_prompt = build_explainer_prompt(scene_texts, max_cards)
                    explainer_result = generate_text(explainer_prompt, "director2_explainer", config=ctx.router)
                    explainer_parsed = extract_json_object(explainer_result.text)
                    explainer_cards = [
                        normalized
                        for entry in (explainer_parsed.get("cards") or [])[:max_cards]
                        if (normalized := _normalize_explainer_card(entry, valid_scenes)) is not None
                    ]
                except Exception as exc:  # noqa: BLE001 - best-effort, matches _draw_overlay_assets below
                    message = f"Explainer cards could not be planned ({exc}); continuing without them."
                    ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=90, message=message))
                    explainer_cards = []

        card_plan = _build_card_plan(
            scenes, takeover_cards_enabled=takeover_cards_enabled, explainer_cards=explainer_cards
        )
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
