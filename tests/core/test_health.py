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


# ── Slice 12 Task 4: Deep Check exercises the app ──────────────────────────

def test_deep_checks_exercise_things_and_clean_up(tmp_path):
    """The three existing stages ask "is it present and does it parse".
    This one does the thing and undoes it."""
    from avg.core.health import _deep_checks

    output_root = tmp_path / "library"
    output_root.mkdir()
    checks = _deep_checks(settings_path=tmp_path / "settings.json", output_root=output_root)

    names = {c.name for c in checks}
    assert "Settings round-trip" in names
    assert "Output folder write" in names
    # Nothing may be left behind.
    assert list(output_root.iterdir()) == []


def test_deep_checks_report_a_failure_rather_than_raising(tmp_path):
    """A check that cannot run is a red row, never an exception - the same
    contract the other three stages already keep."""
    from avg.core.health import _deep_checks

    checks = _deep_checks(
        settings_path=tmp_path / "settings.json",
        output_root=tmp_path / "does" / "not" / "exist" / "and" / "cannot",
    )
    assert any(c.ok is False for c in checks)


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
    must never touch, or echo, a stored key."""
    from avg.core.health import _deep_checks

    checks = _deep_checks(settings_path=tmp_path / "settings.json", output_root=tmp_path)
    blob = " ".join(f"{c.name} {c.detail} {c.version}" for c in checks)
    assert "sk-" not in blob
