"""ElevenLabs voice provider.

This file exists to get one thing exactly right: the primary/backup *key*
fallback, which the legacy PowerShell app got wrong twice across two
releases before B2.19.2 settled it (`Invoke-TtsSingleCall`,
`D:\\AI_Video_Gen\\modules\\pipeline.ps1`, lines 1498-1573; the error-body
parser it leans on, `Get-ElevenLabsErrorMessage`, is at line 1414). The
corrected rule, restated precisely because restating it precisely is the
entire point of this module:

  - `auth` or `quota` -> retry ONCE with the configured backup key. Both
    kinds say something about *this key specifically* - a bad/expired key,
    or this account's quota being exhausted - so a different account's key
    may simply work where this one didn't.
  - Anything else - most importantly a `5xx`, which `avg.providers.errors.
    classify` maps to `ErrorKind.network` (see that module's hard rule:
    "500 <= status < 600 -> network", checked before any body text) - must
    NOT switch keys. A server error says nothing about which key sent the
    request; switching would only spend the backup key's quota on a
    problem the backup key cannot fix. This is the exact mistake
    `Invoke-TtsSingleCall`'s own B2.19.2 comment documents fixing (a 5xx is
    SERVICE-wide, not ACCOUNT-scoped) - see `_KEY_SWITCH_KINDS` below.
  - If the backup key's own attempt also fails, its `AvgError` propagates
    unchanged - there is no third key. Falling further back to Edge TTS is
    a caller/router decision (Slice 4 assembles the pipeline - see
    `docs/superpowers/plans/2026-07-25-slice-3-provider-layer.md`'s "Out of
    scope for Slice 3"), not this provider's job: this class's whole
    responsibility stops at "did ElevenLabs, with up to two keys, produce
    audio."

Deliberately NOT derived from `POLICY[kind].on_exhausted`: that table's
`quota` row says `"switch_provider"` (correct at the *router* level - a
future voice router deciding whether to try an entirely different TTS
provider next), not `"switch_key"`. Blurring the two would either make a
generic quota failure switch providers here (not this file's job) or make
every provider's quota failure quietly retry a second key it doesn't have
(most providers only have one key). ElevenLabs' two-key behaviour is a
narrower, ElevenLabs-specific rule layered on top of the shared taxonomy,
not a re-reading of it - so `_KEY_SWITCH_KINDS` is stated here, literally,
as the brief states it.

The two keys are both read through `avg.config.secrets.get_key` under two
provider-ish names - `"elevenlabs"` (primary) and `"elevenlabs_backup"`
(backup) - mirroring the legacy app's `config.api_keys.fallbacks.elevenlabs`
slot but fitting `secrets.py`'s existing one-key-per-name shape (a plain
`{name: ciphertext}` store, see that module's docstring) rather than
inventing a second, nested schema for exactly one provider's second key.
"""

from __future__ import annotations

from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.transport import Transport
from avg.providers.voice.base import raise_for_status

_BASE_URL = "https://api.elevenlabs.io/v1"

# Matches the legacy app's own -TimeoutSec values for the equivalent calls
# (Invoke-ElevenLabsTtsRequest / the voices list call in settings_menu.ps1).
_SYNTHESIZE_TIMEOUT_SECONDS = 60.0
_LIST_VOICES_TIMEOUT_SECONDS = 20.0

# The only two ErrorKinds a second ElevenLabs key can plausibly fix - see
# this module's docstring for why this is stated literally rather than
# derived from POLICY[kind].on_exhausted.
_KEY_SWITCH_KINDS = (ErrorKind.auth, ErrorKind.quota)

# The secrets-store "provider name" the backup key is filed under. Not a
# real provider - just this file's chosen slot for ElevenLabs' second key
# (see module docstring).
_BACKUP_KEY_NAME = "elevenlabs_backup"


class ElevenLabsVoiceProvider:
    name = "elevenlabs"

    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()

    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text`, or raise `AvgError`.

        Tries the primary key first. On `auth`/`quota` failure, retries
        exactly once against the configured backup key (if any is
        configured) - never more than once, and never for any other
        failure kind (see module docstring, especially the 5xx case).
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
