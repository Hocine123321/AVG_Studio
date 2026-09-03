"""Cross-platform base directories every `default_*_path()` in
`avg/config/` and `avg/providers/` resolves from.

Windows keeps every historical env var (`APPDATA`, `LOCALAPPDATA`,
`USERPROFILE`) exactly as before - zero behaviour change there, and every
caller that used to read one of those three directly can switch to the
matching function here with no change in what path it gets back.

Linux resolves the semantically equivalent XDG Base Directory
(freedesktop.org spec) location instead. This is also what a Flatpak
sandbox already redirects per-app for free: `$XDG_CONFIG_HOME`,
`$XDG_CACHE_HOME` and `$XDG_DATA_HOME` all point under
`~/.var/app/<app-id>/` when running sandboxed, bind-mounted there by
`bwrap` before this process ever starts - no extra portal permission or
code in this module needed to get that isolation. `$XDG_DOCUMENTS_DIR`
and `$XDG_VIDEOS_DIR` are the one exception: a Flatpak only sees the
real host `~/Documents` / `~/Videos` if its manifest requests
`--filesystem=xdg-documents` / `--filesystem=xdg-videos` (or the portal
equivalent) - see the Flatpak manifest for which of those this app asks
for and why.

Four roots, matching the four Windows locations this app already keyed
off of before this module existed:

- `config_root()`    roaming per-user settings.    Win `%APPDATA%`,               Linux `$XDG_CONFIG_HOME`   (`~/.config`)
- `cache_root()`     derived, safe-to-delete data. Win `%LOCALAPPDATA%`,          Linux `$XDG_CACHE_HOME`    (`~/.cache`)
- `documents_root()` user-owned, backed-up data.   Win `%USERPROFILE%\\Documents`, Linux `$XDG_DOCUMENTS_DIR` (`~/Documents`)
- `videos_root()`    default project-library home. Win `%USERPROFILE%\\Videos`,    Linux `$XDG_VIDEOS_DIR`    (`~/Videos`)

Every function falls back to a sane default rather than raising if its
env var is unset - matching every `default_*_path()` in this codebase's
existing "a missing env var must never break startup" rule. Callers
never branch on platform themselves; that branching lives here, once.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Computed once at import time, not per-call: the interpreter's platform
#: does not change while this process is running, and every function below
#: is called often enough (once per resolved document, on every access)
#: that re-checking `sys.platform` each time would be pure waste.
_WINDOWS = sys.platform == "win32"


def config_root() -> Path:
    """Roaming per-user config/settings root."""
    if _WINDOWS:
        return Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"))
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def cache_root() -> Path:
    """Derived, safe-to-delete data root (thumbnails, advertised-model lists,
    proven-model sweeps - anything a version upgrade may discard for free)."""
    if _WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    return Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"))


def documents_root() -> Path:
    """User-owned, backup-friendly document root - `UserAssets` lives here."""
    if _WINDOWS:
        base = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(base) / "Documents"
    return Path(os.environ.get("XDG_DOCUMENTS_DIR") or str(Path.home() / "Documents"))


def videos_root() -> Path:
    """Where a fresh install's project library lands before onboarding has
    stored an explicit choice - see `avg.shell.resolve_output_root`."""
    if _WINDOWS:
        base = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(base) / "Videos"
    return Path(os.environ.get("XDG_VIDEOS_DIR") or str(Path.home() / "Videos"))
