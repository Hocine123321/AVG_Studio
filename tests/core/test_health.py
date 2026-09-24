"""Tests for the component health checks (`avg.core.health`).

The ffmpeg check is a real subprocess, so these do not assert that it
succeeds - that would make the suite fail on a machine without ffmpeg,
which is exactly the situation this module exists to REPORT rather than to
break on. What is tested is the reporting contract: a broken component is a
row with `ok=False` and a reason, never an exception; the cache is honoured
and the refresh flag defeats it.
"""

from __future__ import annotations

import subprocess

import pytest

from avg.core import health


@pytest.fixture(autouse=True)
def _clean_cache():
    health.clear_cache()
    yield
    health.clear_cache()


def test_check_all_reports_a_row_per_component_and_never_raises():
    components = health.check_all(refresh=True)
    names = [c.name for c in components]
    assert "FFmpeg" in names
    assert {"Pillow", "moviepy", "NumPy"} <= set(names)
    # Whatever this machine has, every row is well-formed.
    for component in components:
        assert isinstance(component.ok, bool)
        assert isinstance(component.version, str)


def test_a_missing_ffmpeg_is_a_failed_row_not_an_exception(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("no ffmpeg here")

    monkeypatch.setattr(subprocess, "run", missing)
    ffmpeg = next(c for c in health.check_all(refresh=True) if c.name == "FFmpeg")
    assert ffmpeg.name == "FFmpeg"
    assert ffmpeg.ok is False
    assert "not found" in ffmpeg.detail


def test_a_hanging_ffmpeg_is_a_failed_row(monkeypatch):
    def hangs(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(subprocess, "run", hangs)
    ffmpeg = next(c for c in health.check_all(refresh=True) if c.name == "FFmpeg")
    assert ffmpeg.ok is False
    assert "respond" in ffmpeg.detail


def test_a_nonzero_exit_is_a_failed_row(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(args=["ffmpeg"], returncode=127, stdout="", stderr=""),
    )
    ffmpeg = next(c for c in health.check_all(refresh=True) if c.name == "FFmpeg")
    assert ffmpeg.ok is False
    assert "127" in ffmpeg.detail


def test_the_version_comes_from_ffmpegs_own_first_line(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=["ffmpeg"],
            returncode=0,
            stdout="ffmpeg version 7.1-full_build Copyright (c) 2000-2024\nbuilt with gcc\n",
            stderr="",
        ),
    )
    ffmpeg = next(c for c in health.check_all(refresh=True) if c.name == "FFmpeg")
    assert ffmpeg.ok is True
    assert ffmpeg.version == "7.1-full_build"


def test_the_cache_prevents_a_second_subprocess_within_the_ttl(monkeypatch):
    calls = {"n": 0}

    def counted(*_a, **_k):
        calls["n"] += 1
        return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="ffmpeg version 7.1 x\n", stderr="")

    monkeypatch.setattr(subprocess, "run", counted)

    health.check_all(now=1000.0)
    health.check_all(now=1000.0 + health.TTL_SECONDS - 1)
    assert calls["n"] == 1, "a cached read must not spawn a process"

    # Past the TTL it re-checks...
    health.check_all(now=1000.0 + health.TTL_SECONDS + 1)
    assert calls["n"] == 2

    # ...and refresh defeats the cache outright. This is the Recheck button:
    # if it were cached it would look like it did nothing.
    health.check_all(refresh=True, now=1000.0 + health.TTL_SECONDS + 1)
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# Full dependency coverage (Slice 7)


def test_every_runtime_dependency_is_checked():
    """The check covered four packages; the app needs a dozen.

    A dependency check that only looks at the render path cannot answer
    "why won't it start", which is the question it exists for.
    """
    names = {c.name for c in health.check_all(refresh=True)}
    assert {
        "FFmpeg",
        "Pillow",
        "moviepy",
        "NumPy",
        "FastAPI",
        "uvicorn",
        "pywebview",
        "httpx",
        "pydantic",
        "edge-tts",
        "pydub",
        "imageio-ffmpeg",
    } <= names


def test_essential_and_optional_are_distinguished():
    by_name = {c.name: c for c in health.check_all(refresh=True)}
    # Without these there is no app and no render.
    assert by_name["FFmpeg"].required is True
    assert by_name["FastAPI"].required is True
    assert by_name["pywebview"].required is True
    # Voice has a second provider, so Edge TTS is degradable.
    assert by_name["edge-tts"].required is False


def test_pydub_is_required_because_voice_cannot_run_without_it():
    """It was declared optional and it is not.

    `avg/pipeline/phases/voice.py` imports pydub for assembly and calls
    `remove_silences_core`, which imports it too. Neither has a path that
    skips it, so a machine without pydub fails the Voice phase outright -
    while the health screen said the dependency was optional and only
    "refined silence handling".
    """
    by_name = {c.name: c for c in health.check_all(refresh=True)}
    assert by_name["pydub"].required is True


def test_essential_components_are_listed_first():
    """The screen is read top-down, and the row that explains why nothing
    works should not be below three optional ones."""
    components = health.check_all(refresh=True)
    required_flags = [c.required for c in components]
    assert required_flags == sorted(required_flags, reverse=True)


def test_a_missing_optional_component_does_not_fail_the_stage():
    """An optional dependency is worth listing, not worth failing over.

    Observed live: a machine missing a degradable dependency showed
    "Dependency check: Failed" in red next to a row labelled Optional. A
    screen that cries wolf about a degradable dependency stops being read
    at all.

    The example is edge-tts rather than pydub: pydub became a declared
    requirement on 2026-07-29 (the Voice phase has no path that skips it),
    so it is no longer an optional anything.
    """
    stage = health.Stage(
        key="dependencies",
        name="Dependency check",
        detail="",
        checks=[
            health.Component(name="FFmpeg", ok=True, required=True),
            health.Component(name="edge-tts", ok=False, detail="cannot import", required=False),
        ],
    )
    assert stage.ok is True

    stage.checks[0] = health.Component(name="FFmpeg", ok=False, required=True)
    assert stage.ok is False


def test_deep_checks_exercise_things_and_clean_up(tmp_path):
    """The three existing stages ask "is it present and does it parse".
    This one does the thing and undoes it."""
    from avg.core.health import _deep_checks

    output_root = tmp_path / "library"
    output_root.mkdir()
    checks = _deep_checks(output_root=output_root)

    names = {c.name for c in checks}
    assert "Settings round-trip" in names
    assert "Output folder write" in names
    # Nothing may be left behind.
    assert list(output_root.iterdir()) == []


def test_deep_checks_output_folder_removes_a_folder_it_created(tmp_path):
    """Slice 12 review Minor: `mkdir(parents=True, exist_ok=True)` happily
    creates a whole chain of missing ancestors, and the check used to leave
    every one of them behind when `output_root` itself did not exist yet -
    the common case for a fresh install being diagnosed before its library
    folder has ever been chosen or written to."""
    from avg.core.health import _deep_checks

    missing_root = tmp_path / "not" / "yet" / "created" / "library"
    assert not missing_root.parent.exists()

    _deep_checks(output_root=missing_root)

    assert not missing_root.parent.parent.parent.exists(), "the whole chain this check made must be gone"


def test_deep_checks_report_a_failure_rather_than_raising(tmp_path):
    """A check that cannot run is a red row, never an exception - the same
    contract the other three stages already keep.

    A merely-nonexistent, writable path is not enough to provoke a failure -
    `mkdir(parents=True)` happily creates any depth of new directories under
    a writable tmp_path, on every platform this suite runs on. What
    genuinely cannot be turned into a directory is a path that already names
    a FILE: `mkdir` on a child of a file raises rather than creating it.

    Asserts the SPECIFIC row rather than "any row failed" (the Slice 12
    review's Minor): `any(...)` passes for the wrong reason on a machine
    whose FFmpeg is genuinely broken, since `_deep_checks` also runs an
    FFmpeg encode - which would make this test green even if the Output
    folder write check this scenario is actually provoking silently started
    passing (or raising) instead.
    """
    from avg.core.health import _deep_checks

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    checks = _deep_checks(output_root=blocked / "cannot")
    by_name = {c.name: c for c in checks}
    assert by_name["Output folder write"].ok is False


def test_the_report_has_four_stages_only_when_asked(tmp_path):
    from avg.core.health import report

    shallow = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    assert [s.key for s in shallow] == ["dependencies", "warmup", "safeguards"]

    deep = report(
        settings_path=tmp_path / "s.json",
        channels_dir=tmp_path / "c",
        output_root=tmp_path,
        deep=True,
    )
    assert [s.key for s in deep] == ["dependencies", "warmup", "safeguards", "deep"]


def test_no_secret_value_appears_in_a_deep_check(tmp_path):
    """The secret round-trip proves DPAPI works on a throwaway string. It
    must never touch, or echo, a stored key.

    Slice 12 review Minor: `"sk-" not in blob` against code that never reads
    a key cannot fail for the reason its name claims - `_deep_checks` has no
    parameter through which a real key could even reach it. Made meaningful
    by actually storing one first, through `avg.config.secrets` (the only
    module allowed to touch a key at all) into this test's own throwaway
    `secrets_path`, so the assertion is proving something rather than
    describing an impossibility.
    """
    from avg.config import secrets as secrets_config
    from avg.core.health import _deep_checks

    real_key = "sk-do-not-leak-this-value-into-a-deep-check"
    secrets_config.set_key("groq", real_key, tmp_path / "secrets.dat")

    checks = _deep_checks(output_root=tmp_path)
    blob = " ".join(f"{c.name} {c.detail} {c.version}" for c in checks)
    assert real_key not in blob
    assert "sk-" not in blob


from avg.config import provider_health


def test_provider_cooldowns_check_is_ok_when_nothing_is_in_cooldown(tmp_path, monkeypatch):
    from avg.core.health import report

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is True
    assert row.required is False


def test_provider_cooldowns_check_lists_active_ones(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from avg.core.health import report

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is False
    assert "groq" in row.detail


def test_provider_cooldowns_check_never_raises_even_if_the_file_is_corrupt(tmp_path, monkeypatch):
    from avg.core.health import report

    path = tmp_path / "provider_health.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(provider_health, "default_provider_health_path", lambda: path)

    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is True
