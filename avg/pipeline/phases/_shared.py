"""Shared, tiny helpers for the AI-writing phases: turning a `PhaseContext`
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
    Whitespace is collapsed the same way `avg.pipeline.phases.script`'s own
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
