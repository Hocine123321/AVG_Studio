"""Edge TTS voice provider - free, keyless, the last resort.

Unlike every other provider in this codebase, this one does not go through
`avg.providers.transport.Transport`. `edge-tts` is a Python package that
speaks to a Microsoft endpoint over a websocket (via `aiohttp`) - there is
no plain HTTP request/response for `Transport` to record or replay, so this
module wraps the `edge_tts` package directly instead.

That means two things this codebase treats as invariants elsewhere have to
be re-established here, by hand, rather than inherited for free:

  1. **Every failure still becomes an `AvgError` with a real `ErrorKind`.**
     `edge_tts` raises its own exception types (`NoAudioReceived`,
     `WebSocketError`, `UnexpectedResponse`, ...) - this module never lets
     one of those escape. Each is passed to `avg.providers.errors.classify`
     (with `status=None`, since there was never an HTTP response) exactly
     the way `Transport._send_with_retry` classifies a network exception
     that never got a response - see that function's `except
     httpx.RequestError` branch. `classify` is still the only place that
     ever reasons about what a failure *means*; this module just supplies
     the exception.
  2. **No test may reach the network.** `edge_tts.Communicate` and
     `edge_tts.list_voices` are referenced through the `edge_tts` module
     object (`edge_tts.Communicate(...)`, not `from edge_tts import
     Communicate`) precisely so `tests/providers/test_voice.py` can
     monkeypatch them at the module level and this provider never notices
     the difference.

This provider has no API key and no key-fallback concept - see
`avg/providers/voice/elevenlabs.py`'s module docstring for why that logic
lives there and nowhere else. `model` is accepted only to satisfy
`VoiceProvider`'s shared `synthesize` signature; edge-tts has no model
concept (voice selection is the whole story), so it is accepted and
ignored.
"""

from __future__ import annotations

import asyncio

import edge_tts

from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify


class EdgeVoiceProvider:
    name = "edge"

    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text` spoken by `voice_id`.

        `model` is unused (see module docstring) - accepted only so this
        provider satisfies `VoiceProvider.synthesize`'s shared signature.
        """
        del model
        try:
            communicate = edge_tts.Communicate(text, voice_id)
            audio = bytearray()
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    audio.extend(chunk["data"])
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc

        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return bytes(audio)

    def list_voices(self) -> list[dict[str, str]]:
        """Microsoft's live voice catalogue - always fetched, never a
        hardcoded list (matching every other provider's `list_voices`/
        `list_models` in this codebase)."""
        try:
            voices = asyncio.run(edge_tts.list_voices())
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
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
