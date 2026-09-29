"""What image models and voices a provider says it has.

**Listed, never probed.** Text models get a real probe
(`avg.providers.probe`): one tiny generation per model, which is cheap on
the free tiers and gives a true working/not-working verdict. Images and
voices cannot work that way - probing an image model means paying for an
image, and probing an ElevenLabs voice costs characters - so the Live List
curates what a provider ADVERTISES instead of what it proves. That was an
explicit decision, not an omission; see the Slice 8 design doc.

A provider that advertises nothing gets an empty list, never an invented
one. `avg.providers.models`'s standing rule applies here too: the only
fallback is a previous fetch, never a literal baked into source. OpenAI
and Gemini publish no image-model catalogue, so their entries are ones the
user adds by hand, marked `manual` so they can never be mistaken for
something the app verified.

Every failure returns empty. This runs as a sweep over several providers
and one unreachable endpoint must not cost the rest.
"""

from __future__ import annotations

from typing import Any

import httpx

from avg.config.secrets import get_key
from avg.providers.transport import Transport

#: Providers whose image-model list can actually be fetched.
_IMAGE_CATALOGUE_URLS: dict[str, str] = {
    # Returns a bare JSON array of short model names ("flux", "kontext", ...).
    "pollinations": "https://image.pollinations.ai/models",
    # Returns a list of objects; `name` is the model id.
    "stablehorde": "https://stablehorde.net/api/v2/status/models?type=image",
}

#: Same ceiling the probe uses: a catalogue that will not answer promptly
#: is a catalogue that cannot be shown, and the sweep has to end.
CATALOGUE_TIMEOUT_S = 20.0


def _catalogue_transport() -> Transport:
    return Transport(
        mode="live",
        client=httpx.Client(follow_redirects=False, timeout=CATALOGUE_TIMEOUT_S),
        sleep=lambda _s: None,
    )


def image_providers() -> list[str]:
    """Providers this module can list image models for."""
    return sorted(_IMAGE_CATALOGUE_URLS)


def voice_providers() -> list[str]:
    """Providers this module can list voices for.

    Edge needs no key (it is the free provider); ElevenLabs' voice list is
    a plain GET, but an authenticated one, so it is skipped without a key
    rather than reported as broken.
    """
    return ["edge", "elevenlabs"]


def _names_from(payload: Any) -> list[str]:
    """Model ids out of either catalogue shape.

    Pollinations answers with a list of strings, Stable Horde with a list
    of objects carrying `name`. Anything else yields nothing rather than a
    guess at where the ids might be.
    """
    if not isinstance(payload, list):
        return []
    names: list[str] = []
    for item in payload:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return sorted(set(names))


def list_image_models(provider: str, *, transport: Transport | None = None) -> list[str]:
    """`provider`'s advertised image models, or `[]`.

    Empty for a provider with no catalogue endpoint (OpenAI, Gemini), for
    an unreachable one, and for a response shaped in a way this module
    does not recognise. All three mean the same thing to the caller: there
    is nothing to offer that was not typed by hand.
    """
    url = _IMAGE_CATALOGUE_URLS.get(provider)
    if url is None:
        return []

    owned = transport is None
    transport = transport if transport is not None else _catalogue_transport()
    try:
        response = transport.request("GET", url, provider=provider)
        if response.status_code >= 400:
            return []
        return _names_from(response.json())
    except Exception:
        return []
    finally:
        if owned:
            transport.close()


def list_voices(provider: str, *, transport: Transport | None = None) -> list[dict[str, str]]:
    """`provider`'s advertised voices: `{voice_id, name, locale, gender}`.

    Delegates to the voice backends' own `list_voices`, which is where the
    per-provider request shape already lives - this module adds no second
    lister, exactly as `avg.providers.probe` reuses the text backends'.

    No synthesis happens: both providers' voice lists are plain GETs, so
    this costs nothing on either.
    """
    if provider == "elevenlabs" and not get_key("elevenlabs"):
        # Skipped, not reported broken: an unkeyed provider has nothing to
        # say, which is different from a provider that failed to answer.
        return []

    # Imported here rather than at module scope: `voice.providers` pulls in
    # edge_tts, which costs about two seconds of startup, and Slice 7 moved
    # that import off the launch path deliberately.
    from avg.providers.voice.providers import EdgeVoiceProvider, ElevenLabsVoiceProvider  # noqa: PLC0415

    backends = {"edge": EdgeVoiceProvider, "elevenlabs": ElevenLabsVoiceProvider}
    backend_cls = backends.get(provider)
    if backend_cls is None:
        return []

    try:
        backend = backend_cls(transport=transport) if transport is not None else backend_cls()
        voices = backend.list_voices()
    except Exception:
        return []

    out: list[dict[str, str]] = []
    for voice in voices:
        if not isinstance(voice, dict):
            continue
        voice_id = str(voice.get("voice_id", "")).strip()
        if not voice_id:
            continue
        out.append(
            {
                "voice_id": voice_id,
                "name": str(voice.get("name", voice_id)),
                "locale": str(voice.get("locale", "")),
                "gender": str(voice.get("gender", "")),
            }
        )
    return out
