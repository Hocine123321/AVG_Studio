"""Where a render's tools and inputs live on disk.

Two questions the render has to answer before it can start, neither of which
involves any actual media processing: *which ffmpeg binary do we call*, and
*which folder do we let the render write into*. Both are pure filesystem
plumbing, both are the kind of thing that only bites in a frozen build or on
a user's real project folder, and both are answered here so that
`avg.media.engine`, `avg.media.child` and `avg.media.thumbnails` can assume
the answers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

# ── FFmpeg Location ─────────────────────────────────────────
#
# In development, ffmpeg/ffprobe are expected on PATH (the dev machine already
# has them for rendering). In a frozen (PyInstaller) build there is no ffmpeg
# on the target machine's PATH at all - that is the entire point of shipping
# `vendor/ffmpeg` inside the installer - so this section is the one place that
# decides which binary a caller gets.
#
# Two consumers, two different fixes:
#
# - `avg/media/thumbnails.py` (not protected) calls `ffmpeg_exe()`/
#   `ffprobe_exe()` directly and gets an absolute path to the bundled binary
#   when frozen.
# - `avg/media/vendor/avg.py` is a **protected vendored file that must not be
#   edited**, and it calls bare `["ffmpeg", ...]` / `["ffprobe", ...]` via
#   `subprocess.run`. It cannot be pointed at an absolute path without editing
#   it, so instead `prepend_to_path()` puts the bundled ffmpeg's directory at
#   the front of this process's `PATH` environment variable when frozen. Every
#   bare `"ffmpeg"`/`"ffprobe"` lookup - in this process, and in the spawned
#   render child processes started by `avg.media.engine` (which inherit the
#   parent's environment by default under `multiprocessing`'s spawn start
#   method) - then resolves to the bundled copy without the vendored file ever
#   knowing bundling happened.
#
# Calling `prepend_to_path()` is safe and a no-op in development (not frozen,
# or the bundled folder is somehow missing): PATH is left untouched and bare
# lookups keep working exactly as they do today.

_FFMPEG_EXE_NAME = "ffmpeg"
_FFPROBE_EXE_NAME = "ffprobe"


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
    """Absolute path to the bundled `ffmpeg` when frozen, else `"ffmpeg"`."""
    d = bundled_ffmpeg_dir()
    return str(d / _FFMPEG_EXE_NAME) if d is not None else "ffmpeg"


def ffprobe_exe() -> str:
    """Absolute path to the bundled `ffprobe` when frozen, else `"ffprobe"`."""
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


# ── Filter overlay clips ────────────────────────────────────
#
# `avg.media.vendor.avg._filters_root()` resolves the bundled filter clips
# from `os.environ["AVG_FILTERS_DIR"]`, falling back to a path relative to
# the vendor file itself that assumed the legacy repo's flat layout
# (`Assets/Filters` two directories up from `python/avg.py`) - wrong here,
# and vendor/ is never edited to fix it. This section is the one place
# that resolves the real bundled location (`avg/assets/Filters`, matching
# `avg.pipeline.personality.assets_dir()`'s convention) and sets the env
# var so the vendored resolver finds it without ever needing to change.
#
# Two callers, both idempotent (`os.environ.setdefault`) because either
# can run first depending on how a render was reached:
#   - `avg.pipeline.phases.director.Director2Phase.run` needs the catalogue
#     BEFORE Director II's own prompt is built (same process, no child
#     spawned yet).
#   - `avg.media.engine.MediaEngine.render` needs it before the render
#     child spawns, in case a render is ever triggered without Director II
#     having run first in the same process (e.g. `tools/_dev_serve.py`
#     against an already-planned project).


@lru_cache(maxsize=1)
def filters_dir() -> Path:
    """Directory holding the bundled filter overlay clips
    (`avg/assets/Filters`), in both a dev checkout and a frozen build.

    Independent duplicate of `avg.pipeline.personality.assets_dir()`'s
    exact technique - `avg/media/` may not import `avg/pipeline/`, the
    same reason `bundled_ffmpeg_dir()` above duplicates rather than shares
    its own frozen/dev resolution. The `sys.frozen` branch is inert on
    this branch specifically (never frozen - no PyInstaller here, see
    `docs/linux-port/WINDOWS_VS_LINUX.md`'s "Packaging shape") and always
    falls through to the dev-checkout return below; kept rather than
    removed, matching `bundled_ffmpeg_dir()`'s own precedent immediately
    above of leaving the same dead-but-harmless branch in place rather
    than forking the function's shape between platforms.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "avg" / "assets" / "Filters"
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parent.parent / "assets" / "Filters"


def ensure_filters_dir_env() -> None:
    """Set `AVG_FILTERS_DIR` once, idempotently, so `avg.media.vendor.avg`'s
    `_filters_root()` finds the bundled clips regardless of which entry
    point reaches it first. Safe to call more than once and from more than
    one place - see the module note above."""
    os.environ.setdefault("AVG_FILTERS_DIR", str(filters_dir()))


# ── Console windows ─────────────────────────────────────────
#
# A GUI process on Windows has no console. When it launches a console
# program - ffmpeg, ffprobe, anything the vendored render engine or moviepy
# shells out to - Windows allocates a NEW console window for it, which
# flashes on the user's desktop for the length of the call. A render makes
# dozens of those calls.
#
# `CREATE_NO_WINDOW` suppresses that. Passing it at every call site would
# only cover the call sites this repo owns; `avg/media/vendor/` is protected
# and moviepy is a third-party package, and between them they make most of
# the ffmpeg calls in a render. So `suppress_console_windows()` installs the
# flag as a process-wide DEFAULT instead, which reaches every caller in the
# process including those two - without editing either.

#: 0 on non-Windows, so every use of this is a harmless no-op there.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_console_suppressed = False


def suppress_console_windows() -> None:
    """Make every `subprocess` launch in THIS process windowless.

    Wraps `subprocess.Popen.__init__` so `creationflags` gains
    `CREATE_NO_WINDOW` unless the caller asked for a specific window
    behaviour itself (`CREATE_NEW_CONSOLE` / `DETACHED_PROCESS`), which is
    left alone rather than overridden.

    Call once, early, in any process that renders: the app process at
    startup and the spawned render child before it imports the vendored
    engine. Idempotent - a second call does nothing, so it can never stack
    wrappers on top of each other.

    Deliberately a patch rather than a helper callers must remember: the
    calls that actually flash windows are inside `avg/media/vendor/` (which
    must not be edited) and inside moviepy. A rule that only applies to code
    this repo owns would not fix the symptom.
    """
    global _console_suppressed
    if _console_suppressed or os.name != "nt":
        _console_suppressed = True
        return

    original = subprocess.Popen.__init__
    # Flags that mean "the caller has an opinion about the window" - adding
    # CREATE_NO_WINDOW alongside either is contradictory, so leave them be.
    explicit = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        flags = kwargs.get("creationflags", 0)
        if not flags & explicit:
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        return original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched  # type: ignore[method-assign]
    _console_suppressed = True


# ── Workspace ───────────────────────────────────────────────
#
# `render_project` writes `master_output.mp4` into the folder it is given and
# reads its inputs (`images/`, `audio/`, `render/`) from that same folder.
# Point it at a real project and a completed render silently overwrites the
# finished video that project already holds - the user's actual work, with no
# undo.
#
# Slice 1 re-renders existing projects specifically to compare the new output
# against the original, so overwriting the original destroys the very thing
# being compared against.
#
# `render_workspace` gives the render a disposable copy to work in. The
# original folder is opened read-only and never written to.

# Copied into the workspace because the render reads them. Everything else in
# a project folder (previous outputs, logs, scratch) is not an input, so it is
# left behind rather than duplicated.
_INPUT_DIRS = ("images", "audio", "render", "script")
_INPUT_FILES = ("project.json", "pipeline_state.json")


def _link_file(source: Path, dest: Path) -> None:
    """Hardlink `source` to `dest`, copying if the link is refused.

    A link fails across volumes, on FAT32, and on some network shares -
    all ordinary situations, none of them worth surfacing as an error.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def _link_tree(source: Path, dest: Path) -> None:
    """`_link_file` over a whole directory, preserving its shape."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*")):
        target = dest / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            _link_file(item, target)


@contextmanager
def render_workspace(project_folder: Path, keep: bool | None = None) -> Iterator[Path]:
    """Yield a disposable copy of `project_folder` for a render to write into.

    The copy carries only what the render reads. `master_output.mp4` is
    deliberately NOT copied: the workspace starts without one, so a file
    appearing there is unambiguously this render's output rather than a
    leftover that would make a failed render look successful.

    Inputs are HARDLINKED where the filesystem allows it and copied only
    where it does not. A real project is images and audio measured in
    hundreds of megabytes, and copying them was why a render sat silent
    for minutes before the child process even started. A hardlink is the
    same bytes under a second name, so the no-master_output property above
    is untouched: nothing in the render writes over an input in place, it
    only adds the output file.

    `keep=None` reads `AVG_KEEP_RENDER_WORKSPACE`. This used to be passed
    `True` unconditionally by the render route, so every render ever run
    left a full copy of the project in `%TEMP%` permanently - the escape
    hatch for inspecting a render's workspace is worth keeping, but it
    cannot be the default.
    """
    if keep is None:
        keep = os.environ.get("AVG_KEEP_RENDER_WORKSPACE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    workspace = Path(tempfile.mkdtemp(prefix="avg-render-"))
    try:
        for name in _INPUT_DIRS:
            source = project_folder / name
            if source.is_dir():
                _link_tree(source, workspace / name)
        for name in _INPUT_FILES:
            source = project_folder / name
            if source.is_file():
                _link_file(source, workspace / name)
        yield workspace
    finally:
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)
