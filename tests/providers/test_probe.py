"""Tests for model probing.

No network: every call goes through an injected Transport backed by an
httpx MockTransport. A probe that reached a real provider would cost money
on the paid ones, which is the rule this whole suite exists under.
"""

from __future__ import annotations

import httpx
import pytest

from avg.providers import probe
from avg.providers.probe import probe_all, probe_provider
from avg.providers.transport import Transport


def _mock_transport(handler) -> Transport:
    return Transport(
        mode="live",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def _stub_keys(monkeypatch, key: str = "fixture-key") -> None:
    """Both key lookups, not just the probe's.

    The probe checks for a key itself, but the backend it builds reads the
    real DPAPI store again when it signs its request - so patching only
    `probe.get_key` makes the test pass on the developer's machine (which
    has real keys) and fail anywhere else.
    """
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: key)
    monkeypatch.setattr("avg.providers.text.community.get_key", lambda _p: key)


def test_a_model_that_answers_is_marked_working(monkeypatch):
    _stub_keys(monkeypatch)
    monkeypatch.setattr(
        "avg.providers.probe.list_models", lambda provider, transport: ["good", "bad"]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if b"bad" in request.content:
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    results = probe_provider("groq", transport=_mock_transport(handler))
    assert results == {"good": True, "bad": False}


def test_a_provider_with_no_key_probes_nothing(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "")
    # No key means no call at all - never a probe that fails for the wrong
    # reason and marks every model broken.
    assert probe_provider("gemini", transport=None) == {}


def test_an_unknown_provider_is_empty_not_an_error(monkeypatch):
    _stub_keys(monkeypatch)
    assert probe_provider("not-a-provider", transport=None) == {}


def test_a_failing_list_endpoint_does_not_raise(monkeypatch):
    """The sweep runs over every provider; one bad list must not kill it."""
    _stub_keys(monkeypatch)

    def boom(provider, transport):
        raise RuntimeError("list endpoint down")

    monkeypatch.setattr("avg.providers.probe.list_models", boom)
    assert probe_provider("groq", transport=None) == {}


def test_probe_all_keeps_an_entry_for_every_provider_asked(monkeypatch):
    _stub_keys(monkeypatch)
    monkeypatch.setattr("avg.providers.probe.get_key", lambda p: "k" if p == "groq" else "")
    monkeypatch.setattr("avg.providers.probe.list_models", lambda provider, transport: ["good"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    results = probe_all(["groq", "gemini"], transport=_mock_transport(handler))
    # "keyed but nothing works" and "no key" are different states and the
    # UI has to be able to tell them apart, so the empty entry is kept.
    assert results == {"groq": {"good": True}, "gemini": {}}


def test_probe_models_touches_only_the_models_it_is_given(monkeypatch):
    """A sweep that tests everything a provider advertises is what the Live
    List exists to replace. Only ticked models are probed, and `list_models`
    is never called at all - the caller already knows which ones."""
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "k")
    monkeypatch.setattr(
        "avg.providers.probe.list_models",
        lambda *a, **k: pytest.fail("probe_models must not list a catalogue"),
    )

    asked: list[tuple[str, str]] = []

    def fake_answers(provider, model, _transport):
        asked.append((provider, model))
        return model != "dead"

    monkeypatch.setattr("avg.providers.probe._model_answers", fake_answers)

    result = probe.probe_models({"groq": ["good", "dead"]})

    assert asked == [("groq", "good"), ("groq", "dead")]
    assert result == {"groq": {"good": True, "dead": False}}


def test_probe_models_never_probes_an_unkeyed_provider(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: None)
    monkeypatch.setattr(
        "avg.providers.probe._model_answers",
        lambda *a, **k: pytest.fail("no key means no call at all"),
    )
    assert probe.probe_models({"groq": ["good"]}) == {"groq": {}}


def test_reachability_is_a_catalogue_fetch_never_a_generation(monkeypatch):
    """An image probe is a paid image and an ElevenLabs probe is paid
    characters. Reachability is the provider's own free catalogue GET."""
    monkeypatch.setattr(
        "avg.providers.catalogues.list_image_models", lambda p, **k: ["flux"] if p == "pollinations" else []
    )
    monkeypatch.setattr(
        "avg.providers.probe._model_answers",
        lambda *a, **k: pytest.fail("reachability must never generate"),
    )

    assert probe.check_reachable("image", "pollinations") is True
    assert probe.check_reachable("image", "stablehorde") is False


def test_a_provider_with_no_catalogue_endpoint_is_untested_not_unreachable(monkeypatch):
    """OpenAI and Gemini publish no image catalogue. Reporting them
    `unreachable` would be a verdict nothing earned."""
    assert probe.check_reachable("image", "openai") is None
    assert probe.check_reachable("image", "gemini") is None


def test_an_unkeyed_elevenlabs_is_untested_not_unreachable(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: None)
    assert probe.check_reachable("voice", "elevenlabs") is None


def test_an_unknown_voice_provider_is_untested_not_unreachable(monkeypatch):
    """Mirrors the image branch's guard above. Without it, a voice
    provider `catalogues.voice_providers()` does not know about would fall
    through to `list_voices` and read as `False` - "unreachable", a
    verdict nothing earned - instead of the `None` an unproven provider
    should report."""
    monkeypatch.setattr(
        "avg.providers.catalogues.list_voices",
        lambda *a, **k: pytest.fail("an unknown provider has no catalogue to list"),
    )
    assert probe.check_reachable("voice", "not-a-real-provider") is None


def test_voice_reachability_lists_and_does_not_synthesise(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "k")
    monkeypatch.setattr(
        "avg.providers.catalogues.list_voices",
        lambda p, **k: [{"voice_id": "v", "name": "V", "locale": "en-GB", "gender": "Female"}],
    )
    assert probe.check_reachable("voice", "edge") is True
