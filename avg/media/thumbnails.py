r"""Thumbnail extraction and caching for the project grid.

Source order per project, per the approved design: a frame pulled from
`master_output.mp4` at 35% of its duration; if there is no video (a project
still mid-pipeline), the first image file in the project's `images/` folder;
if neither exists, no thumbnail at all - the UI shows its own fallback
rather than a broken `<img>`.

Extraction shells out to `ffmpeg`/`ffprobe`, resolved via
`avg.media.ffmpeg_locate` (the bundled binary in a frozen build, PATH in
development) and is lazy: the first request for a project's thumbnail
extracts and caches it, every request after that reads the cached JPEG
straight off disk.

The cache lives under the user's `%LOCALAPPDATA%`, never inside a project
folder and never inside the (read-only, in Slice 1's dev environment)
`D:\AI_Video_Gen` tree - this module writes to exactly one place, the
cache directory it is given, and never touches `project.folder`.

Every failure path - missing ffmpeg, a corrupt or truncated video, an empty
or missing `images/` folder, a non-zero exit code - returns `None`. A broken
thumbnail must never raise and take the API route serving it down with it;
`thumbnail_for` has no path that propagates an exception to its caller.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from avg.core.models import ProjectDoc
from avg.media.ffmpeg_locate import ffmpeg_exe, ffprobe_exe

logger = logging.getLogger(__name__)

# 400px wide, JPEG quality 6 via ffmpeg's -q:v scale (2 best .. 31 worst) -
# both values come from the approved design, not re-derived here.
THUMB_WIDTH = 400
JPEG_QUALITY = "6"

# Where in the master output's timeline to grab a frame from. 35% lands past
# any cold-open/title card on most of this app's renders without running
# far enough in to risk landing after a short video's last real frame.
_TIMESTAMP_FRACTION = 0.35

# Public (no leading underscore) - avg.api.app's images-listing route reuses
# this so "what counts as an image" has exactly one definition.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# Generous but bounded - a hung ffmpeg/ffprobe process must not hang the
# HTTP request serving the thumbnail forever.
_PROBE_TIMEOUT_SECONDS = 20
_EXTRACT_TIMEOUT_SECONDS = 30


def default_cache_dir() -> Path:
    """`%LOCALAPPDATA%/AVG Studio/thumbs` - the one place this module writes.

    Falls back to `~/AppData/Local` if `LOCALAPPDATA` is somehow unset
    (never true on a real user's Windows session, but keeps this callable
    in stripped-down environments) rather than raising.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "AVG Studio" / "thumbs"


def _probe_duration_seconds(video: Path) -> float | None:
    """Best-effort media duration via ffprobe. `None` on any failure."""
    try:
        result = subprocess.run(
            [
                ffprobe_exe(),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning("ffprobe failed to run for %s", video, exc_info=True)
        return None

    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def _run_ffmpeg_still(args: list[str], dest: Path) -> bool:
    try:
        result = subprocess.run(args, capture_output=True, timeout=_EXTRACT_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("ffmpeg failed to run for %s", dest, exc_info=True)
        return False
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _extract_video_frame(video: Path, dest: Path) -> bool:
    duration = _probe_duration_seconds(video)
    if duration is None:
        return False
    timestamp = duration * _TIMESTAMP_FRACTION
    return _run_ffmpeg_still(
        [
            ffmpeg_exe(), "-y",
            "-ss", f"{timestamp:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale={THUMB_WIDTH}:-2",
            "-q:v", JPEG_QUALITY,
            str(dest),
        ],
        dest,
    )


def list_image_files(images_dir: Path) -> list[Path]:
    """Sorted list of image files directly inside `images_dir`.

    Top-level only - a legacy project's `images/_composites` subfolder holds
    assembled composites, not the individual per-scene stills, and is
    deliberately not descended into. Empty (never raises) if the folder is
    missing or unreadable.
    """
    if not images_dir.is_dir():
        return []
    try:
        return sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    except OSError:
        return []


def _first_image_file(images_dir: Path) -> Path | None:
    files = list_image_files(images_dir)
    return files[0] if files else None


def _extract_from_image(source: Path, dest: Path) -> bool:
    return _run_ffmpeg_still(
        [
            ffmpeg_exe(), "-y",
            "-i", str(source),
            "-vf", f"scale={THUMB_WIDTH}:-2",
            "-q:v", JPEG_QUALITY,
            str(dest),
        ],
        dest,
    )


def thumbnail_for(project: ProjectDoc, cache_dir: Path) -> Path | None:
    """Return a cached thumbnail JPEG for `project`, extracting it if needed.

    Lazy + cached: a project whose thumbnail already exists in `cache_dir`
    never touches ffmpeg again. Tries `master_output.mp4` first, then the
    first image in `images/`; returns `None` (never raises) if neither
    source is usable.
    """
    cache_dir = Path(cache_dir)
    cached = cache_dir / f"{project.id}.jpg"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.warning("could not create thumbnail cache dir %s", cache_dir, exc_info=True)
        return None

    tmp = cache_dir / f".{project.id}.tmp.jpg"
    ok = False
    try:
        if project.master_output is not None and Path(project.master_output).is_file():
            ok = _extract_video_frame(Path(project.master_output), tmp)

        if not ok:
            source = _first_image_file(project.folder / "images")
            if source is not None:
                ok = _extract_from_image(source, tmp)
    except Exception:
        # Belt and suspenders around the two branches above: nothing in this
        # function may ever propagate to the HTTP route serving it.
        logger.exception("thumbnail extraction failed for project %s", project.id)
        ok = False

    if not ok:
        tmp.unlink(missing_ok=True)
        return None

    try:
        tmp.replace(cached)
    except Exception:
        logger.exception("failed to move extracted thumbnail into cache for project %s", project.id)
        tmp.unlink(missing_ok=True)
        return None

    return cached
