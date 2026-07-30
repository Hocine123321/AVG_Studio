"""Tests for the app error store (`avg.core.error_store`).

The store exists so a user can hand a failure to an AI assistant without
having to describe it from memory. That makes two properties load-bearing,
and both are tested here: it must never drop the newest error, and it must
never persist a secret.
"""

from __future__ import annotations

from avg.core.error_store import ErrorStore, classify


def test_records_survive_a_reload(tmp_path):
    store = ErrorStore(tmp_path / "errors.json")
    store.record(severity="critical", source="render", summary="boom", detail="traceback")

    reopened = ErrorStore(tmp_path / "errors.json")
    records = reopened.all()
    assert len(records) == 1
    assert records[0].summary == "boom"
    assert records[0].severity == "critical"


def test_newest_first_and_capped(tmp_path):
    store = ErrorStore(tmp_path / "errors.json", limit=3)
    for i in range(5):
        store.record(severity="low", source="ui", summary=f"e{i}", detail="")

    summaries = [r.summary for r in store.all()]
    # Newest first, and the cap drops the OLDEST. A store that dropped the
    # newest would be worse than no store at all - the error you are trying
    # to report is always the one that just happened.
    assert summaries == ["e4", "e3", "e2"]


def test_api_keys_are_redacted_before_anything_is_written(tmp_path):
    """A traceback can carry an Authorization header. It must not reach disk.

    Asserts against the raw file bytes, not the parsed object: the point is
    that the secret never lands, not that a reader hides it.
    """
    path = tmp_path / "errors.json"
    store = ErrorStore(path)
    store.record(
        severity="critical",
        source="providers",
        summary="auth failed",
        detail="Authorization: Bearer gsk_livekeyvalue123456 \nx-api-key: sk-abcdef123456",
    )

    raw = path.read_text(encoding="utf-8")
    assert "gsk_livekeyvalue123456" not in raw
    assert "sk-abcdef123456" not in raw
    assert "REDACTED" in raw


def test_context_values_are_redacted_too(tmp_path):
    path = tmp_path / "errors.json"
    ErrorStore(path).record(
        severity="low",
        source="api",
        summary="request failed",
        context={"url": "https://api.groq.com/v1?api_key=gsk_shouldnotland"},
    )
    assert "gsk_shouldnotland" not in path.read_text(encoding="utf-8")


def test_clear_empties_the_store(tmp_path):
    store = ErrorStore(tmp_path / "errors.json")
    store.record(severity="low", source="ui", summary="x", detail="")
    store.clear()
    assert store.all() == []


def test_a_corrupt_store_file_does_not_break_the_app(tmp_path):
    path = tmp_path / "errors.json"
    path.write_text("{not json", encoding="utf-8")
    store = ErrorStore(path)
    # Starts empty rather than raising: an unreadable error log must never
    # itself become the thing that stops the app.
    assert store.all() == []
    store.record(severity="low", source="ui", summary="fine", detail="")
    assert len(store.all()) == 1


def test_classification_follows_the_legacy_vocabulary():
    # Anything that stops a run outright, or that the user cannot act on.
    assert classify(kind="bug", status=None, source="pipeline") == "critical"
    assert classify(kind=None, status=500, source="api") == "critical"
    # Recoverable, and usually about an account - the user can do something.
    assert classify(kind="rate_limit", status=429, source="providers") == "needs_review"
    assert classify(kind="auth", status=401, source="providers") == "needs_review"
    # Everything else.
    assert classify(kind=None, status=404, source="api") == "low"


def test_a_degradation_is_low_severity_and_still_recorded(tmp_path):
    """A run that quietly produced a WORSE video is the class of event that
    went entirely unseen: the fallback worked, so nothing failed, so nothing
    was recorded, and the user never learned their narration came from a
    different provider than the one they picked."""
    store = ErrorStore(tmp_path / "errors.json")

    store.degraded(
        source="pipeline",
        summary="ElevenLabs unavailable; used Edge TTS",
        context={"project_id": "p", "phase": "voice"},
    )

    record = store.all()[0]
    assert record.severity == "low"
    assert record.context["phase"] == "voice"
    assert "Edge TTS" in record.summary


def test_a_degradation_is_redacted_like_anything_else(tmp_path):
    """It goes through `record`, so it cannot become the one path that leaks
    a key."""
    store = ErrorStore(tmp_path / "errors.json")

    store.degraded(source="pipeline", summary="refused with x-api-key: sk-abcdefghijklmnop")

    assert "sk-abcdefghijklmnop" not in store.all()[0].summary
