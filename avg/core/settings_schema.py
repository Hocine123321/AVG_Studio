"""The settings catalogue — the single source of truth for every
user-configurable setting in AVG Studio.

One entry per setting: `id`, `group`, `label`, `help`, `type` (`bool` |
`int` | `float` | `text` | `choice` | `path` | `secret`), `default`,
`choices`, `scope` (`app` | `channel`). The web UI renders its whole
settings screen from `GET /api/settings/schema` (see
`avg.api.routes_settings`) rather than each setting getting its own
hand-written screen — the legacy PowerShell app's `settings_menu.ps1` grew
to ~4,300 lines precisely because every option needed its own numbered
screen. Adding a setting here is one entry in `SETTINGS`; no route or UI
change is needed to make it appear.

`scope` is the enforcement point for the "no global copy of a channel
setting" rule (see `avg.config.channels`'s docstring for the three bugs
that rule replaces): a `scope="app"` setting's value lives in
`avg.config.settings`'s `SettingsDoc.values`; a `scope="channel"`
setting's value lives in the *active* channel's `Channel.settings` dict,
full stop — there is no merge layer and no global fallback copy.

`type="secret"` is different again: those ids name a provider whose *key*
is never read or written through this module or through
`/api/settings/values` at all — only `avg.config.secrets` (DPAPI-backed)
ever touches the value, and only `/api/keys/{provider}` exposes whether
one is set. Entries of this type exist here purely so the settings screen
knows a "Voice provider API key" field belongs on the page; `validate_value`
refuses to accept a value for one, by design.

Values were drawn from the legacy PowerShell app's real config
(`D:\\AI_Video_Gen\\config\\config.json`, 21 top-level keys) — this module
covers the settings a user would actually reach for (format/length,
captions, voice, images, render, the creative fields), not every option
that config ever grew.

Data plus small accessors only: no diffing engine, no undo stack, no
migration framework. That is deliberately out of scope for this module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

SettingType = Literal["bool", "int", "float", "text", "choice", "path", "secret"]
Scope = Literal["app", "channel"]


class SettingSpec(BaseModel):
    """One catalogue entry. `choices` is only meaningful when `type == "choice"`."""

    id: str
    group: str
    label: str
    help: str
    type: SettingType
    default: Any
    scope: Scope
    invalidates: str | None
    choices: list[str] | None = None


class SettingValidationError(ValueError):
    """Raised by `validate_value` when a value doesn't fit its spec.

    Deliberately a plain `ValueError` subclass (not something FastAPI-aware)
    - this module has no HTTP dependency; `avg.api.routes_settings` is what
    turns this into a 400.
    """


SETTINGS: list[SettingSpec] = [
    # --- paths (app scope) ---
    SettingSpec(
        id="python_exe",
        group="paths",
        label="Python executable",
        help="Path to the Python executable used for media rendering. Leave as 'python' to use whatever is on PATH.",
        type="path",
        default="python",
        scope="app",
        invalidates=None,
    ),
    # --- runtime / UI preferences (app scope) ---
    SettingSpec(
        id="theme",
        group="runtime",
        label="Color theme",
        help="Visual theme for the app UI.",
        type="choice",
        default="soft_dark",
        scope="app",
        choices=[
            "soft_dark", "monochrome_dark", "monochrome_light", "dark", "light",
            "high_contrast_light", "neon", "terminal_hacker", "editorial_cream",
        ],
        invalidates=None,
    ),
    SettingSpec(
        id="performance_mode",
        group="runtime",
        label="Performance mode",
        help="Trade rendering speed against resource usage on lower-end machines.",
        type="choice",
        default="lightweight",
        scope="app",
        choices=["lightweight", "balanced", "max"],
        invalidates=None,
    ),
    SettingSpec(
        id="run_as_admin",
        group="runtime",
        label="Run as administrator",
        help="Relaunch elevated on startup.",
        type="bool",
        default=False,
        scope="app",
        invalidates=None,
    ),
    # --- video format and length (channel scope: a production choice) ---
    SettingSpec(
        id="video_form",
        group="video",
        label="Form",
        help="Short-form (9:16 vertical) or long-form (16:9 horizontal) output.",
        type="choice",
        default="shortform",
        scope="channel",
        choices=["shortform", "longform"],
        invalidates="brief",
    ),
    SettingSpec(
        id="length_preset",
        group="video",
        label="Target length",
        help="Approximate target duration for the finished video.",
        type="choice",
        default="60s",
        scope="channel",
        choices=["30s", "60s", "90s", "2m", "4m", "8m"],
        invalidates="script",
    ),
    SettingSpec(
        id="aspect_ratio",
        group="video",
        label="Aspect ratio",
        help="Output frame aspect ratio.",
        type="choice",
        default="9:16",
        scope="channel",
        choices=["9:16", "16:9", "1:1"],
        invalidates="visuals",
    ),
    SettingSpec(
        id="fps",
        group="video",
        label="Frame rate",
        help="Output frames per second.",
        type="int",
        default=30,
        scope="channel",
        invalidates="render",
    ),
    SettingSpec(
        id="ken_burns",
        group="video",
        label="Ken Burns pan/zoom",
        help="Slowly pan and zoom on still images instead of holding them static.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="director2",
    ),
    # --- captions ---
    SettingSpec(
        id="captions_enabled",
        group="captions",
        label="Captions enabled",
        help="Burn captions into the final render.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="captions",
    ),
    SettingSpec(
        id="captions_provider",
        group="captions",
        label="Transcription provider",
        help="Speech-to-text provider used to generate caption timing.",
        type="choice",
        default="assemblyai",
        scope="channel",
        choices=["assemblyai", "whisper"],
        invalidates="captions",
    ),
    SettingSpec(
        id="captions_position",
        group="captions",
        label="Position",
        help="Where captions sit on the frame.",
        type="choice",
        default="bottom",
        scope="channel",
        choices=["top", "center", "bottom"],
        invalidates="captions",
    ),
    SettingSpec(
        id="captions_animation_style",
        group="captions",
        label="Animation style",
        help="How each caption word/line animates in.",
        type="choice",
        default="fade",
        scope="channel",
        choices=["none", "fade", "pop"],
        invalidates="captions",
    ),
    SettingSpec(
        id="captions_font_size",
        group="captions",
        label="Font size",
        help="Caption font size in pixels. 0 means auto-fit.",
        type="int",
        default=0,
        scope="channel",
        invalidates="captions",
    ),
    SettingSpec(
        id="font_family",
        group="captions",
        label="Font",
        help=(
            "The GLOBAL font (family name or a .ttf/.otf path) shared by captions AND "
            "takeover cards - one setting, one look. Empty uses the system default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="captions",
    ),
    # --- voice provider and options ---
    SettingSpec(
        id="voice_provider",
        group="voice",
        label="Voice provider",
        help="Text-to-speech backend.",
        type="choice",
        default="elevenlabs",
        scope="channel",
        choices=["elevenlabs", "edge"],
        invalidates="voice",
    ),
    SettingSpec(
        id="voice_id",
        group="voice",
        label="Voice ID",
        help="Provider-specific voice identifier.",
        type="text",
        default="",
        scope="channel",
        invalidates="voice",
    ),
    SettingSpec(
        id="voice_model_id",
        group="voice",
        label="Voice model",
        help="Provider-specific TTS model id.",
        type="text",
        default="",
        scope="channel",
        invalidates="voice",
    ),
    SettingSpec(
        id="voice_stability",
        group="voice",
        label="Stability",
        help="Voice stability (0-1). Higher is more consistent, lower is more expressive.",
        type="float",
        default=0.5,
        scope="channel",
        invalidates="voice",
    ),
    SettingSpec(
        id="voice_speed",
        group="voice",
        label="Speed",
        help="Speech playback rate multiplier.",
        type="float",
        default=1.0,
        scope="channel",
        invalidates="voice",
    ),
    SettingSpec(
        id="dynamic_pauses_enabled",
        group="voice",
        label="Dynamic pauses",
        help="Insert natural pauses at [pause] markers in the script.",
        type="bool",
        default=True,
        scope="channel",
        invalidates="script",
    ),
    # --- image provider and model ---
    SettingSpec(
        id="image_provider",
        group="images",
        label="Image provider",
        help="Backend used to generate scene visuals.",
        type="choice",
        default="pollinations",
        scope="channel",
        choices=["openai", "gemini", "pollinations", "stablehorde", "huggingface"],
        invalidates="visuals",
    ),
    SettingSpec(
        id="image_model",
        group="images",
        label="Image model",
        help="Provider-specific image model id.",
        type="text",
        default="flux",
        scope="channel",
        invalidates="visuals",
    ),
    SettingSpec(
        id="image_quality",
        group="images",
        label="Image quality",
        help="Rendering quality/resolution tier for generated images.",
        type="choice",
        default="medium",
        scope="channel",
        choices=["low", "medium", "high"],
        invalidates="visuals",
    ),
    # --- render engine and quality ---
    SettingSpec(
        id="render_engine",
        group="render",
        label="Render engine",
        help="Which media pipeline renders the final video.",
        type="choice",
        default="auto",
        scope="channel",
        choices=["auto", "ffmpeg", "moviepy"],
        invalidates="render",
    ),
    SettingSpec(
        id="render_codec",
        group="render",
        label="Video codec",
        help="Video codec used for the final render.",
        type="choice",
        default="libx264",
        scope="channel",
        choices=["libx264", "libx265"],
        invalidates="render",
    ),
    # --- creative fields ---
    SettingSpec(
        id="content_shape",
        group="creative",
        label="Content shape",
        help="The narrative/content template this channel follows.",
        type="text",
        default="documentary",
        scope="channel",
        invalidates="brief",
    ),
    SettingSpec(
        id="visual_tone",
        group="creative",
        label="Visual tone",
        help="Overall visual style used for image-prompt generation.",
        type="choice",
        default="documentary_cinematic",
        scope="channel",
        choices=[
            "documentary_cinematic", "news_broadcast", "dark_cinematic", "epic_cinematic",
            "realistic", "vibrant", "minimal", "wireframe", "custom",
        ],
        invalidates="director1",
    ),
    SettingSpec(
        id="style_lock",
        group="creative",
        label="Style lock",
        help="Free-text style suffix appended to every image prompt, to keep visuals consistent.",
        type="text",
        default="",
        scope="channel",
        invalidates="director1",
    ),
    SettingSpec(
        id="auto_style_lock",
        group="creative",
        label="Auto style lock",
        help="Let the app derive the style lock automatically instead of using the text above.",
        type="bool",
        default=False,
        scope="channel",
        invalidates="director1",
    ),
    SettingSpec(
        id="brand_preset",
        group="creative",
        label="Brand preset",
        help="Color/typography preset used for on-screen graphics and captions.",
        type="choice",
        default="modern_dark",
        scope="channel",
        choices=[
            "wireframe_ink", "modern_dark", "tiktok_viral", "neon_cyber", "warm_organic",
            "clean_light", "luxury_gold", "vsauce_curiosity", "academic_editorial", "tactical_terminal",
        ],
        invalidates="director2",
    ),
    # --- provider API keys (app scope; value never lives here — see the
    # type="secret" note in this module's docstring) ---
    SettingSpec(id="openai", group="keys", label="OpenAI API key",
                help="Used for OpenAI image generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="gemini", group="keys", label="Gemini API key",
                help="Used for Gemini text and image generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="elevenlabs", group="keys", label="ElevenLabs API key",
                help="Used for ElevenLabs voice generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="pollinations", group="keys", label="Pollinations API key",
                help="Used for Pollinations image generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="stablehorde", group="keys", label="StableHorde API key",
                help="Used for StableHorde image generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="huggingface", group="keys", label="HuggingFace API key",
                help="Used for HuggingFace text and image generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="groq", group="keys", label="Groq API key",
                help="Used for Groq text generation.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="assemblyai", group="keys", label="AssemblyAI API key",
                help="Used for AssemblyAI caption transcription.", type="secret", default=None, scope="app",
                invalidates=None),
]

BY_ID: dict[str, SettingSpec] = {s.id: s for s in SETTINGS}

# Every secret-typed id, i.e. every provider the settings screen shows a
# key toggle for regardless of whether a key has ever been set for it.
KEY_PROVIDER_IDS: list[str] = [s.id for s in SETTINGS if s.type == "secret"]


def get_schema() -> list[SettingSpec]:
    """The whole catalogue, in declaration order."""
    return list(SETTINGS)


def get_spec(setting_id: str) -> SettingSpec | None:
    """One entry by id, or `None` if `setting_id` isn't in the catalogue."""
    return BY_ID.get(setting_id)


def app_defaults() -> dict[str, Any]:
    """`{id: default}` for every `scope="app"`, non-secret entry."""
    return {s.id: s.default for s in SETTINGS if s.scope == "app" and s.type != "secret"}


def channel_defaults() -> dict[str, Any]:
    """`{id: default}` for every `scope="channel"` entry."""
    return {s.id: s.default for s in SETTINGS if s.scope == "channel"}


def validate_value(spec: SettingSpec, value: Any) -> Any:
    """Validate/coerce `value` against `spec`; raise `SettingValidationError` if it doesn't fit.

    Permissive on numeric coercion (a JSON string like `"42"` for an `int`
    field becomes `42`) since values arriving over HTTP are already JSON
    primitives with no guarantee the UI sent the "right" JSON type. Strict
    on `choice` (must be exactly one of `choices`) - silently accepting an
    unrecognised option is exactly the kind of quietly-wrong state this
    schema exists to prevent. Always refuses `type="secret"`: those values
    only ever move through `avg.config.secrets` via `/api/keys/{provider}`,
    never through here.
    """
    if spec.type == "secret":
        raise SettingValidationError(
            f"{spec.id!r} is a key — set it via POST /api/keys/{spec.id}, not /api/settings/values"
        )
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        raise SettingValidationError(f"{spec.id!r}: expected a boolean, got {value!r}")
    if spec.type == "int":
        if isinstance(value, bool):
            raise SettingValidationError(f"{spec.id!r}: expected an integer, got {value!r}")
        try:
            return int(value)
        except (TypeError, ValueError):
            raise SettingValidationError(f"{spec.id!r}: expected an integer, got {value!r}") from None
    if spec.type == "float":
        if isinstance(value, bool):
            raise SettingValidationError(f"{spec.id!r}: expected a number, got {value!r}")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise SettingValidationError(f"{spec.id!r}: expected a number, got {value!r}") from None
    if spec.type in ("text", "path"):
        if not isinstance(value, str):
            raise SettingValidationError(f"{spec.id!r}: expected text, got {value!r}")
        return value
    if spec.type == "choice":
        if not isinstance(value, str) or value not in (spec.choices or []):
            raise SettingValidationError(f"{spec.id!r}: {value!r} is not one of {spec.choices}")
        return value
    raise SettingValidationError(f"{spec.id!r}: unknown setting type {spec.type!r}")
