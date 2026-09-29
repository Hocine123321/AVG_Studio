"""Tests for catalogue listing.

No network: every call goes through an injected Transport backed by an
httpx MockTransport. Nothing here generates anything either - listing is
the whole point, because probing an image model costs an image and probing
an ElevenLabs voice costs characters.
"""

from __future__ import annotations

import httpx

from avg.providers.catalogues import image_providers, list_image_models, list_voices, voice_providers
from avg.providers.transport import Transport


def _mock(handler) -> Transport:
    return Transport(
        mode="live",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_pollinations_models_come_from_its_own_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=["flux", "kontext"])

    assert list_image_models("pollinations", transport=_mock(handler)) == ["flux", "kontext"]
    assert "image.pollinations.ai" in seen[0]


def test_stable_hordes_object_shape_is_understood():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "SDXL 1.0", "count": 3}, {"name": "AlbedoBase XL"}])

    assert list_image_models("stablehorde", transport=_mock(handler)) == ["AlbedoBase XL", "SDXL 1.0"]


def test_a_provider_with_no_catalogue_returns_empty_not_a_guess():
    """OpenAI and Gemini advertise no image-model list. Inventing one is
    what `avg/providers/models.py`'s standing rule forbids: the only
    fallback is a previous fetch, never a literal in source."""
    assert list_image_models("openai", transport=None) == []
    assert list_image_models("gemini", transport=None) == []


def test_an_unreachable_catalogue_is_empty_not_an_exception():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    assert list_image_models("pollinations", transport=_mock(handler)) == []


def test_an_unrecognised_shape_yields_nothing():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": ["flux"]})

    assert list_image_models("pollinations", transport=_mock(handler)) == []


def test_elevenlabs_voices_are_skipped_without_a_key(monkeypatch):
    monkeypatch.setattr("avg.providers.catalogues.get_key", lambda _p: "")
    # Skipped, not reported broken: an unkeyed provider has nothing to say.
    assert list_voices("elevenlabs", transport=None) == []


def test_elevenlabs_voices_are_normalised(monkeypatch):
    monkeypatch.setattr("avg.providers.catalogues.get_key", lambda _p: "k")
    monkeypatch.setattr("avg.providers.voice.providers.get_key", lambda _p: "k")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"voices": [{"voice_id": "abc", "name": "Rachel", "labels": {"gender": "female"}}]},
        )

    voices = list_voices("elevenlabs", transport=_mock(handler))
    assert voices and voices[0]["voice_id"] == "abc"
    assert set(voices[0]) == {"voice_id", "name", "locale", "gender"}


def test_an_unknown_voice_provider_is_empty():
    assert list_voices("not-a-provider", transport=None) == []


def test_the_provider_lists_are_what_the_module_can_actually_do():
    assert image_providers() == ["pollinations", "stablehorde"]
    assert voice_providers() == ["edge", "elevenlabs"]
