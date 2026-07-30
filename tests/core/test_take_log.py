"""Tests for the per-take run log.

Two properties are load-bearing: a key must never reach the file, and retry
noise must not bury the run. Everything else is convenience.
"""

from __future__ import annotations

from avg.core.take_log import TakeLog


def test_a_key_never_reaches_the_file(tmp_path):
    """The one scrubber this app has is `error_store._redact`, and this reuses
    it rather than adding a second pattern list that would drift out of step
    with it."""
    log = TakeLog(tmp_path / "take.log")
    log.call(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        200,
        812,
        authorization="Bearer gsk_livekeylivekeylivekey",
    )
    log.error("provider refused: x-api-key: sk-abcdefghijklmnop")
    log.close()

    text = (tmp_path / "take.log").read_text(encoding="utf-8")
    assert "gsk_livekeylivekeylivekey" not in text
    assert "sk-abcdefghijklmnop" not in text
    assert "REDACTED" in text


def test_identical_consecutive_lines_collapse(tmp_path):
    """A retry loop writes the same WARN forty times. Legacy collapsed them
    into one "(xN)" entry (`D:\\AI_Video_Gen\\info.txt:730`) and so does this -
    a log nobody can skim is a log nobody reads."""
    log = TakeLog(tmp_path / "take.log")
    for _ in range(3):
        log.warn("rate limited, retrying")
    log.info("done")
    log.close()

    lines = [line for line in (tmp_path / "take.log").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert sum(1 for line in lines if "rate limited" in line) == 1
    assert "(x3)" in "\n".join(lines)


def test_a_repeat_is_flushed_before_the_next_line(tmp_path):
    """The count has to land in ORDER, not at close: a reader following the
    file must see the repeats where they happened."""
    log = TakeLog(tmp_path / "take.log")
    log.warn("same")
    log.warn("same")
    log.info("after")
    log.close()

    text = (tmp_path / "take.log").read_text(encoding="utf-8")
    assert text.index("(x2)") < text.index("after")


def test_bodies_are_only_written_when_verbose(tmp_path):
    quiet_path = tmp_path / "quiet.log"
    log = TakeLog(quiet_path)
    log.block("prompt", "You are a scriptwriter...")
    log.close()
    # Nothing at all is written, so the file need not even exist - a body in
    # non-verbose mode is skipped before any line is emitted.
    assert not quiet_path.exists() or "scriptwriter" not in quiet_path.read_text(encoding="utf-8")

    loud = TakeLog(tmp_path / "loud.log", verbose=True)
    loud.block("prompt", "You are a scriptwriter...")
    loud.close()
    assert "scriptwriter" in (tmp_path / "loud.log").read_text(encoding="utf-8")


def test_a_body_is_scrubbed_too(tmp_path):
    """Verbose logging is exactly where a key is most likely to appear - in a
    request body echoed back into the log."""
    log = TakeLog(tmp_path / "take.log", verbose=True)
    log.block("request", 'headers: {"Authorization": "Bearer gsk_secretsecretsecret"}')
    log.close()

    assert "gsk_secretsecretsecret" not in (tmp_path / "take.log").read_text(encoding="utf-8")


def test_an_unwritable_path_is_not_an_error(tmp_path):
    """A log that cannot be written must never be the thing that stops a
    render - the same tolerance `ErrorStore._load` already applies."""
    target = tmp_path / "file"
    target.write_text("not a directory", encoding="utf-8")

    log = TakeLog(target / "deeper" / "take.log")
    log.info("still fine")
    log.close()


def test_events_are_logged_by_type_with_their_fields(tmp_path):
    log = TakeLog(tmp_path / "take.log")
    log.event({"type": "phase_finished", "phase": "brief", "duration_ms": 1200})
    log.close()

    text = (tmp_path / "take.log").read_text(encoding="utf-8")
    assert "phase_finished" in text
    assert "brief" in text
    assert "1200" in text


def test_a_failure_event_is_logged_at_error_level(tmp_path):
    """So that skimming for ERROR finds the phase that broke."""
    log = TakeLog(tmp_path / "take.log")
    log.event({"type": "phase_failed", "phase": "voice", "message": "boom"})
    log.close()

    assert "ERROR" in (tmp_path / "take.log").read_text(encoding="utf-8")


def test_a_call_line_names_the_host_not_the_whole_url(tmp_path):
    """The path carries nothing worth the width; the host is the fact."""
    log = TakeLog(tmp_path / "take.log")
    log.call("POST", "https://api.elevenlabs.io/v1/text-to-speech/abc", 402, 91)
    log.close()

    text = (tmp_path / "take.log").read_text(encoding="utf-8")
    assert "api.elevenlabs.io" in text
    assert "text-to-speech" not in text
    assert "402" in text


def test_log_path_for_follows_take_storage(tmp_path):
    """Through `take_root`, so a root-storage take logs at the project folder
    and an own-storage take inside its own - the rule every other reader of a
    take's files follows."""
    from datetime import datetime, timezone

    from avg.core.models import ProjectDoc, Take
    from avg.core.take_log import log_path_for

    now = datetime.now(timezone.utc)
    project = ProjectDoc(
        id="p",
        title="P",
        folder=tmp_path,
        created=now,
        phases={},
        takes=[
            Take(id=1, label="Take 1", created=now, phases={}, storage="root"),
            Take(id=2, label="Take 2", created=now, phases={}, storage="own"),
        ],
        current_take_id=1,
    )

    assert log_path_for(project, 1) == tmp_path / "take.log"
    assert log_path_for(project, 2) == tmp_path / "takes" / "2" / "take.log"
