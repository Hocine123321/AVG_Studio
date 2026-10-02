"""Tests for avg.config.provider_health."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from avg.config import provider_health as ph
from avg.core.errors import AvgError, ErrorKind


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "provider_health.json"


def test_no_entry_means_not_in_cooldown(path: Path):
    assert ph.active_cooldown("groq", path=path) is None
    assert ph.is_in_cooldown("groq", path=path) is False


def test_quota_cooldown_round_trips_while_active(path: Path):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    ph.record_cooldown("groq", "quota", cooldown_until=until, path=path)

    cooldown = ph.active_cooldown("groq", path=path)
    assert cooldown is not None
    assert cooldown.provider == "groq"
    assert cooldown.status == "quota"
    assert cooldown.cooldown_until == until
    assert ph.is_in_cooldown("groq", path=path) is True


def test_expired_quota_cooldown_is_not_active(path: Path):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ph.record_cooldown("groq", "quota", cooldown_until=past, path=path)

    assert ph.active_cooldown("groq", path=path) is None
    assert ph.is_in_cooldown("groq", path=path) is False


def test_billing_cooldown_has_no_timer_and_persists(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "same-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)

    cooldown = ph.active_cooldown("elevenlabs", path=path)
    assert cooldown is not None
    assert cooldown.status == "billing"
    assert cooldown.cooldown_until is None


def test_billing_cooldown_clears_when_the_key_changes(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "old-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)
    assert ph.is_in_cooldown("elevenlabs", path=path) is True

    monkeypatch.setattr(ph, "get_key", lambda provider: "new-key")
    assert ph.is_in_cooldown("elevenlabs", path=path) is False


def test_billing_cooldown_clears_when_the_key_is_removed(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "old-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)

    monkeypatch.setattr(ph, "get_key", lambda provider: None)
    assert ph.is_in_cooldown("elevenlabs", path=path) is False


def test_clear_cooldown_is_a_no_op_when_nothing_is_recorded(path: Path):
    ph.clear_cooldown("groq", path=path)  # must not raise
    assert ph.active_cooldown("groq", path=path) is None


def test_clear_cooldown_removes_an_active_entry(path: Path):
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    ph.clear_cooldown("groq", path=path)
    assert ph.active_cooldown("groq", path=path) is None


def test_cooldowns_for_different_providers_are_independent(path: Path):
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.active_cooldown("gemini", path=path) is None
    assert ph.active_cooldown("groq", path=path) is not None


def test_provider_matching_is_case_insensitive(path: Path):
    ph.record_cooldown("Groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.is_in_cooldown("groq", path=path) is True
    assert ph.is_in_cooldown("GROQ", path=path) is True


def test_a_corrupt_file_is_treated_as_empty_not_as_a_crash(path: Path):
    path.write_text("{not json", encoding="utf-8")
    assert ph.active_cooldown("groq", path=path) is None
    # And a subsequent write still succeeds, overwriting the corrupt file.
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.is_in_cooldown("groq", path=path) is True


def test_default_quota_cooldown_until_reads_the_setting(monkeypatch: pytest.MonkeyPatch):
    from avg.config import settings as settings_module

    doc = settings_module.SettingsDoc()
    doc.values["resilience_quota_cooldown_hours"] = 2.0
    monkeypatch.setattr(ph, "load_settings", lambda: doc)

    before = datetime.now(timezone.utc)
    until = ph.default_quota_cooldown_until("groq")
    assert timedelta(hours=1, minutes=55) < (until - before) < timedelta(hours=2, minutes=5)


def test_cooldown_error_carries_the_right_kind():
    cooldown = ph.ProviderCooldown(
        provider="groq", status="quota", since=datetime.now(timezone.utc), cooldown_until=None
    )
    err = ph.cooldown_error(cooldown)
    assert isinstance(err, AvgError)
    assert err.kind == ErrorKind.quota

    cooldown = ph.ProviderCooldown(
        provider="elevenlabs", status="billing", since=datetime.now(timezone.utc), cooldown_until=None
    )
    assert ph.cooldown_error(cooldown).kind == ErrorKind.billing


def test_cooldown_message_suffix_formats_quota_and_billing():
    assert ph.cooldown_message_suffix(None) == ""

    until = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    quota = ph.ProviderCooldown(provider="groq", status="quota", since=until, cooldown_until=until)
    assert ph.cooldown_message_suffix(quota) == " (groq: quota, back ~00:00 UTC)"

    billing = ph.ProviderCooldown(provider="elevenlabs", status="billing", since=until, cooldown_until=None)
    assert ph.cooldown_message_suffix(billing) == " (elevenlabs: billing issue)"


def test_concurrent_writes_do_not_lose_an_entry(path: Path):
    # Mirrors avg/pipeline/phases/visuals.py's own ThreadPoolExecutor shape
    # - several threads in one process, not separate processes - which is
    # exactly the concurrency this module's in-process lock has to survive.
    from concurrent.futures import ThreadPoolExecutor

    providers = [f"provider-{i}" for i in range(8)]

    def _record(name: str) -> None:
        ph.record_cooldown(name, "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_record, providers))

    for name in providers:
        assert ph.is_in_cooldown(name, path=path) is True


def test_fingerprint_headers_extracts_known_credential_headers():
    assert ph.fingerprint_headers({"xi-api-key": "abc"}) == ph.fingerprint_headers({"xi-api-key": "abc"})
    assert ph.fingerprint_headers({"xi-api-key": "abc"}) != ph.fingerprint_headers({"xi-api-key": "xyz"})
    assert ph.fingerprint_headers({"X-Goog-Api-Key": "abc"}) == ph.fingerprint_headers({"xi-api-key": "abc"})


def test_fingerprint_headers_strips_bearer_prefix_and_matches_the_raw_key():
    assert ph.fingerprint_headers({"Authorization": "Bearer secret123"}) == ph._fingerprint("secret123")


def test_fingerprint_headers_returns_none_for_unrecognised_or_missing_headers():
    assert ph.fingerprint_headers(None) is None
    assert ph.fingerprint_headers({}) is None
    assert ph.fingerprint_headers({"Content-Type": "application/json"}) is None


def test_key_fingerprint_mismatch_means_not_in_cooldown_for_either_status(path: Path):
    ph.record_cooldown(
        "elevenlabs", "quota",
        cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1),
        key_fingerprint="fingerprint-a", path=path,
    )
    assert ph.active_cooldown("elevenlabs", key_fingerprint="fingerprint-a", path=path) is not None
    assert ph.active_cooldown("elevenlabs", key_fingerprint="fingerprint-b", path=path) is None
    # No override at all still sees the cooldown (old callers, unaffected).
    assert ph.active_cooldown("elevenlabs", path=path) is not None


def test_key_fingerprint_omitted_preserves_original_behavior(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "same-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)
    assert ph.is_in_cooldown("elevenlabs", path=path) is True
    monkeypatch.setattr(ph, "get_key", lambda provider: "new-key")
    assert ph.is_in_cooldown("elevenlabs", path=path) is False


def test_two_credentials_of_one_provider_keep_separate_cooldowns(path: Path):
    """Regression: one entry per provider let each key overwrite the other's."""
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    ph.record_cooldown("elevenlabs", "quota", cooldown_until=until, key_fingerprint="a", path=path)
    ph.record_cooldown("elevenlabs", "quota", cooldown_until=until, key_fingerprint="b", path=path)
    assert ph.is_in_cooldown("elevenlabs", key_fingerprint="a", path=path)
    assert ph.is_in_cooldown("elevenlabs", key_fingerprint="b", path=path)
    assert not ph.is_in_cooldown("elevenlabs", key_fingerprint="c", path=path)


def test_re_recording_the_same_credential_replaces_its_own_entry(path: Path):
    soon = datetime.now(timezone.utc) + timedelta(minutes=1)
    later = datetime.now(timezone.utc) + timedelta(hours=5)
    ph.record_cooldown("groq", "quota", cooldown_until=soon, key_fingerprint="a", path=path)
    ph.record_cooldown("groq", "quota", cooldown_until=later, key_fingerprint="a", path=path)
    assert ph.active_cooldown("groq", key_fingerprint="a", path=path).cooldown_until == later


def test_clear_removes_every_credentials_entry(path: Path):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    ph.record_cooldown("elevenlabs", "quota", cooldown_until=until, key_fingerprint="a", path=path)
    ph.record_cooldown("elevenlabs", "quota", cooldown_until=until, key_fingerprint="b", path=path)
    ph.record_cooldown("groq", "quota", cooldown_until=until, key_fingerprint="g", path=path)
    ph.clear_cooldown("elevenlabs", path=path)
    assert not ph.is_in_cooldown("elevenlabs", key_fingerprint="a", path=path)
    assert not ph.is_in_cooldown("elevenlabs", key_fingerprint="b", path=path)
    assert ph.is_in_cooldown("groq", key_fingerprint="g", path=path)  # another provider untouched


def test_summary_lookup_without_a_fingerprint_sees_any_credentials_cooldown(path: Path):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    ph.record_cooldown("elevenlabs", "quota", cooldown_until=until, key_fingerprint="b", path=path)
    assert ph.active_cooldown("elevenlabs", path=path) is not None  # what the health row and fallback message use


def test_a_legacy_bare_name_entry_is_still_honoured(path: Path):
    """Files written before cooldowns were per-credential keyed by provider name alone."""
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    path.write_text(
        json.dumps({"version": 1, "providers": {"groq": {
            "status": "quota", "since": datetime.now(timezone.utc).isoformat(),
            "cooldown_until": until.isoformat(), "note": "", "key_fingerprint": "old",
        }}}),
        encoding="utf-8",
    )
    assert ph.is_in_cooldown("groq", key_fingerprint="old", path=path)
    assert not ph.is_in_cooldown("groq", key_fingerprint="other", path=path)
    ph.clear_cooldown("groq", path=path)
    assert not ph.is_in_cooldown("groq", key_fingerprint="old", path=path)
