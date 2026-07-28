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
    assert names[0] == "FFmpeg"
    assert {"Pillow", "moviepy", "NumPy"} <= set(names)
    # Whatever this machine has, every row is well-formed.
    for component in components:
        assert isinstance(component.ok, bool)
        assert isinstance(component.version, str)


def test_a_missing_ffmpeg_is_a_failed_row_not_an_exception(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("no ffmpeg here")

    monkeypatch.setattr(subprocess, "run", missing)
    ffmpeg = health.check_all(refresh=True)[0]
    assert ffmpeg.name == "FFmpeg"
    assert ffmpeg.ok is False
    assert "not found" in ffmpeg.detail


def test_a_hanging_ffmpeg_is_a_failed_row(monkeypatch):
    def hangs(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(subprocess, "run", hangs)
    ffmpeg = health.check_all(refresh=True)[0]
    assert ffmpeg.ok is False
    assert "respond" in ffmpeg.detail


def test_a_nonzero_exit_is_a_failed_row(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(args=["ffmpeg"], returncode=127, stdout="", stderr=""),
    )
    ffmpeg = health.check_all(refresh=True)[0]
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
    ffmpeg = health.check_all(refresh=True)[0]
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
