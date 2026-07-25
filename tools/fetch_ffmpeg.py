"""Reproduce `vendor/ffmpeg/` - the ffmpeg build the installer ships.

`vendor/` is gitignored (128 MB has no business in git), so this script is
how anyone else - a teammate, CI, this machine after a clean checkout -
gets it back before running `tools/build.py`.

Downloads the BtbN "shared" LGPL win64 build (shared, not static, because
the static build lacks the separate `avdevice` DLL some ffmpeg builds
split out) and keeps only what actually needs to ship:

- `ffmpeg.exe`, `ffprobe.exe`
- every DLL next to them in the release's `bin/` folder, INCLUDING
  `avdevice-*.dll` - nothing in this app captures a camera or microphone
  through it, but both `ffmpeg.exe` and `ffprobe.exe` are linked against it
  and die instantly with `0xC0000135` (DLL not found) if it's missing.
- `ffplay.exe` is deliberately dropped - it is not used anywhere in this
  app and there is no reason to ship a 128 MB build's third executable.

Usage:
    python tools/fetch_ffmpeg.py [--dest vendor/ffmpeg] [--keep-zip]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-lgpl-shared.zip"
)

REQUIRED_EXES = ("ffmpeg.exe", "ffprobe.exe")
EXCLUDED_EXES = ("ffplay.exe",)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "vendor" / "ffmpeg"


def _download(url: str, dest_zip: Path) -> None:
    print(f"Downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url) as response, open(dest_zip, "wb") as out:
        shutil.copyfileobj(response, out)
    print(f"  -> {dest_zip} ({dest_zip.stat().st_size / 1_048_576:.1f} MiB)", file=sys.stderr)


def _extract_bin(zip_path: Path, dest_dir: Path) -> list[str]:
    """Pull every file out of the release's `bin/` folder into `dest_dir`.

    Skips `EXCLUDED_EXES`. Returns the list of file names written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        bin_members = [
            info for info in zf.infolist()
            if not info.is_dir() and "/bin/" in info.filename.replace("\\", "/")
        ]
        if not bin_members:
            raise RuntimeError(
                "no 'bin/' folder found inside the release zip - "
                "the archive layout may have changed"
            )
        for info in bin_members:
            name = Path(info.filename).name
            if name in EXCLUDED_EXES:
                continue
            target = dest_dir / name
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(name)
    return written


def fetch_ffmpeg(dest: Path = DEFAULT_DEST, keep_zip: bool = False) -> Path:
    with tempfile.TemporaryDirectory(prefix="avg-fetch-ffmpeg-") as tmp:
        tmp_zip = Path(tmp) / "ffmpeg-release.zip"
        _download(RELEASE_URL, tmp_zip)
        written = _extract_bin(tmp_zip, dest)
        if keep_zip:
            kept = dest.parent / tmp_zip.name
            shutil.copy2(tmp_zip, kept)
            print(f"Kept zip at {kept}", file=sys.stderr)

    missing_exes = [name for name in REQUIRED_EXES if name not in written]
    if missing_exes:
        raise RuntimeError(f"release zip did not contain: {missing_exes}")

    has_avdevice = any(name.lower().startswith("avdevice-") for name in written)
    if not has_avdevice:
        raise RuntimeError(
            "no avdevice-*.dll found in the release - ffmpeg.exe/ffprobe.exe "
            "will die at launch with 0xC0000135 without it"
        )

    print(f"Wrote {len(written)} files to {dest}:", file=sys.stderr)
    for name in sorted(written):
        print(f"  {name}", file=sys.stderr)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                         help="Destination folder (default: vendor/ffmpeg)")
    parser.add_argument("--keep-zip", action="store_true",
                         help="Keep the downloaded zip next to --dest for inspection")
    args = parser.parse_args()
    fetch_ffmpeg(dest=args.dest, keep_zip=args.keep_zip)


if __name__ == "__main__":
    main()
