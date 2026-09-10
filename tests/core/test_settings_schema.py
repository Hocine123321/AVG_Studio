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

import re
from pathlib import Path

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


_ALLOWED_RAW_READERS = {
    # The resolver's OWN construction of its first argument - reading
    # `channel.settings` here is what the resolver resolves, not a reader
    # bypassing it. `port_to_global` (line ~1244) is the one legitimate
    # exception within this same file: porting snapshots what the channel
    # *itself* owns, not the resolved/inherited view, so it is correct for
    # that one read to bypass the resolver on purpose. The allowlist is
    # file-level rather than line-level because every `.settings` read left
    # in this file after Task 3 is one of those two legitimate cases.
    "avg/api/routes_settings.py",
}


def test_no_reader_bypasses_the_resolver() -> None:
    """Slice 19 Task 6. §2.3's whole risk: a second, uncoordinated copy of
    the fallback chain would not announce itself. This is what announces
    it - every `.settings.get(` or `.settings[` under avg/pipeline/ or
    avg/api/, outside the one file allowed to build the resolver's own
    input, is a reader that skipped resolve_channel_settings."""
    pattern = re.compile(r"\.settings\.get\(|\.settings\[")
    offenders = []
    for path in (*Path("avg/pipeline").rglob("*.py"), *Path("avg/api").rglob("*.py")):
        rel = path.as_posix()
        if rel in _ALLOWED_RAW_READERS:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == [], f"reads channel.settings directly, bypassing resolve_channel_settings: {offenders}"


def test_validate_value_rejects_an_unlisted_choice_with_no_custom_escape() -> None:
    from avg.core.settings_schema import SettingSpec, SettingValidationError, validate_value

    spec = SettingSpec(
        id="fake_choice",
        group="creative",
        label="Fake",
        help="",
        type="choice",
        default="a",
        scope="channel",
        invalidates="brief",
        choices=["a", "b"],
    )
    try:
        validate_value(spec, "not-a-real-choice")
        assert False, "expected SettingValidationError"
    except SettingValidationError:
        pass


def test_validate_value_accepts_any_string_when_choices_declare_a_custom_escape() -> None:
    """Reproduces a real, pre-existing bug found while implementing Slice
    29 Task 5: visual_tone already ships a "custom" entry in its choices
    with no way to act on it - selecting it and typing anything would
    fail this exact check today, silently, since nothing in the app ever
    exercised that path before Slice 29 gave ChoiceField a UI for it."""
    from avg.core.settings_schema import SettingSpec, validate_value

    spec = SettingSpec(
        id="fake_choice_with_custom",
        group="creative",
        label="Fake",
        help="",
        type="choice",
        default="a",
        scope="channel",
        invalidates="brief",
        choices=["a", "b", "custom"],
    )
    assert validate_value(spec, "Bebas Neue") == "Bebas Neue"
    assert validate_value(spec, "a") == "a"


def test_filters_settings_exist_with_expected_defaults() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["filters_intensity"].default == 50
    assert by_id["filters_intensity"].type == "int"
    assert by_id["filters_intensity"].group == "filters"
    assert by_id["filters_allow"].default == ""
    assert by_id["filters_block"].default == ""
    for setting_id in ("filters_intensity", "filters_allow", "filters_block"):
        assert by_id[setting_id].scope == "channel"
        assert by_id[setting_id].invalidates == "director2"


def test_recycle_settings_exist_with_expected_defaults() -> None:
    by_id = {s.id: s for s in SETTINGS}
    assert by_id["recycle_enabled"].default is True
    assert by_id["recycle_enabled"].type == "bool"
    assert by_id["recycle_ratio"].default == 70
    assert by_id["recycle_ratio"].type == "int"
    for setting_id in ("recycle_enabled", "recycle_ratio"):
        assert by_id[setting_id].group == "images"
        assert by_id[setting_id].scope == "channel"
        assert by_id[setting_id].invalidates == "script"
