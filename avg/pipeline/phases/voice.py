"""Phase 4: Voice & Audio.

Runs after Visuals (Phase 3), before Director II (Phase 5). Reads the
finished script's flat narration (`script/narration.txt`, Pass 1's raw
prose - `avg.pipeline.phases.writing.ScriptPhase`), synthesizes it through
Slice 3's voice providers (`avg/providers/voice/`), and writes
`audio/raw_voice.mp3` + `audio/optimized_voice.mp3` - the same two filenames
the vendored render engine (`avg/media/vendor/avg.py`'s `render_project`/
`cmd_assemble`/`cmd_overlay_render`/`cmd_caption_burn`) already expects as
its one audio input, so a later render phase needs no new wiring to find
this phase's output.

**`[pause]` must become real silence, never spoken text** (this task's
named invariant, carried from the legacy app's B2.13 release - see
`docs`/this repo's `b213-dynamic-pauses` memory note). The old PowerShell
app fed the TTS engine a literal `" ... "` and hoped the voice paused on
it, then post-hoc resized whatever gap that produced. This phase does not
gamble on a provider's prosody: `_split_on_pause` cuts the narration into
segments AT each `[pause]` marker and drops the marker text entirely, so it
is never part of any string handed to `VoiceProvider.synthesize` - see
`test_pause_marker_text_never_reaches_the_provider` in this phase's test
module for the guard. Each segment is synthesized separately and the
segments are rejoined with an explicit `AudioSegment.silent(duration=...)`
gap (`_assemble_with_gaps`) - a real, exact-length silence, not a marker the
provider might or might not honour.

**Do not reimplement silence removal.** `avg.media.vendor.avg.
remove_silences_core` is the already-vendored, already-correct
threshold-based dead-air stripper; this phase calls it, once per segment,
before the deliberate `[pause]` gaps are (re-)inserted. That ordering
matters: `remove_silences_core` has no knowledge of the pause segmentation
this phase just did, so if the exact pause silence were already present in
the audio it hands the function, the same threshold algorithm that trims
ordinary dead air would crunch the deliberate gap down to `_KEEP_SILENCE_MS`
along with it. Cleaning each segment in isolation and only inserting the
exact gap length afterward keeps the two concerns - "trim natural TTS dead
air" and "hold this deliberate pause open for exactly N ms" - from fighting
each other, without this phase (or a future one) having to build the
legacy app's full `pause_map.json`/`scene_audio_map.json` dynamic-resize
machinery (`load_dynamic_pause_gaps`), which depends on a per-scene audio
splice this slice does not build yet.

`beat_map`/`timeline`/`scene_map` - `remove_silences_core`'s other three
protection inputs - are passed as `""` (its own documented "missing/absent"
value): they exist to protect specific beats/takeover-graphics gaps that
Director II (Phase 5) and Captions (Phase 6) haven't produced yet at this
point in the pipeline.

Settings not yet in `avg.core.settings_schema` (silence threshold, minimum
silence length, how much air to keep, and the dynamic-pause target length)
are fixed constants below, mirroring the legacy app's own shipped defaults
(`D:\\AI_Video_Gen\\main.ps1`'s `config.audio`/`config.voice.dynamic_pauses`
- `silence_thresh_dbfs=-40`, `min_silence_len_ms=400`, `keep_silence_ms=100`,
`dynamic_pauses.length_ms=700` for its default "medium" frequency) - the
same "generous, fixed default until a real setting exists" pattern
`avg.pipeline.phases.director`'s `_MIN_IMAGES`/`_MAX_IMAGES` already uses.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from avg.core.errors import AvgError, ErrorKind
from avg.core.events import ProgressEvent
from avg.media.vendor.avg import VOICE_MP3_BITRATE, remove_silences_core
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import (
    allocate_proportional,
    audio_duration_seconds,
    read_json,
    script_dir,
    strip_delivery_markers,
    write_json,
)
from avg.providers.transport import Transport
from avg.providers.voice.base import VoiceProvider
from avg.providers.voice.providers import EdgeVoiceProvider, ElevenLabsVoiceProvider

# Case/whitespace-tolerant, matching the legacy Convert-VoicePauseMarker regex
# (`D:\AI_Video_Gen\modules\pipeline.ps1`) - splitting ON this pattern both
# locates every marker AND strips it (and its surrounding whitespace) from
# the resulting segments in one step.
_PAUSE_RE = re.compile(r"\s*\[\s*pause\s*\]\s*", re.IGNORECASE)

_DEFAULT_PROVIDER = "elevenlabs"
_PROVIDERS: dict[str, type] = {
    "elevenlabs": ElevenLabsVoiceProvider,
    "edge": EdgeVoiceProvider,
}

# Legacy per-provider defaults (main.ps1's `voice` block) - used whenever a
# channel hasn't set `voice_id`/`voice_model_id` (the settings catalogue's
# own default is "", not a real id).
_DEFAULT_VOICE_ID = {"elevenlabs": "21m00Tcm4TlvDq8ikWAM", "edge": "en-US-ChristopherNeural"}
_DEFAULT_MODEL = {"elevenlabs": "eleven_flash_v2_5", "edge": "unused"}

# Legacy config.audio defaults (main.ps1) - see module docstring.
_SILENCE_THRESH_DBFS = -40.0
_MIN_SILENCE_LEN_MS = 400
_KEEP_SILENCE_MS = 100

# Legacy voice.dynamic_pauses.length_ms default for "medium" frequency.
_DEFAULT_PAUSE_MS = 700


def _split_on_pause(narration: str) -> list[str]:
    """Non-empty text segments between `[pause]` markers, each destined for
    its own `synthesize()` call. The markers themselves are consumed by the
    split and never appear in any returned segment - see module docstring's
    "must become real silence" invariant."""
    return [part.strip() for part in _PAUSE_RE.split(narration) if part.strip()]


def _strip_pause_markers(narration: str) -> str:
    """`narration` with every `[pause]` marker collapsed to a single space -
    used when dynamic pauses are disabled: the text still must never reach
    a provider with the literal marker in it, but no deliberate gap is
    inserted either (mirrors the legacy app's own disabled-branch
    behaviour, `Convert-VoicePauseMarker`)."""
    return " ".join(_PAUSE_RE.sub(" ", narration).split())


def _build_provider(name: str, transport: Transport | None) -> VoiceProvider:
    """One voice provider instance for `name`, falling back to the
    catalogue default for an unrecognised name. `EdgeVoiceProvider` takes
    no `transport` - it never goes through `avg.providers.transport.
    Transport` at all (see that module's docstring) - so it is constructed
    separately rather than through one uniform call shape."""
    if name == "edge":
        return EdgeVoiceProvider()
    cls = _PROVIDERS.get(name, ElevenLabsVoiceProvider)
    return cls(transport=transport)


def _narration_path(ctx: PhaseContext) -> Path:
    return script_dir(ctx) / "narration.txt"


def _audio_dir(ctx: PhaseContext) -> Path:
    return Path(ctx.project.folder) / "audio"


def _assemble_with_gaps(segment_paths: list[Path], gap_ms: int, out_path: Path) -> None:
    """Concatenate `segment_paths` (already-encoded mp3 files, in order)
    into one mp3 at `out_path`, inserting an exact `gap_ms` silence between
    each pair - the ONE place this phase touches `pydub` for audio
    manipulation (as opposed to `remove_silences_core`'s own, separate,
    threshold-based dead-air removal). Deferred import, matching
    `avg.media.vendor.avg`'s own lazy `pydub` import pattern, so merely
    importing this module never requires the `media` extra to be
    installed - only actually calling this function does."""
    from pydub import AudioSegment

    combined = AudioSegment.empty()
    for i, path in enumerate(segment_paths):
        if i > 0 and gap_ms > 0:
            combined += AudioSegment.silent(duration=gap_ms)
        combined += AudioSegment.from_file(path, format="mp3")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Explicit bitrate for the same reason avg.py's own splice/remove_silences
    # exports set it (see that module's `VOICE_MP3_BITRATE` comment): this
    # voice track gets re-encoded twice (raw assembly, then again after
    # per-segment cleaning) and pydub's default export has no bitrate at all,
    # which lets ffmpeg pick a low one and degrade a little more each pass.
    combined.export(str(out_path), format="mp3", bitrate=VOICE_MP3_BITRATE)


def scene_audio_map(scene_texts: list[str], total_duration: float) -> dict[str, object]:
    """`audio/scene_audio_map.json` - per-scene windows over the finished
    narration.

    Legacy produced this by silence-detecting the TTS output
    (`splice`/`splice_flat`). This phase synthesises per PAUSE-separated
    SEGMENT, not per scene, so segment boundaries are not scene boundaries
    and there is nothing to detect. The windows are therefore allocated
    proportionally over the real measured duration - the same deterministic
    split Screen Text uses for caption windows, from the one shared
    allocator in `_common`, so the two files cannot disagree.

    Without this file `compute_scene_timings` drops every scene and raises
    (`avg/media/vendor/avg.py:2009`), and `render_project` falls back to
    dividing the track equally, which desyncs any video whose scenes differ
    in length.
    """
    windows = allocate_proportional(list(scene_texts), float(total_duration))
    return {
        "total_duration": round(float(total_duration), 3),
        "scenes": [
            {"scene": i, "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)}
            for i, (start, end) in enumerate(windows, start=1)
        ],
    }


@dataclass(frozen=True, slots=True)
class VoicePhase:
    id: int = 4
    key: str = "voice"
    name: str = "Voice & Audio"

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return _narration_path(ctx).exists()

    def run(self, ctx: PhaseContext) -> None:
        try:
            narration = _narration_path(ctx).read_text(encoding="utf-8")
        except OSError as exc:
            raise AvgError(ErrorKind.bug, "voice phase: no finished narration.txt to synthesize") from exc
        if not narration.strip():
            raise AvgError(ErrorKind.bug, "voice phase: narration.txt is empty")

        settings = ctx.channel.settings
        provider_name = str(settings.get("voice_provider") or _DEFAULT_PROVIDER)
        voice_id = str(settings.get("voice_id") or "").strip() or _DEFAULT_VOICE_ID.get(
            provider_name, _DEFAULT_VOICE_ID[_DEFAULT_PROVIDER]
        )
        model = str(settings.get("voice_model_id") or "").strip() or _DEFAULT_MODEL.get(
            provider_name, _DEFAULT_MODEL[_DEFAULT_PROVIDER]
        )
        pauses_enabled = bool(settings.get("dynamic_pauses_enabled", True))

        if pauses_enabled:
            segments = _split_on_pause(narration)
        else:
            segments = [_strip_pause_markers(narration)]
        segments = [s for s in segments if s]
        if not segments:
            raise AvgError(ErrorKind.bug, "voice phase: narration had no speakable text")

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)

        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id, percent=0, message=f"Synthesizing narration ({len(segments)} part(s))..."
            )
        )
        raw_audio = [provider.synthesize(segment, voice_id=voice_id, model=model) for segment in segments]

        gap_ms = _DEFAULT_PAUSE_MS if (pauses_enabled and len(segments) > 1) else 0
        audio_dir = _audio_dir(ctx)
        audio_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="avg-voice-") as tmp:
            tmp_path = Path(tmp)
            raw_paths = []
            for i, data in enumerate(raw_audio, start=1):
                p = tmp_path / f"raw_{i:03d}.mp3"
                p.write_bytes(data)
                raw_paths.append(p)

            _assemble_with_gaps(raw_paths, gap_ms, audio_dir / "raw_voice.mp3")

            ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=60, message="Removing dead air..."))

            cleaned_paths = []
            for i, raw_path in enumerate(raw_paths, start=1):
                clean_path = tmp_path / f"clean_{i:03d}.mp3"
                remove_silences_core(
                    str(raw_path),
                    str(clean_path),
                    _SILENCE_THRESH_DBFS,
                    _MIN_SILENCE_LEN_MS,
                    _KEEP_SILENCE_MS,
                    "",
                    "",
                    "",
                )
                cleaned_paths.append(clean_path)

            _assemble_with_gaps(cleaned_paths, gap_ms, audio_dir / "optimized_voice.mp3")

        # Best-effort: the narration itself is already on disk and usable.
        # A duration probe that fails (a truncated mp3, an ffmpeg that
        # cannot decode it) costs the render its exact per-scene windows -
        # it falls back to dividing the track equally - and must not throw
        # away audio the user has already paid a provider for.
        try:
            _write_scene_audio_map(ctx, audio_dir)
        except Exception as exc:  # noqa: BLE001 - see above
            ctx.emit(
                ProgressEvent(
                    project_id=ctx.project.id,
                    percent=100,
                    message=f"Per-scene audio map could not be measured ({exc}); the render will divide the track evenly.",
                )
            )

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message="Voice & Audio complete."))


def _write_scene_audio_map(ctx: PhaseContext, audio_dir: Path) -> None:
    """Write `audio/scene_audio_map.json`, if there are scenes to map.

    Measured from `optimized_voice.mp3` - the file the render actually
    reads - rather than from the sum of the pre-trim segments:
    `remove_silences_core` shortens the audio, so a map built before it
    would run late by however much dead air was cut.

    No `scenes.json` means no map is written at all. This phase reads
    `narration.txt`, so it can legitimately run without one (a legacy
    project, a hand-edited folder), and an invented map would be worse
    than none: `compute_scene_timings` drops the scenes a map does not
    mention.
    """
    scenes = read_json(script_dir(ctx) / "scenes.json")
    if not isinstance(scenes, list):
        return
    scene_texts = [
        cleaned
        for scene in scenes
        if isinstance(scene, dict) and (cleaned := strip_delivery_markers(str(scene.get("text") or "")))
    ]
    if not scene_texts:
        return
    write_json(
        audio_dir / "scene_audio_map.json",
        scene_audio_map(scene_texts, audio_duration_seconds(audio_dir / "optimized_voice.mp3")),
    )
