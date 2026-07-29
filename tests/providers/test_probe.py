"""Tests for model probing.

No network: every call goes through an injected Transport backed by an
httpx MockTransport. A probe that reached a real provider would cost money
on the paid ones, which is the rule this whole suite exists under.
"""

from __future__ import annotations

import httpx

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
