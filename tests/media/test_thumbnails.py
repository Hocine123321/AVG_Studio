r"""Tests for thumbnail extraction and caching.

Exercises real `ffmpeg`/`ffprobe` subprocess calls against tiny synthetic
media generated with ffmpeg's own lavfi test sources - the whole point of
this module is shelling out to those binaries correctly, so mocking them
away would test nothing. Nothing here touches the real (read-only, in this
project's dev environment) `D:\AI_Video_Gen` tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from avg.core.models import ProjectDoc
from avg.media.thumbnails import default_cache_dir, thumbnail_for

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _make_project(
    folder: Path, *, with_video: bool, with_images: bool, project_id: str = "proj"
) -> ProjectDoc:
    folder.mkdir(parents=True, exist_ok=True)
    master_output = None

    if with_video:
        video_path = folder / "master_output.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=2",
                str(video_path),
            ],
            capture_output=True,
            check=True,
        )
        master_output = video_path

    if with_images:
        images_dir = folder / "images"
        images_dir.mkdir(exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64",
                "-frames:v", "1", str(images_dir / "001.png"),
            ],
            capture_output=True,
            check=True,
        )

    return ProjectDoc(
        id=project_id,
        title="Test project",
        folder=folder,
        created="2026-07-01T00:00:00",
        master_output=master_output,
    )


def test_extracts_a_frame_from_the_video(tmp_path):
    project = _make_project(tmp_path / "a", with_video=True, with_images=False)
    cache_dir = tmp_path / "cache"

    thumb = thumbnail_for(project, cache_dir)

    assert thumb == cache_dir / f"{project.id}.jpg"
    assert thumb.is_file() and thumb.stat().st_size > 0


def test_second_call_is_a_pure_cache_hit(tmp_path, monkeypatch):
    project = _make_project(tmp_path / "b", with_video=True, with_images=False)
    cache_dir = tmp_path / "cache"

    first = thumbnail_for(project, cache_dir)
    assert first is not None
    mtime = first.stat().st_mtime

    calls: list[object] = []
    original_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(args)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    second = thumbnail_for(project, cache_dir)

    assert second == first
    assert second.stat().st_mtime == mtime
    assert calls == []  # no ffmpeg/ffprobe invocation on a cache hit


def test_falls_back_to_the_first_image_when_there_is_no_video(tmp_path):
    """The rule that matters most: three of the ten real projects are
    mid-pipeline with no master_output.mp4 at all. Without this fallback
    their cards would render blank."""
    project = _make_project(tmp_path / "c", with_video=False, with_images=True)
    cache_dir = tmp_path / "cache"

    thumb = thumbnail_for(project, cache_dir)

    assert thumb is not None
    assert thumb.is_file() and thumb.stat().st_size > 0


def test_returns_none_without_raising_when_there_is_nothing_to_extract(tmp_path):
    project = _make_project(tmp_path / "d", with_video=False, with_images=False)

    assert thumbnail_for(project, tmp_path / "cache") is None


def test_a_broken_video_yields_none_not_an_exception(tmp_path):
    folder = tmp_path / "e"
    folder.mkdir()
    broken_video = folder / "master_output.mp4"
    broken_video.write_bytes(b"this is not a video file")
    project = ProjectDoc(
        id="broken",
        title="Broken",
        folder=folder,
        created="2026-07-01T00:00:00",
        master_output=broken_video,
    )

    assert thumbnail_for(project, tmp_path / "cache") is None


def test_a_missing_ffmpeg_binary_never_raises(tmp_path, monkeypatch):
    project = _make_project(tmp_path / "f", with_video=True, with_images=False)

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert thumbnail_for(project, tmp_path / "cache") is None


def test_cache_never_writes_into_the_project_folder(tmp_path):
    project = _make_project(tmp_path / "g", with_video=True, with_images=False)
    cache_dir = tmp_path / "cache"

    thumbnail_for(project, cache_dir)

    assert not any(project.folder.rglob("*.jpg"))


def test_default_cache_dir_is_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_cache_dir() == tmp_path / "AVG Studio" / "thumbs"
