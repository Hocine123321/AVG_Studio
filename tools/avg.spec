# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AVG Studio - Task 11.

--onedir, not --onefile: onefile re-extracts ~250 MB to a temp folder on
every launch, onedir starts immediately. Inno Setup (installer/avg.iss) is
what turns this folder into the single `AVG-Setup.exe` end users see.

Bundles three things beyond the pure-Python package:

- `--copy-metadata` for imageio/imageio_ffmpeg/moviepy/numpy/Pillow - the
  Task 1 packaging spike found the exe builds fine and dies at launch with
  "No package metadata was found for imageio" without these; moviepy
  resolves its own version through importlib.metadata at import time and
  PyInstaller does not bring `.dist-info` along unless told to.
- `web/dist` (the built React UI) as data, landing at
  `<frozen root>/web/dist` - `avg/api/app.py`'s `WEB_DIST` is computed as
  `Path(__file__).resolve().parents[2] / "web" / "dist"`, which for a
  frozen `avg/api/app.py` resolves to `<frozen root>/web/dist`, matching
  this layout without any code change.
- `vendor/ffmpeg` as data, landing at `<frozen root>/vendor/ffmpeg` -
  matches what `avg.media.environment.bundled_ffmpeg_dir()` looks for
  (`Path(sys._MEIPASS) / "vendor" / "ffmpeg"`).
- `avg/assets` (the bundled `personality.txt` / `dynamic_options.txt`
  prompt-building copy - see `avg.pipeline.personality`) as data, landing
  at `<frozen root>/avg/assets` - PyInstaller's `Analysis` only follows
  Python imports, so a non-`.py` file living inside a package directory is
  NOT picked up automatically the way `.py` modules are; it must be listed
  here explicitly, same reasoning as `web/dist` and `vendor/ffmpeg` above.
  Matches what `avg.pipeline.personality.assets_dir()` looks for
  (`Path(sys._MEIPASS) / "avg" / "assets"`).

Run from the repo root with the build venv:
    python -m PyInstaller tools/avg.spec

Set AVG_BUILD_CONSOLE=1 to get a console build for debugging a launch
failure (the packaging spike's hard lesson: never ship a --noconsole build
without having seen a console build of the same code run clean first). The
shipped build is windowed (console=False) unless that variable is set.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

REPO_ROOT = Path(SPECPATH).resolve().parent  # tools/ -> repo root
ENTRY = REPO_ROOT / "avg" / "shell.py"
WEB_DIST = REPO_ROOT / "web" / "dist"
VENDOR_FFMPEG = REPO_ROOT / "vendor" / "ffmpeg"
AVG_ASSETS = REPO_ROOT / "avg" / "assets"
ICON = REPO_ROOT / "assets" / "icon.ico"

if not WEB_DIST.is_dir():
    raise SystemExit(
        f"{WEB_DIST} is missing - run `npm run build` in web/ before building the exe"
    )
if not (VENDOR_FFMPEG / "ffmpeg.exe").is_file():
    raise SystemExit(
        f"{VENDOR_FFMPEG} is missing ffmpeg.exe - run "
        "`python tools/fetch_ffmpeg.py` before building the exe"
    )
if not (AVG_ASSETS / "personality.txt").is_file():
    raise SystemExit(
        f"{AVG_ASSETS} is missing personality.txt - it should be checked into the repo"
    )

datas = []
datas += copy_metadata("imageio")
datas += copy_metadata("imageio_ffmpeg")
datas += copy_metadata("moviepy")
datas += copy_metadata("numpy")
datas += copy_metadata("Pillow")
datas += [(str(WEB_DIST), "web/dist")]
datas += [(str(VENDOR_FFMPEG), "vendor/ffmpeg")]
datas += [(str(AVG_ASSETS), "avg/assets")]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AVG",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get("AVG_BUILD_CONSOLE") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Every Windows size is baked into this .ico by tools/make_icon.py -
    # without 16 and 32 the taskbar and title bar downscale the 256 and it
    # goes muddy. Passed as a plain path rather than guarded on existence:
    # a build with no icon is a build worth failing.
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AVG",
)
