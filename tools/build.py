"""Build the real AVG Studio installer end to end (Task 11).

    python tools/build.py pyinstaller   # freeze avg/shell.py -> dist/AVG/ (onedir)
    python tools/build.py installer     # wrap dist/AVG/ -> dist/AVG-Setup.exe
    python tools/build.py all           # both, in order

Must be run from the build virtualenv described in
docs/decisions/2026-07-24-packaging.md (pyinstaller + the media deps), not
the project's dev .venv - this script does not create or activate that
environment, it only shells out to whichever interpreter invoked it.

Preconditions this script does NOT do for you (it checks and fails loudly
instead of guessing):
  - `web/dist` must exist and be current - run `npm run build` in web/ first.
  - `vendor/ffmpeg/ffmpeg.exe` must exist - run `python tools/fetch_ffmpeg.py`
    first (vendor/ is gitignored, so a fresh checkout never has it).
  - Inno Setup 6 must be installed. This machine has it at
    C:\\Users\\HO\\AppData\\Local\\Programs\\Inno Setup 6\\ISCC.exe (a
    per-user install, so it is not on PATH); override with --iscc if it
    lives elsewhere.

The old Task 1 packaging spike (`--onefile`, `tools/spike_window.py`) is
superseded by this for real builds; `python tools/build.py nuitka` is left
in place only as the still-unevaluated spike comparison point noted in
docs/decisions/2026-07-24-packaging.md - it is not part of the shipped
installer.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "tools" / "avg.spec"
ISS = REPO_ROOT / "installer" / "avg.iss"
WEB_DIST = REPO_ROOT / "web" / "dist"
VENDOR_FFMPEG_EXE = REPO_ROOT / "vendor" / "ffmpeg" / "ffmpeg.exe"

DEFAULT_ISCC = Path(
    r"C:\Users\HO\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
)

# Kept only for the spike comparison referenced above - not used by `all`.
SPIKE_ENTRY = Path(__file__).with_name("spike_window.py")


def build_pyinstaller() -> None:
    """Freeze avg/shell.py per tools/avg.spec -> dist/AVG/ (onedir)."""
    if not WEB_DIST.is_dir():
        raise SystemExit(f"{WEB_DIST} is missing - run `npm run build` in web/ first")
    if not VENDOR_FFMPEG_EXE.is_file():
        raise SystemExit(
            f"{VENDOR_FFMPEG_EXE} is missing - run `python tools/fetch_ffmpeg.py` first"
        )
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)],
        check=True,
        cwd=REPO_ROOT,
    )


def build_installer(iscc: Path = DEFAULT_ISCC, release: bool = False) -> None:
    """Run Inno Setup on installer/avg.iss -> dist/AVG-Setup.exe.

    `release=False` (the default) passes /DFASTBUILD, which installer/avg.iss
    reads to switch Compression to lzma2/fast - seconds instead of minutes for
    the ~430MB onedir tree, at the cost of a bigger .exe. Only pass
    release=True for the build you're actually about to publish; that's the
    one time the slow lzma2/max compression is worth paying for.
    """
    onedir = REPO_ROOT / "dist" / "AVG" / "AVG.exe"
    if not onedir.is_file():
        raise SystemExit(f"{onedir} is missing - run `python tools/build.py pyinstaller` first")
    if not iscc.is_file():
        raise SystemExit(f"ISCC.exe not found at {iscc} - pass --iscc PATH")
    cmd = [str(iscc)]
    if not release:
        cmd.append("/DFASTBUILD")
    cmd.append(str(ISS))
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def build_nuitka(entry: Path = SPIKE_ENTRY, output_filename: str = "avg-spike-nuitka.exe") -> None:
    subprocess.run(
        [
            sys.executable, "-m", "nuitka",
            "--onefile",
            "--windows-console-mode=disable",
            "--enable-plugin=no-qt",
            f"--output-filename={output_filename}",
            str(entry),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "step",
        choices=["pyinstaller", "installer", "all", "nuitka"],
        help="which build step to run",
    )
    parser.add_argument(
        "--iscc", type=Path, default=DEFAULT_ISCC,
        help="path to ISCC.exe (only used by 'installer'/'all')",
    )
    parser.add_argument(
        "--release", action="store_true",
        help="use full lzma2/max compression (slow) instead of the default fast "
             "dev compression (only used by 'installer'/'all')",
    )
    args = parser.parse_args()

    if args.step == "pyinstaller":
        build_pyinstaller()
    elif args.step == "installer":
        build_installer(iscc=args.iscc, release=args.release)
    elif args.step == "all":
        build_pyinstaller()
        build_installer(iscc=args.iscc, release=args.release)
    elif args.step == "nuitka":
        build_nuitka()


if __name__ == "__main__":
    main()
