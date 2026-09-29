"""Phase 6: Captions & Takeover Text.

Runs after Director II (Phase 5), before Render (Phase 7, someone else's
task - see this module's own "what Render still needs" note below). Reads
the finished narration (`scenes.json`) and the synthesized voice track
(`audio/optimized_voice.mp3`, Phase 4), transcribes it to timed captions,
and writes `render/captions.ass` - the exact `.ass` shape the vendored
render engine (`avg.media.vendor.avg`) expects to burn later, matched
field-for-field against a real, working legacy project (this slice's task
brief) rather than invented.

**AssemblyAI when a key exists (`avg.config.secrets.has_key`/`get_key`),
otherwise derive timings from the script and audio duration** - this
task's named invariant, and the reason `_get_words` never lets a missing OR
FAILING transcription take the whole phase down: a network hiccup, an
expired key, or no key at all all degrade to the same synthetic timing
path (`_synthetic_word_timings`), matching this codebase's general "one
provider failing must not kill the phase" philosophy (see
`avg.pipeline.phases.visuals`'s identical stance on one image failing).
The synthetic path allocates each scene a slice of the total audio
duration proportional to its (delivery-marker-stripped) character count,
then slices that scene's own words the same way - a simple, always-
available estimate, not a claim of AssemblyAI-grade precision.

**AssemblyAI's `/v2/upload` endpoint takes a raw byte body**, which
`avg.providers.transport.Transport` (JSON-body-only, by design - see that
module's docstring) cannot express, so this phase talks to `httpx`
directly (`_transcribe_with_assemblyai`) rather than through the shared
provider Transport. Tests monkeypatch that one function wholesale, the
same "patch the phase module's own seam" convention
`avg.pipeline.phases.visuals`/`.voice`'s `_build_provider` already
established - no live call is ever made in this repo's test suite.

**Respects the global font setting** (`avg.pipeline.phases._common.
resolve_font_family`) - the ONE resolver shared with (a future) takeover-
card renderer, so the two can never drift the way the legacy PowerShell/
Python resolvers did. The resolved family name reaches the `.ass` file's
`Style:` line directly.

**Captions vanish during takeover-card windows** - this phase also
resolves each of Director II's planned cards
(`render/card_plan.json`, written with `{element, scene, text, accent}`
and no timing yet) to a `start`/`end` window and writes the plan back
enriched (mirroring the legacy `cmd_caption_burn`'s own card-window
refinement step), then suppresses any caption cue whose span overlaps a
card window - a takeover card owns the whole screen while it is up.

**What Render (Phase 7, a separate task) still needs to do**: burn
`captions.ass` (via ffmpeg/libass) and composite the resolved
`card_plan.json` windows - this phase produces the data, not the burn.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from avg.config.secrets import get_key, has_key
from avg.core.errors import AvgError, ErrorKind, report_degradation
from avg.core.events import ProgressEvent
from avg.media.vendor.avg import _target_size
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import (
    allocate_proportional,
    audio_duration_seconds,
    read_json,
    render_dir,
    resolve_font_family,
    resolve_font_family_value,
    script_dir,
    strip_delivery_markers,
    write_json,
)

_ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"
_ASSEMBLYAI_UPLOAD_TIMEOUT = 120.0
_ASSEMBLYAI_SUBMIT_TIMEOUT = 30.0
_ASSEMBLYAI_POLL_TIMEOUT = 15.0
_ASSEMBLYAI_POLL_INTERVAL_SECONDS = 3.0
_ASSEMBLYAI_MAX_POLLS = 60

# Generous, fixed defaults until a real setting exists for them - matches
# the "generous, fixed default" pattern `avg.pipeline.phases.voice`'s own
# module docstring already established for silence/pause constants not yet
# in `avg.core.settings_schema`.
_MAX_WORDS_PER_CUE = 6
_DEFAULT_FONT_SIZE = 60

# Matches the vendored engine's own `make_ass_header` field values
# (`avg.media.vendor.avg`, `cmd_caption_burn`) for everything this phase
# doesn't have a settings-driven knob for yet.
_COLOR_PRIMARY = "&H00FFFFFF"
_COLOR_SECONDARY = "&H000000FF"
_COLOR_OUTLINE = "&H00000000"
_COLOR_BACK = "&H00000000"
_OUTLINE_WIDTH = "3.00"
_ALIGNMENT_BY_POSITION = {"bottom": 2, "top": 8, "center": 5}
_MARGIN_V_BY_POSITION = {"bottom": 40, "top": 40, "center": 0}

# The pop-in entrance every caption cue gets: scale up from 65% through an
# overshoot at 108%, settling at 100% by 190ms - "the app's caption feel"
# (this slice's task brief, verbatim from the sample `captions.ass` read
# off a real project). Kept exactly, not reinterpreted.
_POP_IN_OVERRIDE = r"{\fscx65\fscy65\t(0,90,0.7,\fscx108\fscy108)\t(90,190,\fscx100\fscy100)}"

# The highlight/typewriter accent - a FIXED colour, not a setting,
# matching legacy's own `_HIGHLIGHT_ACCENT` constant exactly (bright
# yellow). Porting it as a new brand-driven setting would be scope past
# the source (see the design spec's Out of Scope).
_HIGHLIGHT_ACCENT = "&H0000FFFF"

# Rise's move distance and cue_pos's padding are both scaled against this
# reference resolution, matching the pop-in override's own
# resolution-independence (it uses fixed ms/percentages, needing no
# scaling; rise's PIXEL distance does).
_CAP_REF_LONG_EDGE = 1920.0


class _AssemblyAIError(RuntimeError):
    """Raised by `_transcribe_with_assemblyai`/`_assemblyai_poll` on any
    upload/submit/poll failure - always caught by `_get_words`, which
    degrades to the synthetic timing path rather than letting a
    transcription failure take the whole phase down."""


def _audio_dir(ctx: PhaseContext) -> Path:
    return Path(ctx.project.folder) / "audio"


def _optimized_voice_path(ctx: PhaseContext) -> Path:
    return _audio_dir(ctx) / "optimized_voice.mp3"


def seed_transcript_cache(
    project_folder: Path, audio_path: Path, provider: str, words: list[dict[str, Any]]
) -> Path | None:
    """Hand this phase's word list to the render's burn pass, for free.

    The burn pass transcribes unless it finds its own cache file
    (`avg/media/vendor/avg.py:3548-3568`). This phase has ALREADY produced a
    word list - from AssemblyAI, or from the proportional estimate when
    there is no key - so writing that list into the vendored cache means the
    burn never re-uploads the audio and never re-pays for it. It also means
    the words that get burnt are the same ones this phase timed the cards
    against.

    Units are converted here, deliberately and in ONE place: this phase
    works in seconds, the vendored pass in milliseconds.

    `provider` is whatever produced the words - `"assemblyai"` or
    `"estimated"`. It is part of the cache key, so the render has to pass
    the same string back; the phase records it in
    `render/_transcript_source.json` rather than leaving Render to guess. A
    mismatch is a silent cache miss, not an error, which is exactly why it
    is written down.
    """
    from avg.media.vendor.avg import _seg_cache_key

    try:
        key = _seg_cache_key("transcript", 0, provider, str(audio_path))  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - a cache we cannot key is a cache we skip
        return None

    cache_dir = Path(project_folder) / "render" / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"transcript_{key}.json"
    payload = [
        {
            "text": str(word.get("text") or ""),
            "start": int(round(float(word.get("start") or 0.0) * 1000)),
            "end": int(round(float(word.get("end") or 0.0) * 1000)),
        }
        for word in words
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ── proportional timing estimate (the no-key/degraded path) ──────────────


def _scene_windows(scene_texts_clean: list[str], total_duration: float) -> dict[int, tuple[float, float]]:
    """1-indexed `{scene_number: (start, end)}` spanning the whole audio
    track - used both to derive synthetic word timing AND (regardless of
    whether AssemblyAI is in play) to resolve each takeover card's window,
    since a card names a `scene`, not a transcript offset, and no
    `scene_timings.json` exists yet at this point in the pipeline (Render,
    Phase 7, builds that later from Director II's `direction_plan.json`)."""
    spans = allocate_proportional(scene_texts_clean, total_duration)
    return {i + 1: span for i, span in enumerate(spans)}


def _synthetic_word_timings(
    scene_texts_clean: list[str], scene_windows: dict[int, tuple[float, float]]
) -> list[dict]:
    """Per-word `{text, start, end}` (seconds) for every scene, without a
    real transcript: each scene's own words are split proportionally
    across that scene's already-estimated window."""
    words: list[dict] = []
    for i, text in enumerate(scene_texts_clean, start=1):
        scene_words = text.split()
        if not scene_words:
            continue
        start, end = scene_windows.get(i, (0.0, 0.0))
        for word, (ws, we) in zip(scene_words, allocate_proportional(scene_words, max(end - start, 0.0), start)):
            words.append({"text": word, "start": ws, "end": we})
    return words


# ── AssemblyAI (real transcription, only when a key is configured) ───────


def _assemblyai_poll(audio_bytes: bytes, api_key: str, *, client: httpx.Client, sleep: Callable[[float], None]) -> list[dict]:
    """Upload -> submit -> poll-until-`completed`, returning per-word
    `{text, start, end}` (seconds). Raises `_AssemblyAIError` on any
    failure - upload/submit HTTP error, a job that ends `error`, or the
    poll budget (`_ASSEMBLYAI_MAX_POLLS`) running out - always caught by
    `_get_words`."""
    headers = {"authorization": api_key}

    upload_resp = client.post(f"{_ASSEMBLYAI_BASE}/upload", headers=headers, content=audio_bytes)
    if upload_resp.status_code >= 400:
        raise _AssemblyAIError(f"AssemblyAI upload failed: HTTP {upload_resp.status_code}")
    upload_url = upload_resp.json().get("upload_url")
    if not upload_url:
        raise _AssemblyAIError("AssemblyAI upload returned no upload_url")

    submit_resp = client.post(f"{_ASSEMBLYAI_BASE}/transcript", headers=headers, json={"audio_url": upload_url})
    if submit_resp.status_code >= 400:
        raise _AssemblyAIError(f"AssemblyAI transcript submission failed: HTTP {submit_resp.status_code}")
    transcript_id = submit_resp.json().get("id")
    if not transcript_id:
        raise _AssemblyAIError("AssemblyAI transcript submission returned no id")

    for _ in range(_ASSEMBLYAI_MAX_POLLS):
        poll_resp = client.get(f"{_ASSEMBLYAI_BASE}/transcript/{transcript_id}", headers=headers)
        if poll_resp.status_code >= 400:
            raise _AssemblyAIError(f"AssemblyAI poll failed: HTTP {poll_resp.status_code}")
        data = poll_resp.json()
        status = data.get("status")
        if status == "completed":
            return [
                {
                    "text": str(w.get("text") or ""),
                    "start": float(w.get("start") or 0) / 1000.0,
                    "end": float(w.get("end") or 0) / 1000.0,
                }
                for w in (data.get("words") or [])
                if str(w.get("text") or "").strip()
            ]
        if status == "error":
            raise _AssemblyAIError(f"AssemblyAI transcription error: {data.get('error')}")
        sleep(_ASSEMBLYAI_POLL_INTERVAL_SECONDS)

    raise _AssemblyAIError("AssemblyAI transcription timed out waiting for a result")


def _transcribe_with_assemblyai(audio_bytes: bytes, api_key: str) -> list[dict]:
    """Real AssemblyAI call, real network client. Tests monkeypatch this
    function directly (`avg.pipeline.phases.visuals`/`.voice`'s own
    `_build_provider` seam-patching convention) rather than driving httpx -
    see module docstring for why this bypasses the shared Transport."""
    with httpx.Client() as client:
        return _assemblyai_poll(audio_bytes, api_key, client=client, sleep=time.sleep)


def _get_words(
    ctx: PhaseContext, audio_path: Path, scene_texts_clean: list[str], scene_windows: dict[int, tuple[float, float]]
) -> tuple[list[dict], str]:
    """`(words, source)` - `source` is `"tts_native"`, `"assemblyai"`, or
    `"estimated"`.

    `captions_timing_source` (default `"auto"`) decides priority: native
    TTS word timing (free, from `avg.pipeline.phases.voice`'s own
    `audio/word_timings.json` when that phase's provider supported it)
    ahead of AssemblyAI, ahead of the proportional estimate - a real TTS
    engine's own account of what it said and when is more reliably in sync
    with the actual audio than a second transcription of it could be, and
    it costs nothing. `"assemblyai"`/`"estimated"` force one path
    explicitly, skipping whatever ranks above it.

    ANY AssemblyAI failure (network, timeout, bad key, empty result)
    degrades to the synthetic estimate rather than raising - see module
    docstring's "no key OR a failing key" invariant, unchanged from before
    this slice."""
    timing_source = str(ctx.channel_values.get("captions_timing_source") or "auto")

    if timing_source in ("auto", "tts_native"):
        native = read_json(_audio_dir(ctx) / "word_timings.json")
        if isinstance(native, dict) and isinstance(native.get("words"), list) and native["words"]:
            return native["words"], "tts_native"

    if timing_source in ("auto", "assemblyai") and has_key("assemblyai"):
        key = get_key("assemblyai")
        if key:
            try:
                words = _transcribe_with_assemblyai(audio_path.read_bytes(), key)
            except Exception as exc:  # noqa: BLE001 - any transcription failure degrades, never raises
                message = f"AssemblyAI transcription unavailable ({exc}); using estimated timing."
                ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=5, message=message))
                # Recorded, because the captions the user gets are word-timed
                # by a proportional guess rather than by a transcript - and
                # nothing else on any screen would ever say so.
                report_degradation(ctx, message)
            else:
                if words:
                    return words, "assemblyai"
    return _synthetic_word_timings(scene_texts_clean, scene_windows), "estimated"


# ── card-window resolution (Director II's card_plan.json, refined) ───────


def _resolve_card_windows(cards: list[dict], scene_windows: dict[int, tuple[float, float]]) -> list[dict]:
    """Each card gets a `start`/`end` (seconds) from its scene's estimated
    window - Director II plans WHICH scene a card belongs to; this phase
    is the first one that actually has any notion of time, so it is the
    one that resolves the window (mirrors the legacy `cmd_caption_burn`'s
    own card-window refinement, minus the transcript-word-matching
    precision - see module docstring). Rounded to 3 decimals for the
    on-disk representation only - `_card_suppression_windows` below uses
    the exact, unrounded value for the actual overlap test, since rounding
    UP can push a window's boundary past an adjacent scene's exact cue
    start and wrongly suppress it too."""
    resolved: list[dict] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        c = dict(card)
        scene_num = _coerce_int(card.get("scene"))
        window = scene_windows.get(scene_num) if scene_num is not None else None
        if window is not None:
            c["start"] = round(window[0], 3)
            c["end"] = round(window[1], 3)
        resolved.append(c)
    return resolved


def _card_suppression_windows(
    cards: list[dict], scene_windows: dict[int, tuple[float, float]]
) -> list[tuple[float, float]]:
    """The exact (unrounded) `(start, end)` window for every card that
    names a real scene - see `_resolve_card_windows`'s docstring for why
    this is kept separate from the rounded, on-disk `start`/`end`."""
    windows: list[tuple[float, float]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        scene_num = _coerce_int(card.get("scene"))
        window = scene_windows.get(scene_num) if scene_num is not None else None
        if window is not None:
            windows.append(window)
    return windows


# ── captions.ass rendering ────────────────────────────────────────────────


def _ass_header(width: int, height: int, font_name: str, font_size: int, position: str) -> str:
    """Matches `avg.media.vendor.avg`'s own `make_ass_header` field-for-
    field (see module docstring) - `width`/`height` become `PlayResX`/
    `PlayResY`, `font_name` reaches `Fontname` directly."""
    alignment = _ALIGNMENT_BY_POSITION.get(position, 2)
    margin_v = _MARGIN_V_BY_POSITION.get(position, 40)
    return (
        "[Script Info]\n"
        "Title: AVG Captions\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},{_COLOR_PRIMARY},{_COLOR_SECONDARY},"
        f"{_COLOR_OUTLINE},{_COLOR_BACK},0,0,0,0,100,100,0,0,1,{_OUTLINE_WIDTH},0,"
        f"{alignment},10,10,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    if cs >= 100:  # rounding at the .995s boundary must not emit ".100"
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _res_scale(width: int, height: int) -> float:
    """Pixel distances scale against a 1920px reference long edge, the
    same reference `_CAP_REF_LONG_EDGE` names in legacy - so `rise`'s
    move distance (and its own padding) looks the same fraction of the
    frame at any output resolution, not a fixed pixel count that would be
    oversized on a small preview render or too subtle on 4K."""
    long_edge = max(width, height)
    return long_edge / _CAP_REF_LONG_EDGE if long_edge else 1.0


def _cue_pos(width: int, height: int, position: str, font_size: int) -> tuple[int, int]:
    """Where a caption cue rests once its entrance finishes - centred
    horizontally (captions are already centre-aligned via the Style
    line's own `Alignment` field), vertically per `position`. Ported from
    `cue_pos` (`D:\\AI_Video_Gen\\python\\avg.py`) - simple arithmetic,
    not font-metric text measurement; the same padding constant (`40`)
    `_ass_header`'s own `MarginL`/`MarginR` use, scaled by `_res_scale`."""
    x = width // 2
    pad = round(40 * _res_scale(width, height))
    if position == "bottom":
        y = height - pad - font_size // 2
    elif position == "top":
        y = pad + font_size // 2
    else:
        y = height // 2
    return x, y


def _ass_color_tag(ass_color: str) -> str:
    """This file's own `AABBGGRR` colour constants (`&H00FFFFFF`, always
    10 characters: `&H` + 8 hex digits) rewritten as the `\\c&HBBGGRR&`
    colour-override tag, which carries no alpha byte - strips the leading
    `&H00`. Ported from `Get-AiEditPlan`'s sibling `_c()` closure in
    `reveal_text`/`highlight_text` (`D:\\AI_Video_Gen\\python\\avg.py`)."""
    return r"\c&H" + ass_color[4:] + "&"


def _animated_text(
    style: str,
    cue: dict[str, Any],
    *,
    width: int,
    height: int,
    position: str,
    font_size: int,
) -> str:
    """The per-cue Dialogue-line body: the escaped caption text, wrapped
    in whichever ASS override the selected style needs. Six genuinely
    different mechanisms, ported line-for-line from `animated_text`/
    `reveal_text`/`highlight_text`
    (`D:\\AI_Video_Gen\\python\\avg.py`) - not reskins of one another:
    pop=scale, fade=opacity, rise=move+opacity, highlight=cumulative
    colour fill (left behind), typewriter=a single whole-word highlight
    that MOVES with the voice (not left behind - that's what makes it
    distinct from highlight). An unrecognised style degrades to plain
    text rather than raising, matching every other closed-vocabulary
    degrade in this codebase.

    All per-word timing below is in SECONDS (`cue["words"]`'s own shape -
    see `_group_cues`'s docstring and `_synthetic_word_timings`'s), NOT
    the milliseconds legacy's raw AssemblyAI dicts carried - the
    centisecond/millisecond conversions below are scaled accordingly, not
    copied as literal legacy arithmetic.
    """
    text = _ass_escape(cue["text"])

    if style == "none":
        return text

    if style == "pop":
        return _POP_IN_OVERRIDE + text

    if style == "fade":
        # Clean opacity fade-in, no movement, 180ms.
        return r"{\alpha&HFF&\t(0,180,\alpha&H00&)}" + text

    if style == "rise":
        # Glides up ~34px (scaled) while fading in, 220ms.
        x, y = _cue_pos(width, height, position, font_size)
        dist = max(6, round(34 * _res_scale(width, height)))
        return (
            r"{\alpha&HFF&\move(" + f"{x},{y + dist},{x},{y},0,220" + r")"
            r"\t(0,220,\alpha&H00&)}" + text
        )

    words = cue.get("words") or []
    if not words:
        return text  # no per-word data to sync highlight/typewriter to

    if style == "highlight":
        # Every word visible; each fills base -> accent as it's spoken
        # (\kf sweep), staying accent-coloured once filled.
        parts = []
        for w in words:
            dur_cs = max(1, round((float(w["end"]) - float(w["start"])) * 100))
            parts.append(r"{\kf" + str(dur_cs) + r"}" + _ass_escape(str(w["text"])) + " ")
        return "".join(parts).rstrip()

    if style == "typewriter":
        # Every word visible; a whole-word colour SNAP moves with the
        # voice - lit for exactly the moment it's spoken, then snapped
        # back. \r before each word scopes the transform so only the
        # currently-spoken word is lit at any instant.
        base = _ass_color_tag(_COLOR_PRIMARY)
        accent = _ass_color_tag(_HIGHLIGHT_ACCENT)
        cue_start = float(cue["start"])
        parts = []
        for w in words:
            # ws must be >=1ms: an accent \t at exactly t=0 collides with
            # the \r reset (also at t=0) and \r wins, so the FIRST word
            # would never light.
            ws = max(1, round((float(w["start"]) - cue_start) * 1000))
            we = max(ws + 1, round((float(w["end"]) - cue_start) * 1000))
            parts.append(
                r"{\r\t(" + str(ws) + "," + str(ws) + "," + accent + r")"
                r"\t(" + str(we) + "," + str(we) + "," + base + r")}" + _ass_escape(str(w["text"]))
            )
        return " ".join(parts)

    return text


#: What a caption preview shows. Short enough to fit one line at any of the
#: supported positions, long enough to show wrapping behaviour.
SAMPLE_CAPTION_TEXT = "This is what your captions will look like"


def sample_ass(width: int, height: int, font_name: str, font_size: int, position: str) -> str:
    """A one-line `.ass` file for the caption preview.

    Built from this module's own `_ass_header` - the same construction the
    real `captions.ass` uses, which itself matches the vendored engine's
    `make_ass_header` field-for-field. A preview that restated those style
    fields would be a second implementation, free to drift from what libass
    actually burns, and that is precisely the lie the preview exists to
    prevent.

    `font_name`/`font_size` are the RAW setting values - a blank font, a
    `.ttf`/`.otf` path, a size of `0` for "auto" - exactly as
    `avg.api.routes_preview` reads them off the request body, not
    pre-resolved. Resolving them here, with the identical rule `run()`
    below applies (`resolve_font_family_value`, `or _DEFAULT_FONT_SIZE`),
    is what makes this the ONE resolver: a caller one layer up guessing at
    its own default (a hardcoded `"Figtree"`/`64`, or worse, doing nothing
    and letting a blank family reach libass, which then draws nothing) is
    the exact bug this function exists to prevent. See Slice 14's review.
    """
    resolved_name = resolve_font_family_value(font_name)
    resolved_size = int(font_size or 0) or _DEFAULT_FONT_SIZE
    header = _ass_header(width, height, resolved_name, resolved_size, position)
    line = (
        f"Dialogue: 0,{_ass_time(0.0)},{_ass_time(2.0)},Default,,0,0,0,,"
        f"{_ass_escape(SAMPLE_CAPTION_TEXT)}\n"
    )
    return header + line


def _group_cues(words: list[dict], max_words: int = _MAX_WORDS_PER_CUE) -> list[dict]:
    """Consecutive words chunked into `{start, end, text, words}` caption
    cues of up to `max_words` each - simple fixed-size grouping (no
    sentence-boundary awareness), sufficient for both the real AssemblyAI
    word list and the synthetic estimate.

    `words` (the cue's own raw per-word `{text, start, end}` list) is kept
    alongside the already-joined `text` for `_animated_text`'s
    `"highlight"`/`"typewriter"` styles, which sync colour to individual
    spoken words - every other style only ever reads `start`/`end`/`text`.
    """
    cues: list[dict] = []
    for i in range(0, len(words), max_words):
        chunk = [w for w in words[i : i + max_words] if str(w.get("text") or "").strip()]
        if not chunk:
            continue
        cues.append(
            {
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(str(w["text"]) for w in chunk),
                "words": chunk,
            }
        )
    return cues


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def _dialogue_line(start: float, end: float, body: str) -> str:
    """`body` is the already-built line content - `_animated_text`'s job,
    not this function's; a plain (`"none"`-style) body is exactly the
    escaped cue text with no override prefix at all."""
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{body}"


@dataclass(frozen=True, slots=True)
class ScreenTextPhase:
    """Phase 6 - the text that appears on screen.

    Named "Screen Text" rather than "Captions" because it owns BOTH kinds:
    the spoken-word captions written into `captions.ass`, and the takeover
    cards' own text, whose windows this phase resolves into
    `card_plan.json`. The old app called it "Captions & Takeover Text"
    (`D:\\AI_Video_Gen\\info.txt:225`) - this is the same phase under a
    shorter name.

    The module keeps its own name: it is named for the file it writes,
    `render/captions.ass`, which has not changed.
    """

    id: int = 6
    key: str = "screen_text"
    name: str = "Screen Text"
    description: str = (
        "Transcribes the narration and writes the caption file the renderer burns in. Writes render/captions.ass."
    )

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return _optimized_voice_path(ctx).exists()

    def run(self, ctx: PhaseContext) -> None:
        # `captions_enabled` used to be a dead switch: the settings screen
        # offered it, saved it, and this phase transcribed regardless -
        # which for the AssemblyAI provider means a paid API call the user
        # explicitly asked not to make. Off means off, and the phase
        # completes rather than failing, because "no captions" is a valid
        # finished state, not an error.
        if ctx.channel is not None and ctx.channel_values.get("captions_enabled") is False:
            ctx.emit(
                ProgressEvent(
                    project_id=ctx.project.id, percent=100, message="Captions are switched off for this channel."
                )
            )
            return

        audio_path = _optimized_voice_path(ctx)
        if not audio_path.exists():
            raise AvgError(ErrorKind.bug, "captions phase: no finished audio/optimized_voice.mp3 to transcribe")

        scenes = read_json(script_dir(ctx) / "scenes.json")
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "captions phase: no finished scenes.json to derive caption text from")

        scene_texts_clean = [
            cleaned
            for scene in scenes
            if isinstance(scene, dict) and (cleaned := strip_delivery_markers(str(scene.get("text") or "")))
        ]
        if not scene_texts_clean:
            raise AvgError(ErrorKind.bug, "captions phase: scenes.json had no usable narration text")

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message="Transcribing narration..."))
        total_duration = audio_duration_seconds(audio_path)
        scene_windows = _scene_windows(scene_texts_clean, total_duration)

        words, timing_source = _get_words(ctx, audio_path, scene_texts_clean, scene_windows)
        if not words:
            raise AvgError(ErrorKind.bug, "captions phase: produced no usable words to caption")

        rdir = render_dir(ctx)
        rdir.mkdir(parents=True, exist_ok=True)

        card_plan = read_json(rdir / "card_plan.json")
        cards_in = card_plan.get("cards") or [] if isinstance(card_plan, dict) else []
        cards = _resolve_card_windows(cards_in, scene_windows)
        write_json(rdir / "card_plan.json", {"cards": cards})
        card_windows = _card_suppression_windows(cards_in, scene_windows)

        font_name = resolve_font_family(ctx)
        font_size = int(ctx.channel_values.get("captions_font_size") or 0) or _DEFAULT_FONT_SIZE
        position = str(ctx.channel_values.get("captions_position") or "bottom")
        width, height = _target_size(ctx.project.aspect_ratio, ctx.project.short_edge)

        animation_style = str(ctx.channel_values.get("captions_animation_style") or "fade")

        cues = _group_cues(words)
        lines = [_ass_header(width, height, font_name, font_size, position)]
        suppressed = 0
        for cue in cues:
            if any(_overlaps(cue["start"], cue["end"], cs, ce) for cs, ce in card_windows):
                suppressed += 1
                continue
            body = _animated_text(
                animation_style, cue, width=width, height=height, position=position, font_size=font_size
            )
            lines.append(_dialogue_line(cue["start"], cue["end"], body))

        (rdir / "captions.ass").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Hand the same words to the render's burn pass through the vendored
        # cache, so it neither re-uploads the audio nor re-pays for it, and
        # burns exactly the words these cards were timed against. The
        # provider name is part of the cache key, so it is recorded for
        # Render to pass back rather than guessed there.
        seed_transcript_cache(Path(ctx.project.folder), audio_path, timing_source, words)
        write_json(rdir / "_transcript_source.json", {"provider": timing_source})

        source_label = {
            "tts_native": "native TTS timing",
            "assemblyai": "AssemblyAI",
            "estimated": "estimated timing",
        }[timing_source]
        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id,
                percent=100,
                message=f"Captions complete ({len(cues) - suppressed}/{len(cues)} cue(s), {source_label}).",
            )
        )
