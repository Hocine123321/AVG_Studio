"""Internal helpers shared across the pipeline phases: defensive parsing of
model replies, and the on-disk `<project>/script/` conventions the phases
hand work off to one another through.

Both halves are private to `avg.pipeline.phases` (hence the leading
underscore) and both are pure helpers - no provider calls, no phase
sequencing, no `PhaseRecord` bookkeeping. `avg.export.capcut` also reaches
in for `read_json`/`strip_delivery_markers`, the one caller outside this
package.

── Parsing Utilities ───────────────────────────────────────

Defensive parsing of a text model's reply, shared by the AI-writing
phases.

A model's response is untrusted input: it will sometimes wrap valid JSON in
a markdown fence, add prose before/after it, or return the segmentation
block format with a piece missing. Every function here either returns a
usable value or raises a plain `ValueError` with a message a phase can wrap
directly into an `AvgError(ErrorKind.bug, ...)` - never a bare
`json.JSONDecodeError`/`KeyError`/`IndexError` a caller would have to
inspect to explain.

Ported from the legacy app's own defensive parsing
(`D:\\AI_Video_Gen\\modules\\ai.ps1`): `Invoke-CreativeBrief`'s fence-strip +
outermost-brace extraction, and `Invoke-SceneSegmentation`'s
`@@SCENE@@`/`@@IMAGE@@`/`@@LABEL@@` block splitter.

── Shared Helpers ──────────────────────────────────────────

Shared, tiny helpers for the AI-writing phases: turning a `PhaseContext`
into `avg.pipeline.prompts.CreativeInputs`, and the on-disk `<project>/script/`
JSON/text conventions those phases hand work off to one another and to
Director II/Render later.

A channel stores `length_preset`/`video_form` as catalogue ids
(`avg.core.settings_schema`), not the seconds/label a prompt needs - the
same "id vs. prompt text" shape as `visual_tone` (see
`avg.pipeline.prompts`'s module docstring), so it is resolved once here
rather than three times, one per phase.

File writes go under `<project.folder>/script/` (already created empty by
`avg.pipeline.create.new_project` - see `_PROJECT_DIRS` there), matching
this app's "grouped, not flat, project folder" convention (the legacy
app's own `script\\brief.json` / `script\\video_map.json` layout under
`D:\\AI_Video_Gen`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from avg.pipeline.phase import PhaseContext
from avg.pipeline.prompts import CreativeInputs


# ── Parsing Utilities ───────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9]*\s*(.*?)```\s*$", re.DOTALL)
_SCENE_SPLIT_RE = re.compile(r"^\s*@@SCENE@@\s*$", re.IGNORECASE | re.MULTILINE)
_IMAGE_SPLIT_RE = re.compile(r"^(.*?)^\s*@@IMAGE@@\s*$(.*)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_LABEL_SPLIT_RE = re.compile(r"^(.*?)^\s*@@LABEL@@\s*$(.*)", re.IGNORECASE | re.MULTILINE | re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Unwrap a single ```...``` fence wrapping the WHOLE reply, if present.

    Models are told never to fence their reply and sometimes do it anyway;
    a fence around only PART of the reply (rare, and not what the legacy
    app guarded against either) is left alone."""
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text


def extract_json_object(text: str) -> dict:
    """Parse the outermost `{...}` JSON object out of a model reply that may
    carry a markdown fence or leading/trailing prose around it.

    Raises `ValueError` (never a raw `json.JSONDecodeError`) if no `{`/`}`
    pair is found, the slice between them doesn't parse, or it parses to
    something other than a JSON object (e.g. a bare list or number)."""
    cleaned = strip_code_fence(text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model response")
    candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model response's JSON was not an object")
    return parsed


def _collapse_to_one_line(text: str) -> str:
    """Join a (possibly multi-line) block back into a single spoken/prompt
    line: trim each line, drop blanks, collapse repeated internal
    whitespace - mirrors the legacy parser's identical collapse step."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return re.sub(r"\s{2,}", " ", " ".join(lines)).strip()


def parse_scene_blocks(text: str) -> list[dict[str, str]]:
    """Parse Pass 2's `@@SCENE@@` / `@@IMAGE@@` / `@@LABEL@@` block format
    into `[{"text": ..., "image_prompt": ..., "label": ...}, ...]`, in
    order.

    Never raises on malformed input - a block with no `@@IMAGE@@` marker is
    skipped (the same tolerance the legacy parser applies), a missing
    `@@LABEL@@` degrades to an empty label, and a reply with no recognisable
    blocks at all simply returns `[]`. The caller decides whether an empty
    result is fatal (it is, for Pass 2 - see `avg.pipeline.phases.writing`)."""
    cleaned = strip_code_fence(text)
    chunks = _SCENE_SPLIT_RE.split(cleaned)
    scenes: list[dict[str, str]] = []
    for chunk in chunks[1:]:  # chunks[0] is any preamble before the first marker
        m_img = _IMAGE_SPLIT_RE.match(chunk)
        if not m_img:
            continue
        scene_text = _collapse_to_one_line(m_img.group(1))
        rest = m_img.group(2)

        m_lbl = _LABEL_SPLIT_RE.match(rest)
        if m_lbl:
            image_prompt = _collapse_to_one_line(m_lbl.group(1))
            label_lines = [ln.strip() for ln in m_lbl.group(2).strip().splitlines() if ln.strip()]
            label = label_lines[0] if label_lines else ""
        else:
            image_prompt = _collapse_to_one_line(rest)
            label = ""

        if not scene_text:
            continue
        scenes.append({"text": scene_text, "image_prompt": image_prompt, "label": label or "Scene"})
    return scenes


# ── Shared Helpers ────────────────────────────

# Matches the settings catalogue's `length_preset` choices
# (`avg.core.settings_schema`'s `SettingSpec(id="length_preset", ...)`).
_LENGTH_PRESET_SECONDS: dict[str, int] = {
    "30s": 30, "60s": 60, "90s": 90, "2m": 120, "4m": 240, "8m": 480,
}
_DEFAULT_LENGTH_SECONDS = 30

_FORM_LABELS: dict[str, str] = {
    "shortform": "Short-form (9:16)",
    "longform": "Long-form (16:9)",
}
_DEFAULT_FORM = "shortform"


def script_dir(ctx: PhaseContext) -> Path:
    """`<project.folder>/script` - where every text-phase artifact lives."""
    return Path(ctx.project.folder) / "script"


def render_dir(ctx: PhaseContext) -> Path:
    """`<project.folder>/render` - where Director II/Captions/Render write
    their plan/asset files (`direction_plan.json`, `card_plan.json`,
    `captions.ass`, and - later, Render's own job - `index.json`/
    `scene_timings.json`). Already created empty by `avg.pipeline.create.
    new_project` (see that module's `_PROJECT_DIRS`), matching this app's
    "grouped, not flat" project-folder convention."""
    return Path(ctx.project.folder) / "render"


def resolve_font_family(ctx: PhaseContext) -> str:
    """The ONE font resolver shared by captions and takeover cards (the
    channel's `font_family` setting - `avg.core.settings_schema`), so the
    two never drift the way the legacy PowerShell/Python resolvers did
    (see this repo's `global-font` memory note: `config.edit.font` had to
    reach both the caption `.ass` Style line AND the card renderer, and a
    B2.19.3 release existed solely to fix the half that was missed).

    An unset/blank setting degrades to `"Arial"` - a safe, near-universal
    family name - rather than raising or emitting an empty `Fontname`,
    which libass would otherwise refuse to match against anything."""
    font = str(ctx.channel.settings.get("font_family") or "").strip()
    if not font:
        return "Arial"
    # A path (family name OR a .ttf/.otf file) - libass's Style line wants
    # a family NAME either way, so a path's filename stem is used, exactly
    # like the vendored engine's own global-font resolution
    # (`avg.media.vendor.avg`'s `cmd_caption_burn`: "for a path, use its
    # stem"). A bare family name (the common case) passes through as-is.
    if font.lower().endswith((".ttf", ".otf")):
        return Path(font).stem
    return font


# Delivery-cue markers a narration/scene text may carry (script.py's own
# docstring: `[pause]`, `[[focus]]`, `((question))`) - ported from the
# legacy `Convert-VoicePauseMarker`/caption-cue-building regexes
# (`D:\\AI_Video_Gen\\modules\\pipeline.ps1`/`ai.ps1`). Shared here because
# both Director II (question markers drive deterministic Focused takeover
# cards) and Captions (every marker must be stripped/unwrapped before the
# words are ever treated as "spoken text" a caption or fallback-timing
# pass can use) need the identical patterns.
_PAUSE_MARKER_RE = re.compile(r"\s*\[\s*pause\s*\]\s*", re.IGNORECASE)
_FOCUS_MARKER_RE = re.compile(r"\[\[\s*(.*?)\s*\]\]", re.DOTALL)
_QUESTION_MARKER_RE = re.compile(r"\(\(\s*(.*?)\s*\)\)", re.DOTALL)


def strip_delivery_markers(text: str) -> str:
    """`text` with every delivery-cue marker resolved to what is actually
    SPOKEN: `[pause]` (no spoken content) removed outright, `[[focus]]`/
    `((question))` unwrapped to their inner words (the brackets are a
    delivery cue, not something a narrator says or a caption should show).
    Whitespace is collapsed the same way `avg.pipeline.phases.writing`'s own
    `_normalize_whitespace` does."""
    cleaned = _PAUSE_MARKER_RE.sub(" ", text)
    cleaned = _FOCUS_MARKER_RE.sub(r"\1", cleaned)
    cleaned = _QUESTION_MARKER_RE.sub(r"\1", cleaned)
    return " ".join(cleaned.split())


def extract_question_markers(text: str) -> list[str]:
    """Every `((question))` marker's inner text, in order - the
    deterministic source of Director II's Focused takeover cards (mirrors
    the legacy `Export-CardPlan`'s question-marker scan of `markers.json`;
    this codebase has no separate markers.json extraction phase, so this
    reads the markers directly off `scenes.json`'s own text instead)."""
    return [m.strip() for m in _QUESTION_MARKER_RE.findall(text) if m.strip()]


def duration_seconds(ctx: PhaseContext) -> int:
    """The active channel's `length_preset` expanded to seconds; an unset
    or unrecognised preset degrades to the 30-second default rather than
    raising - a broken/legacy channel document must still produce a
    buildable prompt."""
    preset = str(ctx.channel.settings.get("length_preset") or "")
    return _LENGTH_PRESET_SECONDS.get(preset, _DEFAULT_LENGTH_SECONDS)


def form_label(ctx: PhaseContext) -> str:
    """The active channel's `video_form` expanded to the prompt's
    human-readable label; unset/unrecognised degrades to short-form."""
    form = str(ctx.channel.settings.get("video_form") or _DEFAULT_FORM)
    return _FORM_LABELS.get(form, _FORM_LABELS[_DEFAULT_FORM])


def creative_inputs(ctx: PhaseContext) -> CreativeInputs:
    """Build `CreativeInputs` for `ctx` - the one place a phase reads the
    active channel's creative settings, so every phase's prompt agrees on
    what they mean. `aspect_ratio` comes from `ctx.project` (already
    resolved once at project-creation time by
    `avg.pipeline.create.new_project`), not re-read from the channel."""
    settings = ctx.channel.settings
    return CreativeInputs(
        topic=ctx.project.title,
        content_shape=str(settings.get("content_shape") or "documentary"),
        visual_tone=str(settings.get("visual_tone") or "documentary_cinematic"),
        style_lock=str(settings.get("style_lock") or ""),
        brand_preset=str(settings.get("brand_preset") or ""),
        form_label=form_label(ctx),
        aspect_ratio=ctx.project.aspect_ratio,
        duration_seconds=duration_seconds(ctx),
    )


def read_json(path: Path) -> Any:
    """Best-effort JSON read: `None` if the file is missing or unparseable,
    never raises - callers treat "no prior artifact" and "a corrupt one" the
    same way (both mean "nothing usable to build on")."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Any) -> None:
    """Write `data` as pretty-printed JSON, creating parent directories if
    needed (a fresh project already has `script/`, but a phase run in
    isolation - e.g. a test - may not)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
