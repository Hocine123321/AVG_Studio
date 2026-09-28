"""The two concrete voice providers: ElevenLabs (premium, keyed) and Edge
TTS (free, keyless).

They sit together because they are the entire set - `avg.pipeline.phases.
voice` picks between exactly these two - and because the fallback rule that
connects them only makes sense read as one story: ElevenLabs tries its
primary key, then its backup key, and a caller that still has nothing falls
through to Edge TTS. What each one may and may not do about a failure is
stated in its own section docstring below; neither rule is derived from the
other.

`avg.providers.voice.base` (the `VoiceProvider` protocol and
`raise_for_status`) stays separate - it is the contract, not an
implementation of it.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from avg.config import provider_health
from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.transport import Transport
from avg.providers.voice.base import raise_for_status

# ── ElevenLabs Provider ─────────────────────────────────────
#
# This section exists to get one thing exactly right: the primary/backup
# *key* fallback, which the legacy PowerShell app got wrong twice across two
# releases before B2.19.2 settled it (`Invoke-TtsSingleCall`,
# `D:\AI_Video_Gen\modules\pipeline.ps1`, lines 1498-1573; the error-body
# parser it leans on, `Get-ElevenLabsErrorMessage`, is at line 1414). The
# corrected rule, restated precisely because restating it precisely is the
# entire point:
#
#   - `auth` or `quota` -> retry ONCE with the configured backup key. Both
#     kinds say something about *this key specifically* - a bad/expired key,
#     or this account's quota being exhausted - so a different account's key
#     may simply work where this one didn't.
#   - Anything else - most importantly a `5xx`, which `avg.providers.errors.
#     classify` maps to `ErrorKind.network` (see that module's hard rule:
#     "500 <= status < 600 -> network", checked before any body text) - must
#     NOT switch keys. A server error says nothing about which key sent the
#     request; switching would only spend the backup key's quota on a
#     problem the backup key cannot fix. This is the exact mistake
#     `Invoke-TtsSingleCall`'s own B2.19.2 comment documents fixing (a 5xx is
#     SERVICE-wide, not ACCOUNT-scoped) - see `_KEY_SWITCH_KINDS` below.
#   - If the backup key's own attempt also fails, its `AvgError` propagates
#     unchanged - there is no third key. Falling further back to Edge TTS is
#     a caller/router decision (Slice 4 assembles the pipeline - see
#     `docs/superpowers/plans/2026-07-25-slice-3-provider-layer.md`'s "Out of
#     scope for Slice 3"), not this provider's job: this class's whole
#     responsibility stops at "did ElevenLabs, with up to two keys, produce
#     audio."
#
# Deliberately NOT derived from `POLICY[kind].on_exhausted`: that table's
# `quota` row says `"switch_provider"` (correct at the *router* level - a
# future voice router deciding whether to try an entirely different TTS
# provider next), not `"switch_key"`. Blurring the two would either make a
# generic quota failure switch providers here (not this class's job) or make
# every provider's quota failure quietly retry a second key it doesn't have
# (most providers only have one key). ElevenLabs' two-key behaviour is a
# narrower, ElevenLabs-specific rule layered on top of the shared taxonomy,
# not a re-reading of it - so `_KEY_SWITCH_KINDS` is stated here, literally,
# as the brief states it.
#
# The two keys are both read through `avg.config.secrets.get_key` under two
# provider-ish names - `"elevenlabs"` (primary) and `"elevenlabs_backup"`
# (backup) - mirroring the legacy app's `config.api_keys.fallbacks.elevenlabs`
# slot but fitting `secrets.py`'s existing one-key-per-name shape (a plain
# `{name: ciphertext}` store, see that module's docstring) rather than
# inventing a second, nested schema for exactly one provider's second key.

_BASE_URL = "https://api.elevenlabs.io/v1"

# Matches the legacy app's own -TimeoutSec values for the equivalent calls
# (Invoke-ElevenLabsTtsRequest / the voices list call in settings_menu.ps1).
_SYNTHESIZE_TIMEOUT_SECONDS = 60.0
_LIST_VOICES_TIMEOUT_SECONDS = 20.0

# The only two ErrorKinds a second ElevenLabs key can plausibly fix - see
# the section comment above for why this is stated literally rather than
# derived from POLICY[kind].on_exhausted.
_KEY_SWITCH_KINDS = (ErrorKind.auth, ErrorKind.quota)

# The secrets-store "provider name" the backup key is filed under. Not a
# real provider - just this file's chosen slot for ElevenLabs' second key
# (see the section comment above).
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
    name = "elevenlabs"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

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
                "Accept": "audio/mpeg",
            },
            json={"text": text, "model_id": model},
            timeout=_SYNTHESIZE_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        if not with_timestamps:
            content = response.content
            if not content:
                raise AvgError(ErrorKind.bug, "ElevenLabs returned no audio data")
            return content, None

        # `/with-timestamps` returns JSON (base64 audio + alignment), not
        # raw audio bytes - a different response shape, not just a
        # different URL.
        try:
            data = response.json()
        except Exception as exc:
            raise AvgError(ErrorKind.bug, "ElevenLabs returned an unparseable with-timestamps response") from exc
        audio_b64 = data.get("audio_base64")
        if not audio_b64:
            raise AvgError(ErrorKind.bug, "ElevenLabs returned no audio data")
        content = base64.b64decode(audio_b64)
        words = _words_from_alignment(data.get("alignment"))
        return content, words

    def list_voices(self) -> list[dict[str, str]]:
        """The account's available voices. Uses the primary key only - a
        voice catalogue is the same regardless of which key lists it, so
        there is nothing for a backup key to fix here."""
        key = get_key(self.name)
        if not key:
            raise AvgError(ErrorKind.auth, "ElevenLabs API key is not set")

        response = self._transport.request(
            "GET",
            f"{_BASE_URL}/voices",
            headers={"xi-api-key": key},
            timeout=_LIST_VOICES_TIMEOUT_SECONDS,
            provider=self.name,
        )
        raise_for_status(response, self.name)

        payload = response.json()
        voices = payload.get("voices") or []
        return [
            {"voice_id": str(v.get("voice_id", "")), "name": str(v.get("name", ""))}
            for v in voices
            if isinstance(v, dict)
        ]


# ── Edge TTS Provider ───────────────────────────────────────
#
# Free, keyless, the last resort.
#
# Unlike every other provider in this codebase, this one does not go through
# `avg.providers.transport.Transport`. `edge-tts` is a Python package that
# speaks to a Microsoft endpoint over a websocket (via `aiohttp`) - there is
# no plain HTTP request/response for `Transport` to record or replay, so this
# class wraps the `edge_tts` package directly instead.
#
# That means two things this codebase treats as invariants elsewhere have to
# be re-established here, by hand, rather than inherited for free:
#
#   1. **Every failure still becomes an `AvgError` with a real `ErrorKind`.**
#      `edge_tts` raises its own exception types (`NoAudioReceived`,
#      `WebSocketError`, `UnexpectedResponse`, ...) - this class never lets
#      one of those escape. Each is passed to `avg.providers.errors.classify`
#      (with `status=None`, since there was never an HTTP response) exactly
#      the way `Transport._send_with_retry` classifies a network exception
#      that never got a response - see that function's `except
#      httpx.RequestError` branch. `classify` is still the only place that
#      ever reasons about what a failure *means*; this class just supplies
#      the exception.
#   2. **No test may reach the network.** `edge_tts.Communicate` and
#      `edge_tts.list_voices` are referenced through the `edge_tts` module
#      object (`edge_tts.Communicate(...)`, not `from edge_tts import
#      Communicate`) precisely so `tests/providers/test_voice.py` can
#      monkeypatch them at the module level and this provider never notices
#      the difference.
#
# This provider has no API key and no key-fallback concept - see the
# ElevenLabs section above for why that logic lives there and nowhere else.
# `model` is accepted only to satisfy `VoiceProvider`'s shared `synthesize`
# signature; edge-tts has no model concept (voice selection is the whole
# story), so it is accepted and ignored.



# ── edge-tts, imported on first use ─────────────────────────
#
# NOT a module-scope `import edge_tts`. That import costs ~2.0s of a ~4.3s
# app startup (it drags in aiohttp), and this module is reached from
# `avg.api.app` transitively - phase registry -> voice phase -> here - so
# every launch of the app paid for a TTS library that nothing touches until
# the voice phase actually runs, minutes into a pipeline if at all.
#
# The module `__getattr__` below is what keeps that lazy WITHOUT breaking
# the existing seam: `tests/providers/test_voice.py` monkeypatches
# `voice_module.edge_tts.Communicate` / `.list_voices`, and attribute access
# on this module still resolves to the real package - it just imports it at
# that moment rather than at startup. Callers inside this module go through
# `_edge()`, since a module `__getattr__` is not consulted for a plain
# global-name lookup.


def _edge() -> Any:
    """The `edge_tts` package, imported and cached on first use."""
    module = globals().get("edge_tts")
    if module is None:
        import edge_tts as module  # noqa: PLC0415 - deliberately lazy, see above

        globals()["edge_tts"] = module
    return module


def __getattr__(name: str) -> Any:
    # PEP 562. Keeps `voice_module.edge_tts` working for the tests that
    # patch it, without importing the package to define this module.
    if name == "edge_tts":
        return _edge()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# `edge_tts` has no `httpx`/transport seam of its own to carry a per-call
# timeout, unlike every other provider in this package - so a stalled
# connection here has no way to be interrupted and would hang whichever
# thread called `synthesize`/`list_voices` (the render pipeline's own
# thread, since nothing wraps this provider in an executor) indefinitely,
# holding `app.state.render_thread`'s single slot forever. These bound both
# calls the same way ElevenLabs' own `httpx` calls are bounded above.
_EDGE_SYNTHESIZE_TIMEOUT_SECONDS = 60.0
_EDGE_LIST_VOICES_TIMEOUT_SECONDS = 20.0


class EdgeVoiceProvider:
    name = "edge"

    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text` spoken by `voice_id`.

        `model` is unused (see the section comment above) - accepted only so
        this provider satisfies `VoiceProvider.synthesize`'s shared
        signature.
        """
        del model

        cooldown = provider_health.active_cooldown(self.name)
        if cooldown is not None:
            raise provider_health.cooldown_error(cooldown)

        def _collect() -> bytes:
            communicate = _edge().Communicate(text, voice_id)
            audio = bytearray()
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    audio.extend(chunk["data"])
            return bytes(audio)

        # `stream_sync()` is a blocking generator, not a coroutine, so it
        # can't be bounded with `asyncio.wait_for` - run it on a worker
        # thread instead and bound `Future.result` itself. A timed-out
        # thread is not killed (Python has no API for that) but is no
        # longer waited on, so this call returns promptly either way.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            audio = pool.submit(_collect).result(timeout=_EDGE_SYNTHESIZE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            if kind in (ErrorKind.quota, ErrorKind.billing):
                if kind == ErrorKind.quota:
                    provider_health.record_cooldown(
                        self.name, "quota", cooldown_until=provider_health.default_quota_cooldown_until(self.name)
                    )
                else:
                    provider_health.record_cooldown(self.name, "billing")
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc
        finally:
            pool.shutdown(wait=False)

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

        cooldown = provider_health.active_cooldown(self.name)
        if cooldown is not None:
            raise provider_health.cooldown_error(cooldown)

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
            if kind in (ErrorKind.quota, ErrorKind.billing):
                if kind == ErrorKind.quota:
                    provider_health.record_cooldown(
                        self.name, "quota", cooldown_until=provider_health.default_quota_cooldown_until(self.name)
                    )
                else:
                    provider_health.record_cooldown(self.name, "billing")
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc
        finally:
            pool.shutdown(wait=False)

        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio, (words or None)

    def list_voices(self) -> list[dict[str, str]]:
        """Microsoft's live voice catalogue - always fetched, never a
        hardcoded list (matching every other provider's `list_voices`/
        `list_models` in this codebase)."""
        async def _list_with_timeout() -> Any:
            return await asyncio.wait_for(_edge().list_voices(), timeout=_EDGE_LIST_VOICES_TIMEOUT_SECONDS)

        try:
            voices = asyncio.run(_list_with_timeout())
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            if kind in (ErrorKind.quota, ErrorKind.billing):
                if kind == ErrorKind.quota:
                    provider_health.record_cooldown(
                        self.name, "quota", cooldown_until=provider_health.default_quota_cooldown_until(self.name)
                    )
                else:
                    provider_health.record_cooldown(self.name, "billing")
            raise AvgError(kind, f"Edge TTS voice list failed: {exc}", detail=str(exc)) from exc

        return [
            {
                "voice_id": str(v.get("ShortName", "")),
                "name": str(v.get("FriendlyName", v.get("Name", ""))),
                "locale": str(v.get("Locale", "")),
                "gender": str(v.get("Gender", "")),
            }
            for v in voices
        ]
