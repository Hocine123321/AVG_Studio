"""Resolve the ffmpeg/ffprobe binaries this app should use.

In development, ffmpeg/ffprobe are expected on PATH (the dev machine already
has them for rendering). In a frozen (PyInstaller) build there is no ffmpeg
on the target machine's PATH at all - that is the entire point of shipping
`vendor/ffmpeg` inside the installer - so this module is the one place that
decides which binary a caller gets.

Two consumers, two different fixes:

- `avg/media/thumbnails.py` (not protected) calls `ffmpeg_exe()`/
  `ffprobe_exe()` directly and gets an absolute path to the bundled binary
  when frozen.
- `avg/media/vendor/avg.py` is a **protected vendored file that must not be
  edited**, and it calls bare `["ffmpeg", ...]` / `["ffprobe", ...]` via
  `subprocess.run`. It cannot be pointed at an absolute path without editing
  it, so instead `prepend_to_path()` puts the bundled ffmpeg's directory at
  the front of this process's `PATH` environment variable when frozen. Every
  bare `"ffmpeg"`/`"ffprobe"` lookup - in this process, and in the spawned
  render child processes started by `avg.media.engine` (which inherit the
  parent's environment by default under `multiprocessing`'s spawn start
  method) - then resolves to the bundled copy without the vendored file ever
  knowing bundling happened.

Calling `prepend_to_path()` is safe and a no-op in development (not frozen,
or the bundled folder is somehow missing): PATH is left untouched and bare
lookups keep working exactly as they do today.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

_FFMPEG_EXE_NAME = "ffmpeg.exe"
_FFPROBE_EXE_NAME = "ffprobe.exe"


@lru_cache(maxsize=1)
def bundled_ffmpeg_dir() -> Path | None:
    """The `vendor/ffmpeg` folder inside a frozen build, or `None`.

    Returns `None` whenever this is not a frozen PyInstaller process, or the
    expected folder isn't where it should be (e.g. someone building without
    the vendor data) - callers fall back to PATH lookup in both cases.
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    candidate = Path(meipass) / "vendor" / "ffmpeg"
    if candidate.is_dir():
        return candidate
    return None


def ffmpeg_exe() -> str:
    """Absolute path to the bundled `ffmpeg.exe` when frozen, else `"ffmpeg"`."""
    d = bundled_ffmpeg_dir()
    return str(d / _FFMPEG_EXE_NAME) if d is not None else "ffmpeg"


def ffprobe_exe() -> str:
    """Absolute path to the bundled `ffprobe.exe` when frozen, else `"ffprobe"`."""
    d = bundled_ffmpeg_dir()
    return str(d / _FFPROBE_EXE_NAME) if d is not None else "ffprobe"


def prepend_to_path() -> None:
    """Put the bundled ffmpeg directory at the front of this process's PATH.

    No-op unless running frozen with a bundled `vendor/ffmpeg` present.
    Idempotent - safe to call more than once (it always inserts at the
    front rather than accumulating duplicates is not guaranteed across
    repeated calls, but this is only ever called once, at startup, before
    anything spawns a subprocess or a render child).
    """
    d = bundled_ffmpeg_dir()
    if d is None:
        return
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")

    # moviepy's fallback render path doesn't shell out to a bare "ffmpeg" at
    # all - it goes through imageio_ffmpeg, which resolves its OWN bundled
    # static binary (PyInstaller's imageio_ffmpeg hook ships one
    # automatically, independent of PATH) unless IMAGEIO_FFMPEG_EXE says
    # otherwise. Pointing it at the same vendored exe means every ffmpeg
    # invocation in the frozen app - direct subprocess calls and moviepy's -
    # is the one binary this app actually ships and tests, not a second,
    # untested copy that happened to come along for the ride.
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(d / _FFMPEG_EXE_NAME)
