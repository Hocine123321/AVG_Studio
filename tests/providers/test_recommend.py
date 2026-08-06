"""Model recommendations. Never a live call: every test here injects the
text response rather than generating one.
"""

from __future__ import annotations

import pytest

from avg.providers import recommend


def _reply(monkeypatch, text: str) -> None:
    """Replace the router's generate with a canned answer."""

    class _Result:
        def __init__(self, t: str) -> None:
            self.text = t
            self.provider = "groq"
            self.model = "test"
            self.used_backup = False

    monkeypatch.setattr(recommend, "_generate", lambda prompt, config: _Result(text))


def test_a_suggestion_carries_a_tier_and_a_reason(monkeypatch):
    _reply(
        monkeypatch,
        '[{"entry_id": "groq/llama-3.1-8b", "kind": "text", "tier": "cheap",'
        ' "reason": "fast and cheap for drafting"}]',
    )
    out = recommend.suggest_models({"text": ["groq/llama-3.1-8b"], "image": []}, config=None)

    assert len(out) == 1
    assert out[0].entry_id == "groq/llama-3.1-8b"
    assert out[0].tier == "cheap"
    assert "drafting" in out[0].reason


def test_an_entry_that_was_not_in_the_catalogue_is_dropped(monkeypatch):
    """The most important guard here. A model naming a plausible id it was
    never shown must not reach the Live List."""
    _reply(
        monkeypatch,
        '[{"entry_id": "openai/gpt-9-ultra", "kind": "text", "tier": "ultra", "reason": "invented"},'
        ' {"entry_id": "groq/real", "kind": "text", "tier": "cheap", "reason": "real"}]',
    )
    out = recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None)

    assert [s.entry_id for s in out] == ["groq/real"]


def test_a_reply_that_is_not_json_yields_nothing(monkeypatch):
    _reply(monkeypatch, "Sure! Here are my picks: use Llama, it's great.")
    assert recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None) == []


def test_voices_carry_no_tier(monkeypatch):
    """Voices have no cost axis, so a tier there would be a meaningless
    value dressed as information."""
    _reply(
        monkeypatch,
        '[{"entry_id": "edge/en-GB-RyanNeural", "kind": "voice", "tier": "ultra",'
        ' "reason": "warm narration voice"}]',
    )
    out = recommend.suggest_voices(["edge/en-GB-RyanNeural"], config=None)

    assert len(out) == 1
    assert out[0].tier is None
    assert out[0].kind == "voice"


def test_an_unknown_tier_becomes_none_rather_than_being_invented(monkeypatch):
    _reply(
        monkeypatch,
        '[{"entry_id": "groq/real", "kind": "text", "tier": "spectacular", "reason": "x"}]',
    )
    out = recommend.suggest_models({"text": ["groq/real"], "image": []}, config=None)
    assert out[0].tier is None
