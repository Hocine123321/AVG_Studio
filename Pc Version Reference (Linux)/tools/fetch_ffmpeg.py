"""Reproduce `vendor/ffmpeg/` - the ffmpeg build the Flatpak ships.

`vendor/` is gitignored (packaging a build that size has no business in
git), so this script is how anyone else - a teammate, CI, this machine
after a clean checkout - gets it back before running `tools/build.py`.

Pulls BtbN's (github.com/BtbN/FFmpeg-Builds) `linux64-gpl` release: a
fully static build where `ffmpeg`/`ffprobe` are single self-contained
binaries with no `.so` files to hunt down or ship alongside them. Chosen
over the "lgpl" variant for parity with what BtbN documents as their most
complete/tested Linux build; this app's ffmpeg usage (encode/decode
common containers and codecs, burn subtitles) needs nothing GPL-only
licensing would otherwise block, so the distinction has no practical
effect here beyond which asset is more reliably static.

`ffplay` is deliberately dropped - it is not used anywhere in this app
and there is no reason to ship a 100+ MB build's third executable.

The Windows build (`main`/`development`) fetches a different asset
(`win64-lgpl-shared.zip`, shared not static, plus every DLL next to the
two executables) with its own copy of this script - see
`docs/linux-port/WINDOWS_VS_LINUX.md`.

Usage:
    python tools/fetch_ffmpeg.py [--dest vendor/ffmpeg] [--keep-archive]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

_ASSET_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"

_REQUIRED_EXES = ("ffmpeg", "ffprobe")
_EXCLUDED_EXES = ("ffplay",)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "vendor" / "ffmpeg"


def _download(url: str, dest_file: Path) -> None:
    print(f"Downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url) as response, open(dest_file, "wb") as out:
        shutil.copyfileobj(response, out)
    print(f"  -> {dest_file} ({dest_file.stat().st_size / 1_048_576:.1f} MiB)", file=sys.stderr)


def _extract_tar_bin(archive_path: Path, dest_dir: Path) -> list[str]:
    """Pull `ffmpeg`/`ffprobe` out of the release tarball's `bin/` folder.

    Every extracted file gets its executable bit set - `tarfile` preserves
    the mode bits the archive itself carries, which is normally enough,
    but this is cheap insurance against a build that didn't set them and a
    silent "Permission denied" the first time this app tries to run one.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with tarfile.open(archive_path, mode="r:xz") as tf:
        bin_members = [m for m in tf.getmembers() if m.isfile() and "/bin/" in m.name]
        if not bin_members:
            raise RuntimeError(
                "no 'bin/' folder found inside the release tarball - "
                "the archive layout may have changed"
            )
        for member in bin_members:
            name = Path(member.name).name
            if name in _EXCLUDED_EXES:
                continue
            target = dest_dir / name
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            with extracted, open(target, "wb") as out:
                shutil.copyfileobj(extracted, out)
            os.chmod(target, 0o755)
            written.append(name)
    return written


def fetch_ffmpeg(dest: Path = DEFAULT_DEST, keep_archive: bool = False) -> Path:
    with tempfile.TemporaryDirectory(prefix="avg-fetch-ffmpeg-") as tmp:
        tmp_archive = Path(tmp) / "ffmpeg-master-latest-linux64-gpl.tar.xz"
        _download(_ASSET_URL, tmp_archive)
        written = _extract_tar_bin(tmp_archive, dest)
        if keep_archive:
            kept = dest.parent / tmp_archive.name
            shutil.copy2(tmp_archive, kept)
            print(f"Kept archive at {kept}", file=sys.stderr)

    missing = [name for name in _REQUIRED_EXES if name not in written]
    if missing:
        raise RuntimeError(f"release archive did not contain: {missing}")

    print(f"Wrote {len(written)} files to {dest}:", file=sys.stderr)
    for name in sorted(written):
        print(f"  {name}", file=sys.stderr)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                         help="Destination folder (default: vendor/ffmpeg)")
    parser.add_argument("--keep-archive", action="store_true",
                         help="Keep the downloaded archive next to --dest for inspection")
    args = parser.parse_args()
    fetch_ffmpeg(dest=args.dest, keep_archive=args.keep_archive)


if __name__ == "__main__":
    main()
