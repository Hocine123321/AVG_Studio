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
    #: Where a field's options come from at RUNTIME, when the catalogue
    #: cannot enumerate them. `"model_cache"` means the working-models cache
    #: (`avg.core.model_cache`) fills the list, which is why the cache has to
    #: be built before a model can be picked from a list. The field keeps its
    #: declared `type`, so an unbuilt cache still allows a typed value rather
    #: than locking the user out of a setting they could always edit before.
    source: str | None = None


class SettingValidationError(ValueError):
    """Raised by `validate_value` when a value doesn't fit its spec.

    Deliberately a plain `ValueError` subclass (not something FastAPI-aware)
    - this module has no HTTP dependency; `avg.api.routes_settings` is what
    turns this into a 400.
    """


# Three entries were REMOVED here on 2026-07-28 as dead switches - settings
# the UI rendered and saved and no Python code ever read:
#
#   python_exe       - nothing shells out to a Python executable. Rendering
#                      runs in a multiprocessing child of THIS interpreter
#                      (`avg.media.engine`), so there is no second Python to
#                      point anywhere.
#   performance_mode - the resource profile is derived from measured RAM in
#                      `load_render_settings`. A second, contradicting knob
#                      would have to win or lose against that measurement,
#                      and neither answer is right.
#   run_as_admin     - there is no elevation code path anywhere in the app.
#
# A leftover key in an existing settings.json or channel document is harmless:
# both are plain dicts and nothing iterates them expecting only known ids.

SETTINGS: list[SettingSpec] = [
    # --- app / how the application itself behaves (app scope) ---
    SettingSpec(
        id="theme",
        group="app",
        label="Color theme",
        help="Visual theme for the app UI. Night is the default — a dark surround tells the truth about a frame's exposure.",
        type="choice",
        # These are the two themes `web/src/theme.ts` actually implements,
        # and this catalogue is now the source of truth for the choice: the
        # UI reads it on load and writes back on toggle.
        #
        # This list used to offer nine values carried over from the legacy
        # app (`soft_dark`, `monochrome_dark`, `monochrome_light`, `dark`,
        # `light`, `high_contrast_light`, `neon`, `terminal_hacker`,
        # `editorial_cream`). None of them was one of the two implemented
        # themes, and `useTheme` never read this setting at all, so all
        # nine did nothing in either direction. Offering a choice that
        # cannot happen is worse than offering two that can; a stored value
        # from the old list is treated as unset (see `theme.ts`).
        default="night",
        scope="app",
        choices=["night", "day"],
        invalidates=None,
    ),
    SettingSpec(
        id="verbose_log",
        group="app",
        label="Verbose take log",
        help="Write every prompt and response into the take's log. Useful when a model is misbehaving; the files get large.",
        type="bool",
        default=False,
        scope="app",
        # Nothing is invalidated: this changes what is RECORDED about a run,
        # never what the run produces.
        invalidates=None,
    ),
    SettingSpec(
        id="motion_scale",
        group="app",
        label="Animation",
        help=(
            "How much the interface moves. Read by `web/src/tokens.css` through the "
            "`--motion-scale` custom property. Your operating system's own "
            "reduced-motion preference always wins over this."
        ),
        type="choice",
        default="full",
        scope="app",
        choices=["full", "reduced", "none"],
        # Nothing is invalidated: this changes how the UI moves, never what a
        # run produces.
        invalidates=None,
    ),
    SettingSpec(
        id="startup_check",
        group="app",
        label="Check components at startup",
        help=(
            "Warm the component check on the boot thread while the library "
            "loads, so App health answers instantly the first time you open "
            "it. Turning it off starts marginally faster; the check still "
            "runs, just later - the first time you open App health after "
            "launch, rather than during startup."
        ),
        type="bool",
        default=True,
        scope="app",
        invalidates=None,
    ),
    SettingSpec(
        id="max_parallel_jobs",
        group="app",
        label="Videos at once",
        help=(
            "How many videos may be made at the same time. Read by "
            "`avg/api/app.py`. Renders always take turns whatever this says - "
            "there is one render engine. Raise it only if your providers are "
            "not rate-limiting you. Nothing is ever refused for being over "
            "the limit: a job past it waits and starts when a slot frees, so "
            "1 means the app makes videos one at a time, in the order you "
            "asked for them."
        ),
        type="choice",
        default="2",
        scope="app",
        choices=["1", "2", "3", "4"],
        invalidates=None,
    ),
    # --- video format and length (channel scope: a production choice) ---
    SettingSpec(
        id="topic_research",
        group="creative",
        label="Research the topic first",
        help=(
            "Spend one extra AI call gathering the facts about the topic before the brief is "
            "written. The script then draws on it."
        ),
        type="bool",
        default=False,
        scope="channel",
        # A different brief means a different script and everything after it -
        # the same chain every other writing setting invalidates.
        invalidates="brief",
    ),
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
    # --- per-phase text models (the mockup's Models table) ---
    #
    # One `provider/model` string per slot rather than a provider choice
    # plus a model text field: model ids are provider-specific and the
    # model catalogue is cache-only, so a `choice` type cannot enumerate
    # them, and two fields per slot would triple this block for no gain.
    # `avg.providers.text.router.parse_target` is the only parser, and it
    # rejects a malformed pair rather than silently routing elsewhere.
    #
    # `script_draft` and `script_segmentation` are ROUTER phase keys - the
    # Script phase's two model calls. `avg.pipeline.registry` still has one
    # `script` phase, so both invalidate `"script"`.
    SettingSpec(
        id="text_model_default",
        group="models",
        label="Default text model",
        help="Fallback 'provider/model' for any phase with no model of its own. E.g. groq/llama-3.3-70b-versatile.",
        type="text",
        default="groq/llama-3.3-70b-versatile",
        scope="channel",
        invalidates="brief",
        source="model_cache",
    ),
    *[
        spec
        for phase_key, phase_label, invalidated in (
            ("brief", "Brief", "brief"),
            ("script_draft", "Script · Draft", "script"),
            ("script_segmentation", "Script · Segmentation", "script"),
            ("director1", "Director I", "director1"),
            ("director2", "Director II", "director2"),
        )
        for spec in (
            SettingSpec(
                id=f"{phase_key}_model",
                group="models",
                label=f"{phase_label} — model",
                help=f"'provider/model' used for {phase_label}. Empty uses the default text model.",
                type="text",
                default="",
                scope="channel",
                invalidates=invalidated,
                source="model_cache",
            ),
            SettingSpec(
                id=f"{phase_key}_backup",
                group="models",
                label=f"{phase_label} — backup",
                help=f"'provider/model' tried once if {phase_label}'s model fails a switchable error. Empty means no backup.",
                type="text",
                default="",
                scope="channel",
                invalidates=invalidated,
                source="model_cache",
            ),
            SettingSpec(
                id=f"{phase_key}_on_failure",
                group="models",
                label=f"{phase_label} — if both fail",
                help="Stop the run, or fall back to the default text model as a last resort.",
                type="choice",
                default="stop",
                scope="channel",
                choices=["stop", "fall_back"],
                invalidates=invalidated,
            ),
        )
    ],
    SettingSpec(
        id="live_list_source",
        group="models",
        label="Live List source",
        help="Use the app-wide Live List, or keep a shortlist that belongs to this channel alone.",
        type="choice",
        default="global",
        scope="channel",
        choices=["global", "channel"],
        # Nothing is invalidated: this changes which models are OFFERED,
        # never which one produced an existing artifact.
        invalidates=None,
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
        invalidates="screen_text",
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
        invalidates="screen_text",
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
        invalidates="screen_text",
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
        invalidates="screen_text",
    ),
    SettingSpec(
        id="captions_font_size",
        group="captions",
        label="Font size",
        help="Caption font size in pixels. 0 means auto-fit.",
        type="int",
        default=0,
        scope="channel",
        invalidates="screen_text",
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
        invalidates="screen_text",
    ),
    # --- overlay cards (Director II's focused/explainer cards and takeover
    # cards) - three brand colours, read by `avg.media.overlays.resolve_brand`,
    # the one function both a real render (`render_layers`, called from
    # `avg.pipeline.phases.director._draw_overlay_assets`) and the free
    # overlay/takeover preview (`avg.media.preview._draw_to_png`) call, so a
    # change here draws the same way in both (Slice 14 review, Important 4).
    # `font_family` is deliberately NOT repeated here: it already lives in
    # the captions group and its own help text already says it is shared
    # with takeover cards - a second copy would be the per-field-inheritance
    # bug CLAUDE.md's config-scopes section warns about, restated as two
    # settings instead of one.
    SettingSpec(
        id="overlay_accent1",
        group="overlay",
        label="Accent 1",
        help=(
            "Primary card accent colour, '#RRGGBB'. Used by most focused/explainer cards. "
            "Empty uses the drawing library's own default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="overlay_accent2",
        group="overlay",
        label="Accent 2",
        help="Secondary card accent colour, '#RRGGBB'. Empty uses the drawing library's own default.",
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
    ),
    SettingSpec(
        id="overlay_highlight",
        group="overlay",
        label="Highlight",
        help=(
            "Card highlight colour, '#RRGGBB', for cards whose plan entry asks for it. "
            "Empty uses the drawing library's own default."
        ),
        type="text",
        default="",
        scope="channel",
        invalidates="director2",
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
        help="Which voice reads the narration. The Live List decides what is offered here.",
        type="text",
        default="",
        scope="channel",
        invalidates="voice",
        source="live_list:voice",
    ),
    SettingSpec(
        # Deliberately NOT a Live List field: this is the TTS ENGINE
        # (e.g. eleven_multilingual_v2), not a voice, and the Live List's
        # voice tab lists voices. Offering voice ids here would be a
        # picker whose every option is wrong.
        id="voice_model_id",
        group="voice",
        label="Voice model",
        help="Provider-specific TTS engine id, e.g. eleven_multilingual_v2.",
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
        help="Which image model to generate with. The Live List decides what is offered here.",
        type="text",
        default="flux",
        scope="channel",
        invalidates="visuals",
        source="live_list:image",
    ),
    SettingSpec(
        id="image_provider_backup",
        group="images",
        label="Image provider — backup",
        help="Tried once if the primary provider fails in a way a different provider could survive. Leave unset to never fall back.",
        type="choice",
        default="",
        scope="channel",
        choices=["", "openai", "gemini", "pollinations", "stablehorde", "huggingface"],
        invalidates="visuals",
    ),
    SettingSpec(
        id="image_model_backup",
        group="images",
        label="Image model — backup",
        help="Which model the backup provider should use. The Live List decides what is offered here.",
        type="text",
        default="",
        scope="channel",
        invalidates="visuals",
        source="live_list:image",
    ),
    SettingSpec(
        id="image_on_failure",
        group="images",
        label="Images — if both fail",
        help=(
            "Stop the run, or fall back to the backup provider. Read by "
            "`avg/pipeline/phases/visuals.py`. A failure a second provider "
            "would hit identically - a blocked prompt, a bug - never falls back."
        ),
        type="choice",
        default="stop",
        scope="channel",
        choices=["stop", "fall_back"],
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
        id="render_threads",
        group="render",
        label="Render threads",
        help=(
            "How many CPU threads the encoder may use. 0 means auto — one per core. "
            "Lower it to keep the machine responsive while a long render runs."
        ),
        type="int",
        default=0,
        scope="channel",
        invalidates="render",
    ),
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


def resolve_channel_settings(
    channel_settings: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    """The one resolver (Slice 19 spec §2.3): for every scope="channel"
    SettingSpec, the channel's own value if the key is present there,
    otherwise the app-wide defaults document's value if present THERE,
    otherwise the SettingSpec's own default. Absence is the only
    inheritance signal - `channel_settings.get(id, ...)` would treat an
    explicitly-stored empty string as absent, which is exactly the legacy
    bug this function exists to not reintroduce; `in` is checked, not
    truthiness.

    Pure and I/O-free on purpose: callers own fetching both dicts (a
    channel's `.settings`, and `avg.config.channel_defaults.load_defaults()`),
    so this function has no path to the filesystem and no path to
    `avg.config.channels` - every caller, api or pipeline, can use it with
    nothing beyond stdlib dicts.
    """
    return {
        spec.id: (
            channel_settings[spec.id]
            if spec.id in channel_settings
            else defaults.get(spec.id, spec.default)
        )
        for spec in SETTINGS
        if spec.scope == "channel"
    }


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
