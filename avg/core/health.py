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

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

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
    #: Whether the app can do its job without this. NOT "whether pip
    #: installed it" - the split exists because a dozen equal-weight rows
    #: cannot answer "which missing one is why nothing works".
    required: bool = True


@dataclass
class _Cache:
    at: float = 0.0
    components: list[Component] = field(default_factory=list)


_CACHE = _Cache()

# `import name` -> the distribution name its version is published under.
# Kept explicit because the two differ for Pillow (`PIL`) and moviepy is
# imported lazily by the render child rather than by this process.
# (label, import name, distribution name, required)
#
# This covered four packages - FFmpeg, Pillow, moviepy, NumPy - which is
# the render path and nothing else. A dependency check that only knows the
# render path cannot answer "why won't it start", which is the question a
# user opens this screen with.
#
# Import names differ from distribution names often enough to be worth
# spelling out: PIL/Pillow, webview/pywebview, edge_tts/edge-tts.
_PACKAGES: tuple[tuple[str, str, str, bool], ...] = (
    # The app itself. Without any of these there is no window and no API.
    ("FastAPI", "fastapi", "fastapi", True),
    ("uvicorn", "uvicorn", "uvicorn", True),
    ("pywebview", "webview", "pywebview", True),
    ("pydantic", "pydantic", "pydantic", True),
    ("httpx", "httpx", "httpx", True),
    # Rendering.
    ("Pillow", "PIL", "Pillow", True),
    ("moviepy", "moviepy", "moviepy", True),
    ("NumPy", "numpy", "numpy", True),
    ("imageio-ffmpeg", "imageio_ffmpeg", "imageio-ffmpeg", True),
    # Degradable. Edge TTS is the free voice provider but ElevenLabs
    # exists, and pydub only refines silence handling - a missing one is
    # worth knowing about, not worth calling the app broken over.
    ("edge-tts", "edge_tts", "edge-tts", False),
    ("pydub", "pydub", "pydub", False),
)


def _check_package(label: str, module: str, distribution: str, required: bool) -> Component:
    # Importing to check costs whatever that import costs - which for
    # edge-tts is the ~2s that Slice 7 took OFF startup. That is fine and
    # must stay fine: a health check runs on demand, never at import, so
    # this does not put the cost back. Do not "optimise" this into a
    # metadata-only check; a package that installs but cannot import is
    # exactly the failure worth catching.
    try:
        __import__(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is the answer
        return Component(name=label, ok=False, detail=f"cannot import: {exc}", required=required)
    try:
        version = metadata.version(distribution)
    except Exception:  # noqa: BLE001
        # Imports fine but has no installed metadata - true in some frozen
        # builds. Present and usable is the thing being reported, so this
        # is healthy with an unknown version, not a failure.
        return Component(
            name=label, ok=True, detail="installed (version unavailable)", required=required
        )
    return Component(name=label, ok=True, version=version, required=required)


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
    components.extend(
        _check_package(label, module, dist, required)
        for label, module, dist, required in _PACKAGES
    )
    # Essential first: this screen is read top-down, and the row that
    # explains why nothing works must not sit below three optional ones.
    components.sort(key=lambda c: (not c.required, c.name.lower()))

    _CACHE.at = stamp
    _CACHE.components = components
    return list(components)


# ── The three-stage report ──────────────────────────────────
#
# The legacy app ran startup as three named stages and showed the user which
# one it was in. Rebuilt here as data, in the same order and for the same
# reason - when launch goes wrong, "which stage failed" is the whole
# diagnosis:
#
#   1. Dependency check - are the execution tools present and runnable
#   2. App warmup       - does this install's own config actually parse
#   3. Safeguards       - is anything about to fail that we can see coming
#
# Ordered, and reported in order, because each stage assumes the one before
# it passed: warmup cannot mean anything if ffmpeg is missing, and a
# safeguard about output disk space is noise if the config never loaded.


@dataclass(frozen=True, slots=True)
class Stage:
    key: str
    name: str
    detail: str
    checks: list[Component]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _warmup_checks(settings_path: Path | None, channels_dir: Path | None) -> list[Component]:
    """Stage 2: does this installation's own configuration parse.

    Reads the same documents the app reads at startup, through the same
    functions, so a corrupt settings.json shows up here as a named failure
    instead of as an exception somewhere further in.
    """
    checks: list[Component] = []

    try:
        from avg.config.settings import load_settings

        doc = load_settings(settings_path) if settings_path is not None else load_settings()
        checks.append(
            Component(
                name="Settings document",
                ok=True,
                detail=f"output root: {doc.output_root or 'not chosen yet'}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Settings document", ok=False, detail=f"could not load: {exc}"))

    try:
        from avg.config import channels as channels_config

        listed = channels_config.list_channels(channels_dir) if channels_dir is not None else channels_config.list_channels()
        checks.append(
            Component(name="Channels", ok=True, detail=f"{len(listed)} channel(s)")
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Channels", ok=False, detail=f"could not load: {exc}"))

    try:
        from avg.core.settings_schema import SETTINGS

        checks.append(
            Component(name="Settings catalogue", ok=True, detail=f"{len(SETTINGS)} settings")
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Settings catalogue", ok=False, detail=f"could not import: {exc}"))

    return checks


def _safeguard_checks(output_root: Path | None) -> list[Component]:
    """Stage 3: things that will break a run, found before the run.

    Every one of these is a failure this project has actually seen: a
    provider at zero quota discovered only when a video died, and an output
    folder that could not be written to discovered at the end of a render
    rather than the start.

    A safeguard that cannot be checked cheaply is not invented here. There
    is no test render and no provider ping - a ping would cost money on the
    paid providers, which is exactly what the no-live-calls rule forbids.
    """
    checks: list[Component] = []

    if output_root is not None:
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            probe = output_root / ".avg-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(Component(name="Library folder writable", ok=True, detail=str(output_root)))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Component(name="Library folder writable", ok=False, detail=f"{output_root}: {exc}")
            )

        try:
            free_gb = shutil.disk_usage(output_root).free / (1024**3)
            # A render writes intermediate frames plus the finished mp4. Under
            # a couple of GB it is a question of when, not whether, it fails.
            checks.append(
                Component(
                    name="Free disk space",
                    ok=free_gb >= 2.0,
                    version=f"{free_gb:.1f} GB",
                    detail="at least 2 GB is wanted for a render",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Component(name="Free disk space", ok=False, detail=str(exc)))

    try:
        from avg.config.secrets import has_key

        # Reports only WHETHER a key is set - never a value. Same rule the
        # /api/keys route follows; see `avg/config/secrets.py`.
        configured = [name for name in ("groq", "gemini", "elevenlabs", "assemblyai") if has_key(name)]
        checks.append(
            Component(
                name="Provider keys",
                # Text generation is the one thing nothing can substitute
                # for, so a run needs at least one text provider keyed.
                ok=any(name in configured for name in ("groq", "gemini")),
                version=f"{len(configured)} set",
                detail=", ".join(configured) if configured else "no provider keys configured",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Provider keys", ok=False, detail=str(exc)))

    return checks


def report(
    *,
    refresh: bool = False,
    settings_path: Path | None = None,
    channels_dir: Path | None = None,
    output_root: Path | None = None,
) -> list[Stage]:
    """The full three-stage startup report, in order.

    Paths are injected rather than resolved here so tests never touch the
    real `%APPDATA%` - the same rule `create_app` follows.
    """
    return [
        Stage(
            key="dependencies",
            name="Dependency check",
            detail="The tools a render cannot run without",
            checks=check_all(refresh=refresh),
        ),
        Stage(
            key="warmup",
            name="App warmup",
            detail="Configuration and catalogue loading",
            checks=_warmup_checks(settings_path, channels_dir),
        ),
        Stage(
            key="safeguards",
            name="Safeguards",
            detail="What would stop the next run",
            checks=_safeguard_checks(output_root),
        ),
    ]


def clear_cache() -> None:
    """Forget the cached result. For tests and for a settings change that
    could plausibly move ffmpeg."""
    _CACHE.at = 0.0
    _CACHE.components = []
