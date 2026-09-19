# Slice 32 (sub-slice 3) — Captions: TTS-Native Word Timing: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** most users' captions stop running on a character-count guess.
TTS-native word timing — free, always available whenever the voice
provider supports it — becomes the new default priority, ahead of
AssemblyAI, ahead of the proportional estimate.

**Architecture:** both voice providers gain an optional
`synthesize_with_timing` method returning `(audio, words | None)` —
additive, `synthesize` itself untouched. `avg/pipeline/phases/voice.py`
calls it when available, and threads the captured per-segment word list
through `_clean_segment`'s silence-removal pass using a mechanism this
plan does not invent: `remove_silences_core`'s own `scene_map` parameter
already remaps any `{"scenes": [{"start", "end"}, ...]}` file through
whatever it just cleaned, regardless of what the spans mean — a throwaway
file holding word timings instead of scene timings reuses that path
verbatim. `avg/pipeline/phases/captions.py`'s `_get_words` becomes a
3-way priority instead of AssemblyAI-or-estimate.

**The remap mechanism was proven correct against real audio, not just
reasoned about** — see Task 3's own verification note. A synthetic 2-word
clip with a real 1-second silence gap, run through the actual vendored
`remove_silences_core` (real ffmpeg, real pydub), produced word timings
shifted by exactly the amount of silence actually removed. This is the
plan's most novel mechanism and the one most worth not taking on faith.

**Tech Stack:** Python 3, pytest, `pydub`+`ffmpeg` (already runtime
dependencies of this phase; not needed to run this plan's own mocked-level
tests, only the one real-audio proof in Task 3).

**Spec:** `docs/superpowers/specs/2026-09-13-slice-32-captions-native-timing-design.md`

## Global Constraints

- No live API calls in tests. Provider-level tests use `mode="record"`
  against in-process fakes, matching `tests/providers/test_voice.py`'s
  existing convention. Phase-level tests mock `remove_silences_core`/
  `_assemble_with_gaps`/`audio_duration_seconds`, matching `tests/
  pipeline/test_phases_media.py`'s existing convention for this exact
  phase (that file's own comment: "pydub is not installed in this test
  venv").
- `avg/media/vendor/avg.py` is never edited. The whole design of Task 3
  exists specifically to avoid needing to.
- AssemblyAI support is not removed — `"assemblyai"`/`"estimated"` remain
  explicit overrides on `captions_timing_source`.
- Verify with `.venv-linux/bin/python -m pytest tests -q` in this
  sandbox; the real gate is `.venv-build\Scripts\python.exe -m pytest
  tests -q` on Windows.

## Files touched

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | +1 `SettingSpec` (`captions_timing_source`) |
| `avg/providers/voice/providers.py` | `ElevenLabsVoiceProvider`: `_call` gains `with_timestamps`; new `synthesize_with_timing`, refactored `_synthesize` shared with `synthesize`; new `_words_from_alignment`. `EdgeVoiceProvider`: new `synthesize_with_timing` |
| `avg/pipeline/phases/voice.py` | Synthesis loop captures word timings when available; `_clean_segment` gains the remap round trip; new `_write_word_timings` |
| `avg/pipeline/phases/captions.py` | `_get_words` becomes a 3-way priority; `run()`'s provider/source variables follow |
| `tests/core/test_settings_schema.py`, `tests/providers/test_voice.py`, `tests/pipeline/test_phases_media.py`, `tests/pipeline/test_phases_polish.py` | New/extended tests per task |

---

## Task 1 — `captions_timing_source` setting

### Step 1.1 — Test

Add to `tests/core/test_settings_schema.py`:

```python
def test_captions_timing_source_setting_exists():
    ids = {s.id: s for s in SETTINGS}
    s = ids["captions_timing_source"]
    assert s.default == "auto"
    assert s.invalidates == "screen_text"
    assert s.scope == "channel"
    assert s.choices == ["auto", "tts_native", "assemblyai", "estimated"]
```

Run it — fails, the id doesn't exist yet.

### Step 1.2 — Implementation

**Verified against the real file before writing this block.**
`font_family`'s entry is the last one in `group="captions"` before the
`# --- overlay cards` section comment. Replace:

```python
    SettingSpec(
        id="font_family",
        group="captions",
        label="Font",
        help=(
            "The GLOBAL font shared by captions AND takeover cards - one setting, one "
            "look. \"Custom\" accepts any family name or a .ttf/.otf path, for anything "
            "not in this shortlist."
        ),
        type="choice",
        default="",
        scope="channel",
        choices=["", "Arial", "Segoe UI", "Calibri", "Georgia", "Verdana", "Impact", "Times New Roman", "custom"],
        invalidates="screen_text",
    ),
    # --- overlay cards
```

with:

```python
    SettingSpec(
        id="font_family",
        group="captions",
        label="Font",
        help=(
            "The GLOBAL font shared by captions AND takeover cards - one setting, one "
            "look. \"Custom\" accepts any family name or a .ttf/.otf path, for anything "
            "not in this shortlist."
        ),
        type="choice",
        default="",
        scope="channel",
        choices=["", "Arial", "Segoe UI", "Calibri", "Georgia", "Verdana", "Impact", "Times New Roman", "custom"],
        invalidates="screen_text",
    ),
    SettingSpec(
        id="captions_timing_source",
        group="captions",
        label="Caption timing source",
        help=(
            "\"Auto\" prefers native TTS word timing (free, from the voice provider "
            "itself) over AssemblyAI, over an estimated guess - in that order, using "
            "whichever is actually available. Force one explicitly to override."
        ),
        type="choice",
        default="auto",
        scope="channel",
        choices=["auto", "tts_native", "assemblyai", "estimated"],
        invalidates="screen_text",
    ),
    # --- overlay cards
```

Run Step 1.1's test — passes. **Mechanically verified**: applied to a real
checkout, `SETTINGS` grows from 67 to 68 entries cleanly.

**Commit:** `feat(settings): add captions_timing_source (auto/tts_native/assemblyai/estimated)`

---

## Task 2 — `synthesize_with_timing` on both voice providers

### Step 2.1 — Test

Add to `tests/providers/test_voice.py` (mirrors this file's existing
`_patch_keys`/`_mock_record_transport`/`PRIMARY_KEY`/`BACKUP_KEY`/
`FAKE_AUDIO`/`VOICE_ID`/`MODEL` fixtures — all already in this file):

```python
from avg.providers.voice.providers import _words_from_alignment


def test_words_from_alignment_groups_characters_at_whitespace():
    alignment = {
        "characters": list("hi there"),
        "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    }
    words = _words_from_alignment(alignment)
    assert words == [
        {"text": "hi", "start": 0.0, "end": 0.2},
        {"text": "there", "start": 0.3, "end": 0.8},
    ]


def test_words_from_alignment_handles_leading_and_multiple_spaces():
    alignment = {
        "characters": list(" a  b"),
        "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4],
        "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5],
    }
    words = _words_from_alignment(alignment)
    assert words == [
        {"text": "a", "start": 0.1, "end": 0.2},
        {"text": "b", "start": 0.4, "end": 0.5},
    ]


def test_words_from_alignment_returns_none_on_malformed_input():
    assert _words_from_alignment(None) is None
    assert _words_from_alignment({}) is None
    assert _words_from_alignment({"characters": ["a"], "character_start_times_seconds": []}) is None


def test_elevenlabs_synthesize_with_timing_hits_the_with_timestamps_endpoint(monkeypatch, tmp_path):
    import base64 as base64_module

    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=BACKUP_KEY)
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "audio_base64": base64_module.b64encode(FAKE_AUDIO).decode(),
                "alignment": {
                    "characters": list("hi"),
                    "character_start_times_seconds": [0.0, 0.1],
                    "character_end_times_seconds": [0.1, 0.2],
                },
            },
        )

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    audio, words = provider.synthesize_with_timing("hi", voice_id=VOICE_ID, model=MODEL)

    assert audio == FAKE_AUDIO
    assert words == [{"text": "hi", "start": 0.0, "end": 0.2}]
    assert seen_urls[0].endswith("/with-timestamps")


def test_elevenlabs_synthesize_with_timing_shares_the_key_switch_rule(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, primary=PRIMARY_KEY, backup=BACKUP_KEY)
    seen_keys = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["xi-api-key"]
        seen_keys.append(key)
        if key == PRIMARY_KEY:
            return httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})
        import base64 as base64_module

        return httpx.Response(200, json={"audio_base64": base64_module.b64encode(FAKE_AUDIO).decode()})

    provider = ElevenLabsVoiceProvider(transport=_mock_record_transport(tmp_path, handler))
    audio, words = provider.synthesize_with_timing("hi", voice_id=VOICE_ID, model=MODEL)

    assert audio == FAKE_AUDIO
    assert words is None  # no alignment in the backup's response
    assert seen_keys == [PRIMARY_KEY, BACKUP_KEY]


def test_edge_synthesize_with_timing_captures_word_boundaries(monkeypatch):
    class _FakeCommunicate:
        def __init__(self, *a, **k):
            pass

        def stream_sync(self):
            yield {"type": "audio", "data": FAKE_AUDIO}
            yield {"type": "WordBoundary", "offset": 0, "duration": 1_000_000, "text": "hi"}
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "there"}

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _FakeCommunicate)

    provider = EdgeVoiceProvider()
    audio, words = provider.synthesize_with_timing("hi there", voice_id="en-US-GuyNeural", model="")

    assert audio == FAKE_AUDIO
    assert words is not None
    assert len(words) == 2
    assert words[0]["text"] == "hi"
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[0]["end"] == pytest.approx(0.1)
    assert words[1]["text"] == "there"
    assert words[1]["start"] == pytest.approx(0.1)
    assert words[1]["end"] == pytest.approx(0.3)


def test_edge_synthesize_with_timing_returns_none_words_when_stream_has_none(monkeypatch):
    class _FakeCommunicate:
        def __init__(self, *a, **k):
            pass

        def stream_sync(self):
            yield {"type": "audio", "data": FAKE_AUDIO}

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _FakeCommunicate)

    provider = EdgeVoiceProvider()
    audio, words = provider.synthesize_with_timing("hi", voice_id="en-US-GuyNeural", model="")

    assert audio == FAKE_AUDIO
    assert words is None
```

Run these — fail, `synthesize_with_timing` doesn't exist on either class
yet. **One real test bug caught and fixed here**: an earlier draft of the
Edge test asserted exact float equality (`words[1]["end"] == 0.3`), which
fails on floating-point rounding from the `/ 1e7` tick conversion
(`0.30000000000000004 != 0.3`) — fixed to `pytest.approx`, as written
above.

### Step 2.2 — Implementation

**Verified against the real file before writing these blocks.** In
`avg/providers/voice/providers.py`, replace the import block:

```python
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any
```

with:

```python
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any
```

Insert `_words_from_alignment` right before `class ElevenLabsVoiceProvider`
— replace:

```python
_BACKUP_KEY_NAME = "elevenlabs_backup"


class ElevenLabsVoiceProvider:
```

with:

```python
_BACKUP_KEY_NAME = "elevenlabs_backup"


def _words_from_alignment(alignment: Any) -> list[dict[str, Any]] | None:
    """Group ElevenLabs' character-level `/with-timestamps` alignment into
    words, split at whitespace boundaries - a word's `start` is its first
    character's start, `end` is its last non-space character's end.

    `None` on anything missing or malformed - never raises, since a timing
    detail must not turn an already-successful synthesis call into a
    failure. The caller already has real audio bytes by the time this runs;
    losing only the timing half is the correct degradation.
    """
    if not isinstance(alignment, dict):
        return None
    characters = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not (isinstance(characters, list) and isinstance(starts, list) and isinstance(ends, list)):
        return None
    if not (len(characters) == len(starts) == len(ends)):
        return None

    words: list[dict[str, Any]] = []
    current = ""
    current_start: float | None = None
    current_end: float | None = None
    for ch, start, end in zip(characters, starts, ends):
        if str(ch).isspace():
            if current:
                words.append({"text": current, "start": current_start, "end": current_end})
                current, current_start, current_end = "", None, None
            continue
        if current_start is None:
            try:
                current_start = float(start)
            except (TypeError, ValueError):
                return None
        current += str(ch)
        try:
            current_end = float(end)
        except (TypeError, ValueError):
            return None
    if current:
        words.append({"text": current, "start": current_start, "end": current_end})
    return words or None


class ElevenLabsVoiceProvider:
```

Replace `synthesize`/`_call` in full — this refactors the shared
primary/backup key-switch rule into `_synthesize`, used by both
`synthesize` and the new `synthesize_with_timing`:

```python
    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text`, or raise `AvgError`.

        Tries the primary key first. On `auth`/`quota` failure, retries
        exactly once against the configured backup key (if any is
        configured) - never more than once, and never for any other
        failure kind (see the section comment above, especially the 5xx
        case).
        """
        primary_key = get_key(self.name)
        if not primary_key:
            raise AvgError(ErrorKind.auth, "ElevenLabs API key is not set")

        try:
            return self._call(text, voice_id=voice_id, model=model, key=primary_key)
        except AvgError as exc:
            if exc.kind not in _KEY_SWITCH_KINDS:
                raise

            backup_key = get_key(_BACKUP_KEY_NAME)
            if not backup_key:
                # No second key to try - the original failure is the only
                # thing there is to report. Matches the legacy behaviour of
                # saying so explicitly rather than silently no-op'ing.
                raise

            # A key switch is not a second attempt at the same thing - it is
            # a different account. Exactly one retry, against the backup,
            # whatever the outcome.
            return self._call(text, voice_id=voice_id, model=model, key=backup_key)

    def _call(self, text: str, *, voice_id: str, model: str, key: str) -> bytes:
        response = self._transport.request(
            "POST",
            f"{_BASE_URL}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={"text": text, "model_id": model},
            timeout=_SYNTHESIZE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        content = response.content
        if not content:
            raise AvgError(ErrorKind.bug, "ElevenLabs returned no audio data")
        return content
```

with:

```python
    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text`, or raise `AvgError`.

        Tries the primary key first. On `auth`/`quota` failure, retries
        exactly once against the configured backup key (if any is
        configured) - never more than once, and never for any other
        failure kind (see the section comment above, especially the 5xx
        case).
        """
        audio, _words = self._synthesize(text, voice_id=voice_id, model=model, with_timestamps=False)
        return audio

    def synthesize_with_timing(
        self, text: str, *, voice_id: str, model: str
    ) -> tuple[bytes, list[dict[str, Any]] | None]:
        """Like `synthesize`, plus a per-word timing list from ElevenLabs'
        `/with-timestamps` endpoint (same request body, a different URL and
        response shape) - shares this class's own primary/backup key-switch
        rule via `_synthesize`, rather than restating it a second time."""
        return self._synthesize(text, voice_id=voice_id, model=model, with_timestamps=True)

    def _synthesize(
        self, text: str, *, voice_id: str, model: str, with_timestamps: bool
    ) -> tuple[bytes, list[dict[str, Any]] | None]:
        """The primary/backup key-switch rule, stated once - both
        `synthesize` and `synthesize_with_timing` go through this."""
        primary_key = get_key(self.name)
        if not primary_key:
            raise AvgError(ErrorKind.auth, "ElevenLabs API key is not set")

        try:
            return self._call(text, voice_id=voice_id, model=model, key=primary_key, with_timestamps=with_timestamps)
        except AvgError as exc:
            if exc.kind not in _KEY_SWITCH_KINDS:
                raise

            backup_key = get_key(_BACKUP_KEY_NAME)
            if not backup_key:
                # No second key to try - the original failure is the only
                # thing there is to report. Matches the legacy behaviour of
                # saying so explicitly rather than silently no-op'ing.
                raise

            # A key switch is not a second attempt at the same thing - it is
            # a different account. Exactly one retry, against the backup,
            # whatever the outcome.
            return self._call(text, voice_id=voice_id, model=model, key=backup_key, with_timestamps=with_timestamps)

    def _call(
        self, text: str, *, voice_id: str, model: str, key: str, with_timestamps: bool = False
    ) -> tuple[bytes, list[dict[str, Any]] | None]:
        suffix = "/with-timestamps" if with_timestamps else ""
        response = self._transport.request(
            "POST",
            f"{_BASE_URL}/text-to-speech/{voice_id}{suffix}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "application/json" if with_timestamps else "audio/mpeg",
            },
            json={"text": text, "model_id": model},
            timeout=_SYNTHESIZE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        if with_timestamps:
            payload = response.json()
            audio_b64 = payload.get("audio_base64")
            if not audio_b64:
                raise AvgError(ErrorKind.bug, "ElevenLabs returned no audio data")
            words = _words_from_alignment(payload.get("alignment"))
            return base64.b64decode(audio_b64), words

        content = response.content
        if not content:
            raise AvgError(ErrorKind.bug, "ElevenLabs returned no audio data")
        return content, None
```

Add `EdgeVoiceProvider.synthesize_with_timing` right after `synthesize`.
Replace:

```python
        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio

    def list_voices(self) -> list[dict[str, str]]:
```

with:

```python
        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio

    def synthesize_with_timing(
        self, text: str, *, voice_id: str, model: str
    ) -> tuple[bytes, list[dict[str, Any]] | None]:
        """Like `synthesize`, plus a per-word timing list - edge-tts's own
        stream already includes `WordBoundary` chunks alongside the audio
        ones, previously discarded by `synthesize`'s `_collect`. `offset`/
        `duration` arrive in 100-nanosecond ticks (edge-tts's own unit);
        `/ 1e7` converts to seconds.

        Returns `(audio, None)` rather than raising when the word-boundary
        half of the stream is unusable - the audio this call already
        produced must not be thrown away over a timing detail. A failure to
        produce audio at all still raises, exactly like `synthesize`.
        """
        del model

        def _collect() -> tuple[bytes, list[dict[str, Any]]]:
            communicate = _edge().Communicate(text, voice_id)
            audio = bytearray()
            words: list[dict[str, Any]] = []
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    audio.extend(chunk["data"])
                elif chunk.get("type") == "WordBoundary":
                    try:
                        offset_s = float(chunk["offset"]) / 1e7
                        duration_s = float(chunk["duration"]) / 1e7
                    except (KeyError, TypeError, ValueError):
                        continue
                    words.append(
                        {"text": str(chunk.get("text", "")), "start": offset_s, "end": offset_s + duration_s}
                    )
            return bytes(audio), words

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            audio, words = pool.submit(_collect).result(timeout=_EDGE_SYNTHESIZE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc
        finally:
            pool.shutdown(wait=False)

        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio, (words or None)

    def list_voices(self) -> list[dict[str, str]]:
```

Run Step 2.1's tests — pass, including the `pytest.approx` fix. **Mechanically
verified**: full `tests/providers/test_voice.py` run, 19/19 (12
pre-existing + 7 new), no regressions.

**Commit:** `feat(providers): synthesize_with_timing on ElevenLabs and Edge TTS`

---

## Task 3 — `voice.py`: capture, remap, offset-merge

This is the plan's most novel mechanism. Read the plan's own preamble
before starting — the verification note below is not a formality, it is
the actual proof this mechanism works, run against real audio before this
plan was finalized.

### Step 3.1 — Real, unmocked proof of the remap mechanism

Before touching `voice.py` at all, this exact scenario was run directly
against the real `_clean_segment` (after Step 3.2's implementation) and
the real vendored `remove_silences_core` — no mocks, real `pydub`, real
`ffmpeg`:

```python
from pydub import AudioSegment
from pydub.generators import Sine

# 0.5s tone, 1.0s silence, 0.5s tone
tone1 = Sine(440).to_audio_segment(duration=500).apply_gain(-10)
silence = AudioSegment.silent(duration=1000)
tone2 = Sine(440).to_audio_segment(duration=500).apply_gain(-10)
(tone1 + silence + tone2).export(raw_path, format="mp3")

word_timings = [
    {"text": "hello", "start": 0.1, "end": 0.4},  # inside the first tone
    {"text": "world", "start": 1.6, "end": 1.9},  # inside the second tone
]

result = _clean_segment(
    None, segment_index=1, raw_path=raw_path, clean_path=clean_path,
    aggressiveness="medium", max_removable_pct=100.0,
    word_timings=word_timings, scene_map_path=scene_map_path,
)
```

Actual output: `0.90s removed` (of the 1.0s silence — the `medium`
preset's 100ms `keep_silence` padding on each side accounts for the other
0.1s), and the remapped result was **`[{"text": "hello", "start": 0.1,
"end": 0.4}, {"text": "world", "start": 0.7, "end": 1.0}]`** — `"hello"`
(before the removed gap) unchanged; `"world"` shifted from `[1.6, 1.9]`
to `[0.7, 1.0]`, exactly the 0.9s that was actually removed. This is the
mechanism working precisely, not approximately — it is not a design that
merely seems plausible.

### Step 3.2 — Test (mocked level, matching this file's existing convention)

Add to `tests/pipeline/test_phases_media.py`, after `_FakeVoiceProvider`:

```python
class _FakeTimingVoiceProvider(_FakeVoiceProvider):
    """`_FakeVoiceProvider` plus `synthesize_with_timing` - one word per
    segment, spanning a fixed 0.0-1.0s window in that segment's own
    pre-clean timeline, so the offset math after concatenation is easy to
    hand-verify."""

    def synthesize_with_timing(self, text, *, voice_id, model):
        self.calls.append((text, voice_id, model))
        return f"fake-audio:{text}".encode(), [{"text": text.split()[0], "start": 0.0, "end": 1.0}]


class _FakePartialTimingVoiceProvider(_FakeVoiceProvider):
    """Has `synthesize_with_timing`, but its second call returns no words -
    the "whole-run, not partial" case."""

    def synthesize_with_timing(self, text, *, voice_id, model):
        self.calls.append((text, voice_id, model))
        words = None if len(self.calls) == 2 else [{"text": "word", "start": 0.0, "end": 1.0}]
        return f"fake-audio:{text}".encode(), words


def test_voice_writes_word_timings_when_the_provider_supports_it(tmp_path, monkeypatch):
    fake = _FakeTimingVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First half. [pause] Second half.")

    VoicePhase().run(ctx)

    timings = read_json(script_dir(ctx).parent / "audio" / "word_timings.json")
    assert timings is not None
    words = timings["words"]
    assert [w["text"] for w in words] == ["First", "Second"]
    # Segment 1: offset 0 -> word at [0.0, 1.0]. Segment 2: offset =
    # segment 1's 10.0s (mocked) duration + the pause gap in seconds.
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[0]["end"] == pytest.approx(1.0)
    assert words[1]["start"] > 10.0  # strictly after segment 1's own duration


def test_voice_does_not_write_word_timings_without_provider_support(tmp_path, monkeypatch):
    fake = _FakeVoiceProvider()  # no synthesize_with_timing
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "Some narration.")

    VoicePhase().run(ctx)

    assert not (script_dir(ctx).parent / "audio" / "word_timings.json").exists()


def test_voice_writes_no_word_timings_when_any_segment_lacks_them(tmp_path, monkeypatch):
    fake = _FakePartialTimingVoiceProvider()
    _patch_voice_collaborators(monkeypatch, fake)
    monkeypatch.setattr("avg.pipeline.phases.voice.audio_duration_seconds", lambda path: 10.0)

    project = _project(tmp_path)
    ctx, _ = _ctx(project)
    _write_narration(ctx, "First half. [pause] Second half.")

    VoicePhase().run(ctx)  # must not raise - a missing timing degrades, not a phase failure

    assert not (script_dir(ctx).parent / "audio" / "word_timings.json").exists()
```

Run these — fail, no capture/remap/offset-merge wiring exists yet.

### Step 3.3 — Implementation

**Verified against the real file before writing these blocks.**

Replace the synthesis line in `run()`:

```python
        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id, percent=0, message=f"Synthesizing narration ({len(segments)} part(s))..."
            )
        )
        raw_audio = [provider.synthesize(segment, voice_id=voice_id, model=model) for segment in segments]
```

with:

```python
        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id, percent=0, message=f"Synthesizing narration ({len(segments)} part(s))..."
            )
        )
        raw_audio: list[bytes] = []
        raw_word_timings: list[list[dict[str, Any]] | None] = []
        supports_timing = hasattr(provider, "synthesize_with_timing")
        for segment in segments:
            if supports_timing:
                data, words = provider.synthesize_with_timing(segment, voice_id=voice_id, model=model)
            else:
                data, words = provider.synthesize(segment, voice_id=voice_id, model=model), None
            raw_audio.append(data)
            raw_word_timings.append(words)
        # Whole-run, not partial: if any segment's timing came back empty,
        # word_timings.json is not written at all for this run - Captions'
        # own AssemblyAI/estimated fallback handles the whole track instead,
        # rather than stitching native timing for some segments and a guess
        # for others.
        word_timings_available = supports_timing and all(w is not None for w in raw_word_timings)
```

Replace the segment-cleaning loop and assembly call:

```python
            cleaned_paths = []
            for i, raw_path in enumerate(raw_paths, start=1):
                clean_path = tmp_path / f"clean_{i:03d}.mp3"
                _clean_segment(
                    ctx,
                    segment_index=i,
                    raw_path=raw_path,
                    clean_path=clean_path,
                    aggressiveness=aggressiveness,
                    max_removable_pct=max_removable_pct,
                )
                cleaned_paths.append(clean_path)

            _assemble_with_gaps(cleaned_paths, gap_ms, audio_dir / "optimized_voice.mp3")
```

with:

```python
            cleaned_paths = []
            remapped_word_timings: list[list[dict[str, Any]]] = []
            for i, raw_path in enumerate(raw_paths, start=1):
                clean_path = tmp_path / f"clean_{i:03d}.mp3"
                segment_words = raw_word_timings[i - 1] if word_timings_available else None
                word_map_path = tmp_path / f"word_map_{i:03d}.json" if segment_words is not None else None
                remapped = _clean_segment(
                    ctx,
                    segment_index=i,
                    raw_path=raw_path,
                    clean_path=clean_path,
                    aggressiveness=aggressiveness,
                    max_removable_pct=max_removable_pct,
                    word_timings=segment_words,
                    scene_map_path=word_map_path,
                )
                cleaned_paths.append(clean_path)
                if word_timings_available:
                    remapped_word_timings.append(remapped or [])

            _assemble_with_gaps(cleaned_paths, gap_ms, audio_dir / "optimized_voice.mp3")

            if word_timings_available and remapped_word_timings:
                _write_word_timings(audio_dir, remapped_word_timings, cleaned_paths, gap_ms)
```

Replace `_clean_segment` in full, and add `_write_word_timings` right
after it:

```python
def _clean_segment(
    ctx: PhaseContext,
    *,
    segment_index: int,
    raw_path: Path,
    clean_path: Path,
    aggressiveness: str,
    max_removable_pct: float,
) -> None:
    """Run `remove_silences_core` once at `aggressiveness`; if it removed
    more than `max_removable_pct` of the segment's own original length,
    retry exactly once at one level less aggressive (a no-op retry if
    already at "low" - see `_AGGRESSIVENESS_STEP_DOWN`). If it's still
    over the cap after that, keep the retried result anyway and report
    the degradation - never a second retry, never silently discarding
    audio the user already paid a provider for.
    """
    thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[aggressiveness]
    result = remove_silences_core(  # type: ignore[no-untyped-call]  # the vendored engine is untyped by design
        str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", ""
    )
    removed_pct = _removed_pct(result)
    if removed_pct is None or removed_pct <= max_removable_pct:
        return

    fallback_level = _AGGRESSIVENESS_STEP_DOWN[aggressiveness]
    if fallback_level != aggressiveness:
        thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[fallback_level]
        result = remove_silences_core(  # type: ignore[no-untyped-call]  # the vendored engine is untyped by design
            str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", ""
        )
        removed_pct = _removed_pct(result)
        aggressiveness = fallback_level

    if removed_pct is not None and removed_pct > max_removable_pct:
        report_degradation(
            ctx,
            f"Segment {segment_index}: dead-air removal cut {removed_pct:.0f}% of the "
            f"audio, above the {max_removable_pct:.0f}% cap even at \"{aggressiveness}\" "
            "- kept the result anyway rather than discard synthesized audio.",
        )
```

with:

```python
def _clean_segment(
    ctx: PhaseContext,
    *,
    segment_index: int,
    raw_path: Path,
    clean_path: Path,
    aggressiveness: str,
    max_removable_pct: float,
    word_timings: list[dict[str, Any]] | None = None,
    scene_map_path: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Run `remove_silences_core` once at `aggressiveness`; if it removed
    more than `max_removable_pct` of the segment's own original length,
    retry exactly once at one level less aggressive (a no-op retry if
    already at "low" - see `_AGGRESSIVENESS_STEP_DOWN`). If it's still
    over the cap after that, keep the retried result anyway and report
    the degradation - never a second retry, never silently discarding
    audio the user already paid a provider for.

    When `word_timings` is given (this segment's own pre-clean per-word
    spans, in seconds), `scene_map_path` is used to remap them through
    whichever call's result is ultimately kept - reusing `remove_silences_
    core`'s own general-purpose (start, end)-span remap
    (`_remap_scene_audio_map`, which reads back whatever `{"scenes": [...]}`
    file it's given regardless of what the spans semantically represent),
    never a second implementation of the same checkpoint logic. Each call
    writes a fresh copy from the ORIGINAL `word_timings`, never chained
    through a prior attempt's remap - `remove_silences_core` computes its
    checkpoints from the original raw audio every time, so remapping from
    anything but the original would silently double-apply. Returns the
    remapped word list, or `None` when no timing was requested.
    """

    def _write_word_scene_map() -> None:
        if scene_map_path is not None and word_timings is not None:
            write_json(
                scene_map_path,
                {"scenes": [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in word_timings]},
            )

    def _read_remapped_words() -> list[dict[str, Any]] | None:
        if scene_map_path is None or word_timings is None:
            return None
        remapped = read_json(scene_map_path)
        if not isinstance(remapped, dict) or not isinstance(remapped.get("scenes"), list):
            return None
        return [
            {"text": w.get("text", ""), "start": w.get("start"), "end": w.get("end")}
            for w in remapped["scenes"]
            if isinstance(w, dict) and w.get("start") is not None and w.get("end") is not None
        ]

    thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[aggressiveness]
    _write_word_scene_map()
    result = remove_silences_core(  # type: ignore[no-untyped-call]  # the vendored engine is untyped by design
        str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", str(scene_map_path or "")
    )
    remapped_words = _read_remapped_words()
    removed_pct = _removed_pct(result)
    if removed_pct is None or removed_pct <= max_removable_pct:
        return remapped_words

    fallback_level = _AGGRESSIVENESS_STEP_DOWN[aggressiveness]
    if fallback_level != aggressiveness:
        thresh, min_sil, keep = _AGGRESSIVENESS_PARAMS[fallback_level]
        _write_word_scene_map()
        result = remove_silences_core(  # type: ignore[no-untyped-call]  # the vendored engine is untyped by design
            str(raw_path), str(clean_path), thresh, min_sil, keep, "", "", str(scene_map_path or "")
        )
        remapped_words = _read_remapped_words()
        removed_pct = _removed_pct(result)
        aggressiveness = fallback_level

    if removed_pct is not None and removed_pct > max_removable_pct:
        report_degradation(
            ctx,
            f"Segment {segment_index}: dead-air removal cut {removed_pct:.0f}% of the "
            f"audio, above the {max_removable_pct:.0f}% cap even at \"{aggressiveness}\" "
            "- kept the result anyway rather than discard synthesized audio.",
        )

    return remapped_words


def _write_word_timings(
    audio_dir: Path, per_segment_words: list[list[dict[str, Any]]], cleaned_paths: list[Path], gap_ms: int
) -> None:
    """Offset each segment's own (post-clean, still segment-local) word
    spans by the cumulative duration of every prior cleaned segment plus
    every prior gap, producing one flat list spanning `optimized_voice.mp3`
    - the same concatenation order/arithmetic `_assemble_with_gaps` itself
    performs, read via `audio_duration_seconds` rather than re-derived by a
    second formula. Writes `audio/word_timings.json`."""
    words: list[dict[str, Any]] = []
    offset = 0.0
    for i, (segment_words, clean_path) in enumerate(zip(per_segment_words, cleaned_paths)):
        if i > 0 and gap_ms > 0:
            offset += gap_ms / 1000.0
        for w in segment_words:
            start, end = w.get("start"), w.get("end")
            if start is None or end is None:
                continue
            words.append(
                {
                    "text": w.get("text", ""),
                    "start": round(offset + float(start), 3),
                    "end": round(offset + float(end), 3),
                }
            )
        offset += audio_duration_seconds(clean_path)
    write_json(audio_dir / "word_timings.json", {"words": words})
```

Run Step 3.2's tests — pass. **Mechanically verified**: full `tests/
pipeline/test_phases_media.py` run, 53/54 (49 pre-existing + 3 new + 1
already-counted from sub-slice 2's own tests in this same file — see that
sub-slice's plan), 1 pre-existing failure unrelated (`test_visuals_
writes_safezones_from_the_real_images`, confirmed on the unmodified
checkout).

**Commit:** `feat(voice): capture, remap, and offset-merge TTS-native word timing into word_timings.json`

---

## Task 4 — `captions.py`: 3-way timing priority

### Step 4.1 — Test

**Verified against this file's actual current tests before writing
these** — an earlier draft of this task assumed an existing "Provider
keys" style test to extend; the real fixtures are `_patch_audio_duration`,
`_write_audio`, `captions_module.has_key`/`get_key`/
`_transcribe_with_assemblyai` monkeypatches, all already established in
`tests/pipeline/test_phases_polish.py`. Add:

```python
def test_captions_prefers_native_tts_timing_over_assemblyai(tmp_path, monkeypatch):
    """The new default priority: native timing wins even when an
    AssemblyAI key IS configured - AssemblyAI must not even be called."""
    called = {"assemblyai": False}

    def _fail_if_called(*a, **k):
        called["assemblyai"] = True
        raise AssertionError("AssemblyAI should not have been called")

    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "fake-key")
    monkeypatch.setattr(captions_module, "_transcribe_with_assemblyai", _fail_if_called)
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_audio(ctx)
    write_json(
        Path(ctx.project.folder) / "audio" / "word_timings.json",
        {"words": [{"text": "Hello", "start": 0.0, "end": 0.5}, {"text": "world", "start": 0.6, "end": 1.0}]},
    )
    _write_scenes(ctx, ["Hello world"])

    ScreenTextPhase().run(ctx)

    assert called["assemblyai"] is False
    assert any("native TTS timing" in e.message for e in events if isinstance(e, ProgressEvent))
    source_doc = read_json(render_dir(ctx) / "_transcript_source.json")
    assert source_doc == {"provider": "tts_native"}


def test_captions_timing_source_forced_to_assemblyai_skips_native_even_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "fake-key")
    monkeypatch.setattr(
        captions_module,
        "_transcribe_with_assemblyai",
        lambda audio_bytes, key: [{"text": "Hi", "start": 0.0, "end": 0.5}],
    )
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, events = _ctx(project, channel=_channel({"captions_timing_source": "assemblyai"}))
    _write_audio(ctx)
    write_json(
        Path(ctx.project.folder) / "audio" / "word_timings.json",
        {"words": [{"text": "Hi", "start": 0.0, "end": 0.5}]},
    )
    _write_scenes(ctx, ["Hi there"])

    ScreenTextPhase().run(ctx)

    assert any("AssemblyAI" in e.message for e in events if isinstance(e, ProgressEvent))
    source_doc = read_json(render_dir(ctx) / "_transcript_source.json")
    assert source_doc == {"provider": "assemblyai"}


def test_captions_timing_source_forced_to_estimated_skips_both(tmp_path, monkeypatch):
    called = {"assemblyai": False}
    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "fake-key")
    monkeypatch.setattr(
        captions_module,
        "_transcribe_with_assemblyai",
        lambda *a, **k: called.__setitem__("assemblyai", True),
    )
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, events = _ctx(project, channel=_channel({"captions_timing_source": "estimated"}))
    _write_audio(ctx)
    write_json(
        Path(ctx.project.folder) / "audio" / "word_timings.json",
        {"words": [{"text": "Hi", "start": 0.0, "end": 0.5}]},
    )
    _write_scenes(ctx, ["A single simple scene of narration text."])

    ScreenTextPhase().run(ctx)

    assert called["assemblyai"] is False
    assert any("estimated timing" in e.message for e in events if isinstance(e, ProgressEvent))
    source_doc = read_json(render_dir(ctx) / "_transcript_source.json")
    assert source_doc == {"provider": "estimated"}


def test_captions_falls_through_to_assemblyai_when_no_native_timing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(captions_module, "has_key", lambda provider: True)
    monkeypatch.setattr(captions_module, "get_key", lambda provider: "fake-key")
    monkeypatch.setattr(
        captions_module,
        "_transcribe_with_assemblyai",
        lambda audio_bytes, key: [{"text": "Hi", "start": 0.0, "end": 0.5}],
    )
    _patch_audio_duration(monkeypatch)

    project = _project(tmp_path)
    ctx, events = _ctx(project)
    _write_audio(ctx)
    # No word_timings.json written at all - voice.py's provider didn't
    # support native timing for this run.
    _write_scenes(ctx, ["Hi there"])

    ScreenTextPhase().run(ctx)

    assert any("AssemblyAI" in e.message for e in events if isinstance(e, ProgressEvent))
```

Run these — fail, `_get_words` doesn't check `word_timings.json` yet, and
`captions_timing_source` isn't read yet.

### Step 4.2 — Implementation

**Verified against the real file before writing this block — the actual
current `_get_words` has a different shape than an earlier draft of this
plan assumed** (a single `if has_key(...): ... else-fallthrough` block
ending in one unconditional return, not three separate early returns).
Replace it exactly as it really is:

```python
def _get_words(
    ctx: PhaseContext, audio_path: Path, scene_texts_clean: list[str], scene_windows: dict[int, tuple[float, float]]
) -> tuple[list[dict], bool]:
    """`(words, used_assemblyai)`. Tries AssemblyAI only when a key is
    configured; ANY failure (network, timeout, bad key, empty result)
    degrades to the synthetic estimate rather than raising - see module
    docstring's "no key OR a failing key" invariant."""
    if has_key("assemblyai"):
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
                    return words, True
    return _synthetic_word_timings(scene_texts_clean, scene_windows), False
```

with:

```python
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
```

Replace `run()`'s call site:

```python
        words, used_assemblyai = _get_words(ctx, audio_path, scene_texts_clean, scene_windows)
        if not words:
            raise AvgError(ErrorKind.bug, "captions phase: produced no usable words to caption")
```

with:

```python
        words, timing_source = _get_words(ctx, audio_path, scene_texts_clean, scene_windows)
        if not words:
            raise AvgError(ErrorKind.bug, "captions phase: produced no usable words to caption")
```

Replace the cache-seeding and final message block:

```python
        # Hand the same words to the render's burn pass through the vendored
        # cache, so it neither re-uploads the audio nor re-pays for it, and
        # burns exactly the words these cards were timed against. The
        # provider name is part of the cache key, so it is recorded for
        # Render to pass back rather than guessed there.
        provider = "assemblyai" if used_assemblyai else "estimated"
        seed_transcript_cache(Path(ctx.project.folder), audio_path, provider, words)
        write_json(rdir / "_transcript_source.json", {"provider": provider})

        source = "AssemblyAI" if used_assemblyai else "estimated timing"
        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id,
                percent=100,
                message=f"Captions complete ({len(cues) - suppressed}/{len(cues)} cue(s), {source}).",
            )
        )
```

with:

```python
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
```

(`"AssemblyAI"` and `"estimated timing"` are kept as the exact same
message substrings the existing tests already assert on — `source_label`
only adds the third case, it does not rename the two that already exist.)

Run Step 4.1's tests — pass. **Mechanically verified**: full `tests/
pipeline/test_phases_polish.py` run, 41/42 (37 pre-existing + 4 new), 1
pre-existing failure unrelated (`test_resolve_font_family_extracts_
family_name_from_a_font_file_path`, confirmed on the unmodified
checkout).

**Commit:** `feat(captions): 3-way timing priority - tts_native over assemblyai over estimated`

---

## Manual verification (after all 4 tasks)

1. `.venv-linux/bin/python -m pytest tests -q` (or the Windows
   equivalent) — zero regressions vs. the ledger's documented baseline.
2. `mypy` — clean on every file this plan touched.
3. A real (or `AVG_HTTP_RECORD=1`) run on Edge TTS (the free, always-
   available default): confirm `audio/word_timings.json` is written and
   its word count roughly matches the narration's own word count.
4. The same, on ElevenLabs with a real key: confirm the `/with-timestamps`
   endpoint was actually hit (visible in a recorded fixture's URL) and the
   resulting captions read naturally against the real audio, not just a
   synthetic fixture.
5. Compare a captioned video's sync, by eye, against the same project run
   with `captions_timing_source` forced to `"estimated"` — the difference
   this slice exists to produce.

## Out of scope (matches the spec)

- Removing AssemblyAI support.
- Any change to cue grouping (`_group_cues`) or card suppression.
- HuggingFace or any future voice provider without a timing-capable
  endpoint - falls back to plain `synthesize()` automatically via the
  `hasattr` check.
- Sub-slices 1, 2, 4 — separate plans (1 and 2 already written).
