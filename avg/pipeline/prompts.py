"""Builds the prompt strings every AI-writing phase sends to a text model.

This is where video quality actually comes from: the brief, the script,
and the direction phases are only as good as the instructions handed to
the model. The wording below is ported from the legacy PowerShell app's
tuned prompts (`D:\\AI_Video_Gen\\modules\\ai.ps1` -
`Invoke-CreativeBrief`, `Invoke-NarrationDraft`, `Invoke-SceneSegmentation`,
and the Director II "style bible" section of the motion/grade prompt),
carried over phrase-for-phrase where the old app's phrasing was doing real
work, not paraphrased.

**The `visual_tone` trap** (the reason this module exists as a single
choke point rather than each phase formatting its own string): a channel
stores `visual_tone` as a catalogue id (e.g. `"documentary_cinematic"`),
but a prompt needs the ~40-word descriptive text behind that id (e.g.
"Documentary — cinematic realism — grounded cinematic realism, real
environments..."). The legacy app shipped a real bug where the raw id
reached the model - the AI received a meaningless slug instead of style
direction, and nobody noticed because the videos were merely *slightly*
worse. `resolve_visual_tone` is the ONLY function in this module that
reads `avg/assets/dynamic_options.txt`'s `video_tone` catalogue, and it is
the ONLY place any builder function below is allowed to turn
`CreativeInputs.visual_tone` into prompt text - no builder ever
interpolates `inputs.visual_tone` directly. See
`tests/pipeline/test_prompts.py`'s
`test_every_builder_routes_tone_through_the_single_resolver` for the
structural check.

This module builds strings only: no model call, no provider knowledge, no
I/O beyond reading the bundled catalogue once. `avg.pipeline.personality`
owns preset parsing; this module owns assembly.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from avg.pipeline.personality import PersonalityPreset, assets_dir, get_preset

_CATALOG_ASSET_NAME = "dynamic_options.txt"

# A bare catalogue id is lowercase snake_case with no spaces (the same
# shape `Resolve-VisualToneText` in the legacy app tests for) - anything
# else (spaces, punctuation, capital letters) is already human-written
# text, e.g. a `visual_tone: "custom"` free-typed description, and must be
# passed through unchanged rather than "resolved" into nonsense.
_ID_RE = re.compile(r"^[a-z0-9_]+$")

_CATEGORY_RE = re.compile(r"^##\s*CATEGORY:\s*(?P<category>\S+)\s*$")
_OPTION_RE = re.compile(r"^##\s*OPTION:\s*(?P<rest>.*)$")


@dataclass(frozen=True, slots=True)
class CatalogOption:
    """One `## OPTION: id | Label | hint` row from `dynamic_options.txt`.

    `payload` is `hint` re-parsed as `key=value;key=value` pairs, when it
    looks like that shape - `brand_presets`' hint carries a real colour/
    style payload (see `resolve_brand_style_bible`, `Task 8`'s
    `resolve_brand`), while every other category's hint is prose a `key=`
    token would never appear in. A malformed token (no `=`) is skipped,
    never raised - matches the legacy `Set-BrandPresetProperty` parser's
    own tolerance. Categories whose hint is prose degrade to `{}`."""

    id: str
    label: str
    hint: str
    payload: dict[str, str] = field(default_factory=dict)


def _parse_payload(raw: str) -> dict[str, str]:
    """`"key=value;key=value"` -> `{"key": "value", ...}`. A token with no
    `=` (ordinary prose, not a payload) is skipped rather than raising -
    this is what lets `_parse_catalog` call it unconditionally for every
    option's hint, prose or not."""
    payload: dict[str, str] = {}
    for token in raw.split(";"):
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key:
            payload[key] = value.strip()
    return payload


def _parse_catalog(raw: str) -> dict[str, dict[str, CatalogOption]]:
    """`{category: {option_id: CatalogOption}}` for every category in `raw`."""
    catalog: dict[str, dict[str, CatalogOption]] = {}
    current: str | None = None
    for line in raw.splitlines():
        cat_match = _CATEGORY_RE.match(line.strip())
        if cat_match:
            current = cat_match.group("category").strip()
            catalog.setdefault(current, {})
            continue
        opt_match = _OPTION_RE.match(line.strip())
        if opt_match and current is not None:
            parts = [p.strip() for p in opt_match.group("rest").split("|", 2)]
            if not parts or not parts[0]:
                continue
            opt_id = parts[0]
            label = parts[1] if len(parts) > 1 else opt_id
            hint = parts[2] if len(parts) > 2 else ""
            catalog[current][opt_id] = CatalogOption(
                id=opt_id, label=label, hint=hint, payload=_parse_payload(hint)
            )
    return catalog


@lru_cache(maxsize=1)
def _load_bundled_catalog() -> dict[str, dict[str, CatalogOption]]:
    path = assets_dir() / _CATALOG_ASSET_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse_catalog(raw)


def load_catalog(path: Path | None = None) -> dict[str, dict[str, CatalogOption]]:
    """The whole `dynamic_options.txt` catalogue (default: the bundled copy)."""
    if path is None:
        return dict(_load_bundled_catalog())
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse_catalog(raw)


def resolve_visual_tone(
    value: str,
    catalog: dict[str, dict[str, CatalogOption]] | None = None,
) -> str:
    """Expand a `visual_tone` catalogue id into its descriptive text.

    Mirrors the legacy `Resolve-VisualToneText`: a value that doesn't look
    like a bare id (contains spaces/punctuation/capitals) is already
    descriptive text and is returned unchanged - this covers `"custom"`
    free-typed tones. An id not found in the catalogue is also returned
    unchanged rather than raised or blanked, so an unrecognised/renamed
    tone still produces *some* prompt text instead of failing the whole
    phase. Blank input returns `""`.

    This is the ONLY function in this module (and, by convention, the only
    place any prompt builder here may call) that turns a `visual_tone`
    value into prompt text.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if not _ID_RE.match(v):
        return v  # already descriptive text
    options = (catalog or _load_bundled_catalog()).get("video_tone", {})
    opt = options.get(v)
    if opt is None:
        return v  # unknown id - degrade to the raw value, never raise
    return f"{opt.label} — {opt.hint}" if opt.hint else opt.label


def resolve_brand_preset_label(
    value: str,
    catalog: dict[str, dict[str, CatalogOption]] | None = None,
) -> str:
    """The human-readable label for a `brand_preset` catalogue id.

    Unlike `video_tone`, a brand preset's catalogue `hint` is a
    `key=value;...` colour payload for rendering, not prose for a model -
    so only the `label` (e.g. "Modern Dark — clean cinematic") is
    prompt-worthy. Same degrade-not-raise contract as `resolve_visual_tone`.
    """
    v = (value or "").strip()
    if not v:
        return ""
    options = (catalog or _load_bundled_catalog()).get("brand_presets", {})
    opt = options.get(v)
    if opt is None:
        return v
    return opt.label


# Ported from `$script:BRAND_STYLE_DEFAULTS`
# (`D:\AI_Video_Gen\modules\utilities.ps1`): a brand preset carries a full
# LOOK (motion feel, transition palette, colour grade), not just colours -
# this is what turns "changing the brand only recolours" into "a different
# brand feels like a different video." The five entries below are the
# legacy app's own, verbatim. The other five keys
# (wireframe_ink/tiktok_viral/vsauce_curiosity/academic_editorial/
# tactical_terminal) are presets this app added that legacy never had -
# profiles written to match each one's own palette/label in
# `avg/assets/dynamic_options.txt`, checked against the same closed
# `_VALID_TRANSITIONS`/`_VALID_GRADE_LOOKS` vocabularies
# `avg.pipeline.phases.director` validates a model's own choices against.
_BRAND_STYLE_DEFAULTS: dict[str, dict[str, str]] = {
    "modern_dark": {
        "motion": "balanced push-ins with occasional slow drifts",
        "transitions": "cut, crossfade",
        "grade": "cinematic",
    },
    "neon_cyber": {
        "motion": "punchy fast pushes and quick pans, high energy",
        "transitions": "cut, whip, slide_left",
        "grade": "vibrant",
    },
    "warm_organic": {
        "motion": "gentle slow drifts and soft zoom-ins, unhurried",
        "transitions": "crossfade, dip, fade",
        "grade": "warm",
    },
    "clean_light": {
        "motion": "minimal, precise, mostly slow zoom-ins",
        "transitions": "cut, fade",
        "grade": "soft",
    },
    "luxury_gold": {
        "motion": "slow, deliberate, elegant push-ins",
        "transitions": "crossfade, dip",
        "grade": "cinematic",
    },
    "wireframe_ink": {
        "motion": "minimal, precise, mostly static holds with slow pans",
        "transitions": "cut, fade",
        "grade": "none",
    },
    "tiktok_viral": {
        "motion": "fast punchy pushes and quick cuts, high energy",
        "transitions": "cut, whip, slide_left",
        "grade": "vibrant",
    },
    "vsauce_curiosity": {
        "motion": "measured pushes with the occasional quick zoom on a reveal",
        "transitions": "crossfade, cut",
        "grade": "cool",
    },
    "academic_editorial": {
        "motion": "minimal, precise, mostly slow zoom-ins",
        "transitions": "cut, fade",
        "grade": "soft",
    },
    "tactical_terminal": {
        "motion": "sharp fast pushes and quick pans, high-alert energy",
        "transitions": "cut, whip",
        "grade": "noir",
    },
}

_BRAND_STYLE_FALLBACK: dict[str, str] = {
    "motion": "balanced push-ins with occasional drifts",
    "transitions": "cut, crossfade",
    "grade": "none",
}


def resolve_brand_style_bible(
    value: str,
    catalog: dict[str, dict[str, CatalogOption]] | None = None,
) -> dict[str, str]:
    """`{"motion", "transitions", "grade"}` for a `brand_preset` id: the
    built-in profile in `_BRAND_STYLE_DEFAULTS` (or `_BRAND_STYLE_FALLBACK`
    for a blank/unrecognised id), overridden per-key by the preset's own
    catalogue payload when it carries `motion=`/`transitions=`/`grade=` -
    ported from `Get-BrandStyleProfile`
    (`D:\\AI_Video_Gen\\modules\\utilities.ps1`). Never returns a dict
    missing any of the three keys."""
    v = (value or "").strip()
    base = dict(_BRAND_STYLE_DEFAULTS.get(v, _BRAND_STYLE_FALLBACK))
    options = (catalog or _load_bundled_catalog()).get("brand_presets", {})
    opt = options.get(v)
    if opt is not None:
        for key in ("motion", "transitions", "grade"):
            override = opt.payload.get(key, "").strip()
            if override:
                base[key] = override
    return base


def _duration_phrase(seconds: int) -> str:
    """`"30 seconds"` / `"1 minute"` / `"3 minutes"` - matches the legacy
    `$durStr` computation exactly, including the `>= 120` (not `> 60`)
    threshold for the plural "s"."""
    if seconds >= 60:
        minutes = round(seconds / 60)
        suffix = "s" if seconds >= 120 else ""
        return f"{minutes} minute{suffix}"
    return f"{seconds} seconds"


@dataclass(frozen=True, slots=True)
class CreativeInputs:
    """Everything a prompt builder needs about one video's creative
    settings. Deliberately plain data (topic + the channel's creative
    fields + the format/length the caller has already resolved) rather
    than a `Channel`/`PhaseContext` reference - this module must not know
    about the pipeline runner or how a channel's settings dict is shaped,
    only the handful of values a prompt actually uses.

    `content_shape` and `visual_tone` are catalogue ids exactly as a
    channel stores them (`avg.core.settings_schema`) - never pre-resolved
    by the caller. Resolution happens once, inside the builder functions
    below, via `avg.pipeline.personality.get_preset` and
    `resolve_visual_tone`/`resolve_brand_preset_label` respectively.
    """

    topic: str
    content_shape: str = "documentary"
    visual_tone: str = "documentary_cinematic"
    style_lock: str = ""
    brand_preset: str = ""
    form_label: str = "Short-form (9:16)"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30


# ── Phase 0: Creative Brief ─────────────────────────────────────────────

def build_research_prompt(topic: str) -> str:
    """Brief part 1's prompt: the facts a short video about `topic` should be
    accurate about.

    Ported from the legacy Brief's `[Part 1] Researching the topic`
    (`D:\\AI_Video_Gen\\info.txt:245-252`), which cached its answer to
    `topic_research.txt` and let Phase 1 reuse it rather than paying for a
    second call.

    Plain prose, no JSON: the output is CONTEXT for a later prompt, and asking
    a model for structure it does not need is how a research call starts
    failing to parse for no benefit.
    """
    return (
        "You are a researcher briefing a scriptwriter.\n\n"
        f"TOPIC: {topic}\n\n"
        "List the facts a short video about this topic must get right: what is "
        "actually true, the specific names, numbers and dates that matter, and "
        "the common misconception a casual writer would repeat. Be concrete and "
        "brief - at most 200 words, plain prose, no headings, no preamble. If "
        "you are unsure of a detail, say so rather than inventing it."
    )


def build_brief_prompt(
    inputs: CreativeInputs,
    *,
    personality: dict[str, PersonalityPreset] | None = None,
) -> str:
    """Phase 0's prompt: the high-level creative direction (emotional arc,
    tone, angle) the writer follows. Ported from `Invoke-CreativeBrief`
    (the non-chaptered path - chapter/takeover-card planning is Director
    I's structural concern, not this prompt's).
    """
    preset = get_preset(inputs.content_shape, personality)
    content_label = preset.description or preset.name
    tone_text = resolve_visual_tone(inputs.visual_tone)
    dur_str = _duration_phrase(inputs.duration_seconds)
    tone_section = f"\nVisual tone: {tone_text}" if tone_text else ""

    return f"""You are a creative director setting the CREATIVE DIRECTION for a short {dur_str} video on this topic: "{inputs.topic}"
Content personality: {content_label}{tone_section}
Do NOT plan individual shots, beats, per-scene roles, or visual intent — that happens later, after the script is written.
The writer (next phase) will mark high-impact FOCUS words and rhetorical QUESTIONS inline so they can be emphasised on screen — keep the emotional_arc and angle punchy enough to give them strong words and hooks to mark.
Decide only the high-level creative intention the writer will follow:
- emotional_arc: one sentence describing the viewer's journey hook→tension→payoff→cta
- tone: a short phrase for the overall tone/energy of the whole video (e.g. "urgent and investigative", "warm and playful")
- angle: the specific angle or thesis this video takes on the topic — what makes THIS telling of it land

Return ONLY a JSON object, no preamble, no markdown fences:
{{
  "emotional_arc": "<string>",
  "tone": "<string>",
  "angle": "<string>",
  "notes": "<optional one-line rationale, logged not rendered>"
}}"""


# ── Phase 1 / Pass 1: narration draft ───────────────────────────────────

_PAUSE_FREQUENCY_GUIDE: dict[str, str] = {
    "low": "SPARINGLY — only at the one or two most dramatic beats in the whole script",
    "high": "LIBERALLY — at every punchy, dramatic beat, several times across the script",
}
_PAUSE_FREQUENCY_DEFAULT = "occasionally — at natural dramatic beats, roughly once every couple of scenes"

MARKER_SECTION = """
=== TEXT MARKERS (focus + questions) ===
Wrap the SINGLE most impactful word or short phrase in each Script: line in DOUBLE SQUARE
brackets, e.g. "These coins are [[worthless]] today". Mark 1-2 per scene MAX — only the word a
viewer should lock onto. These become on-screen highlights, so pick words that are actually
spoken.
Separately, wrap any short rhetorical QUESTION or punchy hook in DOUBLE PARENTHESES, e.g.
"((But why?))" or "((How is this even possible?))". These are isolated for emphasis cards, so
only wrap genuine brief questions/hooks — never a whole sentence.
The two marker types must NOT overlap or nest. The narration still reads naturally — the
brackets are invisible cues, not spoken, and the word count ignores them.
"""


def build_pause_section(frequency: str = "medium") -> str:
    """The "insert [pause] markers" instructions block, ported verbatim
    from `Invoke-ScriptAndPrompts`'s dynamic-pauses section (only
    `$freqGuide` varies)."""
    freq_guide = _PAUSE_FREQUENCY_GUIDE.get(frequency, _PAUSE_FREQUENCY_DEFAULT)
    return f"""
=== DYNAMIC PAUSES (human delivery) ===
Insert the literal marker [pause] INLINE inside Script: lines where a spoken dramatic pause
would land an emotional beat — before a reveal, after a bold claim, or to let a word breathe.
Place [pause] BETWEEN words, e.g. "These inventions are LITERALLY [pause] 2000 years old" or
"Yes [pause] that is true". Use them {freq_guide}. Never put a [pause] at the very start or end
of a scene, and never two in a row. They are SILENT beats — the narration must still read
naturally and the word count above counts every real word (ignore the markers).
"""


# Calibrated to reproduce this app's pre-Slice-21 fixed 60-75 word budget
# exactly at the 30s preset (30 * 2.0 = 60, 30 * 2.5 = 75) - not a
# different pace picked from scratch. See
# docs/superpowers/specs/2026-08-11-slice-21-duration-narration-design.md
# §1 for why the old fixed budget was the actual cause of 2m/4m
# collapsing to ~30s.
_WORDS_PER_SECOND_MIN = 2.0
_WORDS_PER_SECOND_MAX = 2.5


def _word_budget_for_duration(duration_seconds: int) -> tuple[int, int]:
    """The Pass 1 narration prompt's word-count target for a given spoken
    length. Was a fixed 60-75 words regardless of `duration_seconds`
    before this - correct only for the 30s preset it was written against.
    """
    return (
        max(1, round(duration_seconds * _WORDS_PER_SECOND_MIN)),
        max(1, round(duration_seconds * _WORDS_PER_SECOND_MAX)),
    )


def build_narration_prompt(
    inputs: CreativeInputs,
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    word_min: int | None = None,
    word_max: int | None = None,
    pause_frequency: str | None = "medium",
    include_markers: bool = True,
    extra_context: str = "",
) -> str:
    """Phase 1 / Pass 1's prompt: flowing narration prose, no scene grid.

    Ported from `Invoke-NarrationDraft`. `extra_context` is an opaque slot
    for whatever additional context blocks a caller has already built
    (topic research, a rejected-draft note, brief creative direction,
    few-shot examples, house style rules) - this module has no opinion on
    those subsystems and just places the text where the old prompt's
    `=== SYSTEM RULES ===` section did.

    `word_min`/`word_max` default to `_word_budget_for_duration(inputs.
    duration_seconds)` when not given - pass them explicitly only to
    override that calibration (as one test intentionally does).
    """
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    tone_line = f"\nTone & style: {tone_text} — apply this to the narration voice." if tone_text else ""
    dur_str = _duration_phrase(inputs.duration_seconds)
    if word_min is None or word_max is None:
        default_min, default_max = _word_budget_for_duration(inputs.duration_seconds)
        word_min = default_min if word_min is None else word_min
        word_max = default_max if word_max is None else word_max

    pause_section = build_pause_section(pause_frequency) if pause_frequency else ""
    marker_section = MARKER_SECTION if include_markers else ""
    extra_block = f"\n{extra_context}" if extra_context else ""

    return f"""{preset.body}

=== SYSTEM RULES ==={extra_block}

=== TASK ===
Topic: "{inputs.topic}"
Video format: {inputs.form_label}
Total spoken length: {dur_str}{tone_line}

Write the NARRATION ONLY — the exact words a narrator will speak, start to finish.

This is prose, not a storyboard. Do NOT include any of:
- scene numbers, timestamps, or [MM:SS] markers
- image prompts, shot descriptions, or art direction
- headings, labels, bullet points, or any other structural scaffold

Requirements:
- Total length: {word_min} to {word_max} words.
- Hook in the very first sentence — no warmup, no throat-clearing.
- It must read as ONE continuous piece with a real throughline: an idea that builds,
  turns, and pays off. NOT a stack of related facts that happen to share a topic.
- VARY THE SENTENCE RHYTHM ON PURPOSE. A reveal or a punchline gets a short, blunt line.
  A mechanism or a piece of context gets a longer, flowing one. The sentences must NOT all
  come out the same length — that uniformity is the single most common failure here, and
  it is the thing you are being asked to avoid.
- Write for the ear. This is read aloud, so prefer plain spoken phrasing over
  written-essay constructions.
{pause_section}{marker_section}
Return ONLY the narration text. Zero preamble, commentary, markdown, or code fences."""


# ── Phase 1 / Pass 2: scene segmentation + image prompts ───────────────

def build_scene_count_rule(min_scenes: int, max_scenes: int, exact_scene_count: int = 0) -> str:
    """Ported verbatim from `Invoke-SceneSegmentation`'s `$countRule`."""
    if exact_scene_count > 0:
        return f"Produce EXACTLY {exact_scene_count} scenes — no more, no fewer. This count is required."
    return f"""Decide the number of scenes from the CONTENT, not from arithmetic. Cut where the narration
naturally turns — a new idea, a reveal, a shift in subject. It must land between {min_scenes} and
{max_scenes} scenes. A dense explanatory passage may run long in one scene; a punchy reveal may be
a scene on its own. Do NOT make every scene the same length — uneven scenes are correct here."""


def build_scene_segmentation_prompt(
    inputs: CreativeInputs,
    narration: str,
    *,
    min_scenes: int = 3,
    max_scenes: int = 15,
    exact_scene_count: int = 0,
    extra_style_guide: str = "",
    recycle_pool: list[dict] | None = None,
    recycle_ratio: int = 0,
) -> str:
    """Phase 1 / Pass 2's prompt: split the finished narration into scenes
    and write one image prompt per scene — or, for a scene a pool image
    genuinely fits, the canonical `[RECYCLE #k]` token instead. Ported
    from `Invoke-SceneSegmentation` (`D:\\AI_Video_Gen\\modules\\ai.ps1`),
    including its pool-stitching path.

    Mode is derived here, not passed in as a separate flag (legacy's
    `RecycleMode` parameter): `"off"` when `recycle_pool` is empty/`None`
    or `recycle_ratio <= 0`, `"all"` at `>= 100` (every scene must
    recycle, no prompts written at all), else `"partial"` (the model's own
    judgement, guided by `recycle_ratio`). `"off"` mode's prompt is
    BYTE-IDENTICAL to before this parameter existed — pool wording only
    ever appears when there is something to stitch.

    `extra_style_guide` is an opaque slot (mirrors `SceneCtx` in the old
    prompt, which came from a `scene_descriptions.md` context file this
    task has no copy of) for any additional visual-style prose a caller
    wants appended after the resolved `visual_tone` text.
    """
    tone_text = resolve_visual_tone(inputs.visual_tone)
    style_lock = inputs.style_lock.strip()
    style_tail = f" Append this style tag verbatim at the end: {style_lock}" if style_lock else ""
    count_rule = build_scene_count_rule(min_scenes, max_scenes, exact_scene_count)

    style_guide_lines = [f"Visual tone: {tone_text}"] if tone_text else []
    if extra_style_guide:
        style_guide_lines.append(extra_style_guide)
    style_guide = "\n".join(style_guide_lines) if style_guide_lines else "No specific visual style constraints."

    pool = recycle_pool or []
    use_pool = bool(pool) and recycle_ratio > 0
    mode = "all" if (use_pool and recycle_ratio >= 100) else ("partial" if use_pool else "off")

    pool_section = ""
    job_line = "Below is a finished narration. Split it into scenes and write one image prompt per scene."
    image_rule = (
        f"- an Image line: ONE concrete visual subject suited to a {inputs.aspect_ratio} frame. Describe what\n"
        f"  is literally seen — no metaphors, no text-in-image.{style_tail}\n"
    )
    pool_rules = ""
    image_schema_hint = "<the image prompt>"

    if mode != "off":
        pool_lines = "\n".join(f"#{entry['index']}: {entry['description']}" for entry in pool)
        pool_section = f"\n\n=== RE-USABLE IMAGE POOL (#k : description) ===\n{pool_lines}\n"
        image_schema_hint = "<the image prompt, or [RECYCLE #k]>"
        pool_rules = (
            "- Only use image numbers (#k) that appear in the pool above.\n"
            "- The same pool image MAY serve more than one scene; each scene gets at most one.\n"
        )
        if mode == "all":
            job_line = (
                "Below is a finished narration. Split it into scenes and assign each scene a re-usable image."
            )
            image_rule = (
                "- an Image line: ALWAYS the token [RECYCLE #k] naming the pool image that fits this scene best.\n"
                "  This video re-uses ONLY pooled images — no new images will be generated — so EVERY scene must\n"
                "  get a [RECYCLE #k]. When nothing fits well, still pick the nearest. Never write a description.\n"
            )
        else:
            image_rule = (
                "- an Image line, which is ONE of:\n"
                "    [RECYCLE #k]  — when a pool image GENUINELY fits this scene. Re-using a good match saves\n"
                "                    generation, but a FORCED match looks worse than a fresh image, so only\n"
                f"                    re-use on a strong, obvious fit. Re-use at most {recycle_ratio}% of the scenes.\n"
                f"    a prompt      — otherwise: ONE concrete visual subject suited to a {inputs.aspect_ratio} frame,\n"
                f"                    describing what is literally seen. No metaphors, no text-in-image.{style_tail}\n"
                "  Decide this scene by scene, in order, from the first scene to the last.\n"
            )
            job_line = (
                "Below is a finished narration. Split it into scenes, re-use pool images where they genuinely "
                "fit, and write an image prompt for every other scene."
            )

    return f"""=== VISUAL STYLE GUIDE ===
{style_guide}{pool_section}

=== TASK ===
Topic: "{inputs.topic}"
Image aspect ratio: {inputs.aspect_ratio}

{job_line}

{count_rule}

ABSOLUTE RULE — the narration is FINAL:
- Reproduce every word EXACTLY as written, in the original order.
- Do NOT reword, rewrite, shorten, expand, summarise, or fix anything.
- Do NOT add or drop a single word.
- Preserve any [pause], [[double square]] and ((double parenthesis)) markers exactly where
  they appear. They are delivery cues, not typos.
- Concatenating your scene texts in order must reproduce the narration verbatim.

For each scene also write:
{image_rule}- a Label: one or two words naming the scene's job (Hook, Reveal, Context, Payoff, ...).
{pool_rules}
Output EXACTLY this structure for every scene, in order, and nothing else:

@@SCENE@@
<the verbatim narration words for this scene>
@@IMAGE@@
{image_schema_hint}
@@LABEL@@
<the label>

No preamble, no commentary, no markdown, no code fences, no numbering.

=== NARRATION TO SPLIT ===
{narration}"""


# ── Phase 2 (Director I): visual structure ──────────────────────────────

def build_director1_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    min_images: int = 3,
    max_images: int = 160,
    chapters_enabled: bool = False,
) -> str:
    """Phase 2 (Director I)'s prompt: the finished, scene-segmented script is
    FINAL - this decides the video's visual STRUCTURE around it (how many
    distinct images the story needs, each scene's role/beat/visual-intent,
    which words deserve zoom emphasis, and - when `chapters_enabled` -
    how scenes group into named chapters). Ported from `Get-AiVideoMap`
    (`D:\\AI_Video_Gen\\modules\\ai.ps1`), simplified: per-scene filter
    assignment is a separate concern (Director II owns filters).

    `scene_texts` is each scene's verbatim narration, in order (from
    `avg.pipeline.phases.writing`'s `scenes.json`) - the numbered list the
    model classifies is built from these, never re-derived from raw
    narration, so Director I always sees exactly the scenes the render will
    actually use.

    `chapters_enabled=False` produces a BYTE-IDENTICAL prompt to before
    this parameter existed - the chapters section and schema field only
    ever appear when there's something for the model to plan.
    """
    numbered = "\n".join(f'[{i + 1}] "{text}"' for i, text in enumerate(scene_texts))
    current_count = len(scene_texts)

    chapters_section = ""
    parts_schema = ""
    if chapters_enabled and current_count > 0:
        est_parts = max(2, min(4, math.ceil(current_count / 10)))
        chapters_section = f"""

CHAPTER STRUCTURE (this video uses chapters):
- Group the {current_count} scenes into {est_parts} sequential PARTS, each covering one sub-topic.
  Split where the sub-topic genuinely changes, not every N scenes. Never split scene 1 away
  from its part; never put a part break right before the final scene. Each part opens with a
  full-screen takeover card (title + short subtitle) shown BEFORE its first scene.
- Chapter title: max 4 words, states the sub-topic plainly. Subtitle: max 6 words, rephrases
  the title as a hook, not a restatement.
- Also return a "parts" array (see schema)."""
        parts_schema = (
            ',\n "parts":[{"part":1,"title":"<max 4 words>","sub":"<max 6 words>",'
            '"scenes":[<scene numbers>],"duration_sec":1.8}]'
        )

    return f"""You are the STRUCTURE DIRECTOR for a short narrated video built from still images.
The script below is FINAL. Your job is to decide its VISUAL STRUCTURE: how many
distinct images the story needs, how each scene should feel, and what kind of image
each scene wants.

Personality: {inputs.content_shape}. Total length: ~{inputs.duration_seconds} seconds ({inputs.form_label}).
The script has {current_count} scene(s). ONE image per scene is the default and almost
always correct — the scene count reflects the content's own natural pacing, so scenes
are intentionally uneven in length.

SCRIPT (numbered narration lines):
{numbered}

Decide:
- image_count: how many distinct images tell THIS story. KEEP THIS EQUAL to {current_count}
  (one image per scene) unless the script is CLEARLY mis-sized for its length — only then
  adjust. Must be between {min_images} and {max_images}. Do NOT change it for stylistic
  preference; a changed count triggers a full script rewrite, so only diverge from
  {current_count} when the script is genuinely broken.
- scenes: one entry per EXISTING script scene (exactly {current_count}, indexed 1..{current_count}), each with:
    - scene: the scene number
    - role: hook | tension | reveal | proof | example | pause | hit | cta
    - beat: none | HIT | PAUSE | ZOOM | SLOWDOWN  (HIT = hard punch; PAUSE = held breath;
      ZOOM = one key word deserves zoom emphasis; SLOWDOWN = lower energy; none = normal)
    - visual_intent: BACKGROUND | ASSET | POP
        BACKGROUND = full-frame scene backdrop (most scenes)
        ASSET = a floating graphic/icon/diagram composited over a clean background
        POP = a bold full-frame impact graphic that cuts in hard
- zoom_words: array of {{scene, word}} — for each ZOOM-beat scene, the single word to emphasize.
{chapters_section}

Return ONLY a JSON object, no preamble, no markdown fences:
{{"version":1,"image_count":<int>,
 "scenes":[{{"scene":1,"role":"hook","beat":"HIT","visual_intent":"POP"}}],
 "zoom_words":[{{"scene":<int>,"word":"<string>"}}]{parts_schema},
 "notes":"one line on your structural reasoning"}}"""


# ── Phase 5 (Director II / AI Graphics): style bible ────────────────────

def build_director_style_bible(
    inputs: CreativeInputs,
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    emotional_arc: str = "",
) -> str:
    """The "VIDEO STYLE (the creative bible)" block Director II's
    motion/grade prompt opens with, ported from the corresponding section
    of `ai.ps1`'s Director 2 prompt builder, including the per-brand
    motion-feel table (`resolve_brand_style_bible`)."""
    preset = get_preset(inputs.content_shape, personality)
    tone_text = resolve_visual_tone(inputs.visual_tone)
    brand_label = resolve_brand_preset_label(inputs.brand_preset)
    brand_section = ""
    if brand_label:
        bible = resolve_brand_style_bible(inputs.brand_preset)
        brand_section = (
            f"\n- brand: {brand_label}"
            f"\n- brand motion feel: {bible['motion']}"
            f"\n- brand transitions: {bible['transitions']}"
            f"\n- brand grade: {bible['grade']}"
        )

    return f"""VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
- personality: {preset.description or preset.name}
- visual tone: {tone_text}
- emotional arc: {emotional_arc}{brand_section}"""


def _filters_section(filter_catalog: dict[str, str], intensity: int) -> str:
    """The optional "AVAILABLE FILTERS" block `build_director2_prompt`
    appends - empty (never rendered) when there is nothing to offer, so a
    channel with `filters_intensity == 0` or an empty/fully-blocked
    catalogue gets a byte-identical prompt to before this feature existed.
    Frequency wording mirrors the three-tier phrasing `Get-AiDirectionPlan`
    used for the same `Config.edit.filters.intensity` value.

    `intensity <= 0` is a hard "off entirely" per the setting's own help
    text, not merely the sparsest tier of "sparingly" wording - the plan's
    first draft only varied the frequency phrase by intensity and never
    actually suppressed the section at 0, which is a real, user-visible
    difference: an "off" channel calling out available filters at all
    invites the model to use one anyway."""
    if not filter_catalog or intensity <= 0:
        return ""
    if intensity >= 75:
        frequency = "freely — use one on any scene that would benefit"
    elif intensity >= 40:
        frequency = "occasionally — only on scenes where it clearly earns its place"
    else:
        frequency = "sparingly — only on the one or two scenes that most need it"
    lines = "\n".join(f"- {name}: {desc}" for name, desc in filter_catalog.items())
    return f"""

AVAILABLE FILTERS (optional overlay clips baked over the finished scene — use them {frequency}):
{lines}
For a scene that should carry one, add "filter": "<name>" (the exact catalog name, lowercase). Most scenes should have none."""


def build_director2_prompt(
    inputs: CreativeInputs,
    scene_texts: list[str],
    *,
    personality: dict[str, PersonalityPreset] | None = None,
    emotional_arc: str = "",
    filter_catalog: dict[str, str] | None = None,
    filters_intensity: int = 50,
) -> str:
    """Phase 5 (Director II)'s motion/grade prompt: opens with
    `build_director_style_bible`'s style-bible fragment (now including the
    brand's own motion-feel/transitions/grade bias), then asks for a
    per-scene camera move/transition and one overall colour grade for the
    whole video, and - when `filter_catalog` is non-empty - an optional
    per-scene filter overlay clip. Ported from the corresponding section of
    `Get-AiDirectionPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`).

    The vocabulary below (motion types, ease curves, transitions, grade
    looks) is deliberately a closed set - `avg.pipeline.phases.director`
    sanitizes the reply against these exact same names, degrading anything
    else to a safe default, mirroring the legacy app's own validated
    lists."""
    style_bible = build_director_style_bible(inputs, personality=personality, emotional_arc=emotional_arc)
    numbered = "\n".join(f'[{i + 1}] "{text}"' for i, text in enumerate(scene_texts))
    filters_section = _filters_section(filter_catalog or {}, filters_intensity)
    scene_filter_field = ',"filter":"..."' if filters_section else ""

    return f"""You are the DIRECTOR / EDITOR of a short AI-generated video built from still images with
narration. Think like a bold human editor: your goal is to make THIS video feel special and
intentional — a directed, edited piece, not images sliding by. You have real creative
authority over pacing, camera movement, transitions and colour. Use contrast (calm vs.
punchy scenes) and motion that rides the narration's energy — but stay coherent.

{style_bible}

SCENES (number, narration):
{numbered}

For EACH scene choose:
- motion.type: zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down | still
    (use 'still' rarely, only for a deliberate held beat.)
- motion.intensity: 0.0-1.0 (higher = stronger move; push harder on high-energy moments,
    gentler on calm/reflective lines. Avoid a flat 0 everywhere.)
- motion.ease: ease_out_cubic | ease_in_out | ease_in | ease_out | linear
    (ease_out_cubic settles nicely; use it as the default.)
- transition_in: cut | fade | crossfade | dip | slide_left | slide_right | whip
    (cut on rhythm; crossfade/dip on reflective moments; slide/whip on an energetic jump.
     The FIRST scene should be "fade".)

Also choose ONE overall grade (colour look) for the whole video that fits the style bible:
- grade.look: none | warm | cool | cinematic | noir | vibrant | soft
The SAME grade applies to every scene (set it once at top level).
{filters_section}

Return ONLY a JSON object, no preamble, no markdown fences. Schema:
{{"version":1,"grade":{{"look":"..."}},
 "scenes":[{{"scene":<int>,"motion":{{"type":"...","intensity":0.5,"ease":"ease_out_cubic"}}{scene_filter_field},"transition_in":"..."}}]}}
Every scene number above MUST appear exactly once."""


def build_explainer_prompt(scene_texts: list[str], max_cards: int) -> str:
    """The Explainer takeover card planner: picks a FEW scenes whose
    narration states a deeper concept worth a full-screen pause. Ported
    from `Get-AiEditPlan` (`D:\\AI_Video_Gen\\modules\\ai.ps1`) - an AI
    pick, independent of the deterministic Focused cards
    (`avg.pipeline.phases.director._build_card_plan`), which come from
    `((question))` markers instead. `max_cards` is the caller's own
    budget (`avg.pipeline.phases.director.Director2Phase.run`:
    `max(0, min(4, scene_count // 6))`) - stated in the prompt so the
    model self-limits, not enforced here.
    """
    numbered = "\n".join(f"[{i + 1}] {text}" for i, text in enumerate(scene_texts))
    return f"""You are an editor deciding where to drop a full-screen EXPLAINER CARD — a centred "board" that
covers the video for a moment to let the viewer absorb a deeper CONCEPT, definition, or summary
spoken in that scene. Use them RARELY (at most {max_cards} in this whole video), only where a scene
states a genuinely important idea worth pausing on. Most videos need ZERO.

SCENES (number + narration):
{numbered}

For each chosen scene, "text" is a SHORT on-card phrasing of the concept (max 8 words) — it will be
shown centred and synced to the voice, so keep it to the words actually spoken there.

Return ONLY JSON, no preamble/fences: {{"cards":[{{"scene":<int>,"text":"<max 8 words>"}}]}}
If no scene warrants a board, return {{"cards":[]}}."""
