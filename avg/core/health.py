"""Are the render tools actually here, and do they actually run?

The legacy app's Check Health screen (`D:\\AI_Video_Gen`), rebuilt as data.
It answers one question per component - is it present, and what version -
and nothing else: it does not install, repair, or download. The old app's
"Download failed" state is a *report*, not an action this module can take.

**Why this is worth its weight.** Every account and dependency failure in
this project's history was found by a video failing partway through, minutes
and sometimes real money in. A key at zero quota, an ffmpeg missing from a
stripped PATH, and a Pillow import that never landed in a frozen build would
all have shown up here first, before a run started.

Two kinds of check, deliberately different:

  - **Import checks** (Pillow, moviepy, numpy) ask the running interpreter
    what it already has. Cheap, exact, and the version comes from the
    package itself.
  - **The ffmpeg check** runs `ffmpeg -version` as a subprocess, because a
    path existing proves nothing - a truncated download, a wrong
    architecture, or a missing runtime DLL all fail only on exec. It goes
    through `avg.media.environment.ffmpeg_exe()`, so a frozen build checks
    the bundled binary it will really use, not whatever is on PATH.

Results are cached: `check_all()` re-runs at most every `TTL` seconds unless
asked to refresh. The subprocess is the reason - the settings screen must be
free to render this without paying for a process spawn on every mount.

Nothing here prints, prompts, or raises for an unhealthy component. A failed
check is an ordinary `Component` with `ok=False` and the reason in `detail`;
only the API layer decides what a user sees.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from importlib import metadata

from avg.media.environment import ffmpeg_exe

TTL_SECONDS = 300.0

# How long `ffmpeg -version` is given before it is called broken. Generous:
# a cold first exec off a slow disk is not a failure, but a hang is.
_FFMPEG_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class Component:
    """One row of the components table."""

    name: str
    ok: bool
    #: Version string when known, else "". Never a guess.
    version: str = ""
    #: Why it failed, or what it is, in one short sentence for the user.
    detail: str = ""


@dataclass
class _Cache:
    at: float = 0.0
    components: list[Component] = field(default_factory=list)


_CACHE = _Cache()

# `import name` -> the distribution name its version is published under.
# Kept explicit because the two differ for Pillow (`PIL`) and moviepy is
# imported lazily by the render child rather than by this process.
_PACKAGES: tuple[tuple[str, str, str], ...] = (
    ("Pillow", "PIL", "Pillow"),
    ("moviepy", "moviepy", "moviepy"),
    ("NumPy", "numpy", "numpy"),
)


def _check_package(label: str, module: str, distribution: str) -> Component:
    try:
        __import__(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is the answer
        return Component(name=label, ok=False, detail=f"cannot import: {exc}")
    try:
        version = metadata.version(distribution)
    except Exception:  # noqa: BLE001
        # Imports fine but has no installed metadata - true in some frozen
        # builds. Present and usable is the thing being reported, so this
        # is healthy with an unknown version, not a failure.
        return Component(name=label, ok=True, detail="installed (version unavailable)")
    return Component(name=label, ok=True, version=version)


def _check_ffmpeg() -> Component:
    exe = ffmpeg_exe()
    try:
        result = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=_FFMPEG_TIMEOUT,
            # No console window flashes on Windows when the app is frozen
            # and windowed; harmless everywhere else.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return Component(name="FFmpeg", ok=False, detail=f"not found ({exe})")
    except subprocess.TimeoutExpired:
        return Component(name="FFmpeg", ok=False, detail="did not respond to -version")
    except OSError as exc:
        return Component(name="FFmpeg", ok=False, detail=f"could not run: {exc}")

    if result.returncode != 0:
        return Component(name="FFmpeg", ok=False, detail=f"exited {result.returncode}")

    # First line is "ffmpeg version <v> Copyright ...". Take the third word
    # and nothing more - the rest is build configuration noise.
    first = (result.stdout or "").splitlines()[0] if result.stdout else ""
    parts = first.split()
    version = parts[2] if len(parts) >= 3 and parts[0] == "ffmpeg" else ""
    return Component(name="FFmpeg", ok=True, version=version, detail=exe)


def check_all(*, refresh: bool = False, now: float | None = None) -> list[Component]:
    """Every component's current state, cached for `TTL_SECONDS`.

    `refresh=True` is the Recheck button: it ignores the cache and pays for
    the subprocess again. `now` exists so tests can age the cache without
    sleeping.
    """
    stamp = time.monotonic() if now is None else now
    if not refresh and _CACHE.components and (stamp - _CACHE.at) < TTL_SECONDS:
        return list(_CACHE.components)

    components = [_check_ffmpeg()]
    components.extend(_check_package(label, module, dist) for label, module, dist in _PACKAGES)

    _CACHE.at = stamp
    _CACHE.components = components
    return list(components)


def clear_cache() -> None:
    """Forget the cached result. For tests and for a settings change that
    could plausibly move ffmpeg."""
    _CACHE.at = 0.0
    _CACHE.components = []
