"""Tests for `resolve_channel_settings` (Slice 19 §2.3) - the one resolver
every `scope="channel"` setting read must go through.

The whole point of the function is absence-only inheritance: a key's
presence in `channel_settings`, not its truthiness, decides whether the
channel owns it. An explicitly-stored empty string is a value, not a gap -
`resolve_channel_settings` must return it unchanged rather than falling
through to the defaults document or the catalogue default. That is the one
behaviour the legacy per-reader `.get(id) or literal_default` pattern could
never express, and the bug this function exists to not reintroduce.
"""

from __future__ import annotations

from avg.core.settings_schema import SETTINGS, resolve_channel_settings


def test_key_present_in_channel_wins_over_defaults_and_spec_default() -> None:
    result = resolve_channel_settings(
        {"length_preset": "30s"}, {"length_preset": "90s"}
    )
    assert result["length_preset"] == "30s"


def test_key_absent_from_channel_present_in_defaults_wins_over_spec_default() -> None:
    result = resolve_channel_settings({}, {"length_preset": "90s"})
    assert result["length_preset"] == "90s"


def test_key_absent_from_both_falls_to_spec_default() -> None:
    spec = next(s for s in SETTINGS if s.id == "length_preset")
    result = resolve_channel_settings({}, {})
    assert result["length_preset"] == spec.default


def test_empty_string_stored_against_an_owned_key_returns_as_empty_string() -> None:
    result = resolve_channel_settings(
        {"length_preset": ""}, {"length_preset": "90s"}
    )
    assert result["length_preset"] == ""


def test_a_key_in_defaults_that_is_not_a_channel_scope_spec_is_ignored() -> None:
    result = resolve_channel_settings({}, {"theme": "midnight"})
    assert "theme" not in result


def test_result_covers_every_channel_scope_id_and_nothing_else() -> None:
    result = resolve_channel_settings({}, {})
    expected_ids = {s.id for s in SETTINGS if s.scope == "channel"}
    assert set(result) == expected_ids
