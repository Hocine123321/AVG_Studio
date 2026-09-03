"""Base directories every `default_*_path()` in `avg/config/` and
`avg/providers/` resolves from - the Linux fork's replacement for the
Windows build's literal `APPDATA`/`LOCALAPPDATA`/`USERPROFILE` reads.

This is a Linux-only module, not a cross-platform dispatcher: this branch
targets a Flatpak build exclusively (see `docs/linux-port/`), so there is
no `sys.platform` branch here and no Windows code path to keep alive.
The Windows build (`main`/`development`) keeps reading its three env vars
directly wherever it always did; this module is what replaced those reads
on this branch, one for one. `docs/linux-port/WINDOWS_VS_LINUX.md` is the
map between the two if a feature needs porting either direction later.

Every location resolves to the XDG Base Directory (freedesktop.org spec)
equivalent of what the Windows build used, which is also what a Flatpak
sandbox already redirects per-app for free: `$XDG_CONFIG_HOME`,
`$XDG_DATA_HOME` and `$XDG_CACHE_HOME` all point under
`~/.var/app/<app-id>/` when running sandboxed, bind-mounted there by
`bwrap` before this process ever starts - no extra portal permission or
code in this module needed to get that isolation. `$XDG_DOCUMENTS_DIR`
and `$XDG_VIDEOS_DIR` are the one exception: a Flatpak only sees the real
host `~/Documents` / `~/Videos` if its manifest requests
`--filesystem=xdg-documents` / `--filesystem=xdg-videos` (or the portal
equivalent) - see the Flatpak manifest for which of those this app asks
for and why.

Four roots, matching the four Windows locations this app used before this
branch:

- `config_root()`    roaming per-user settings.    Win `%APPDATA%`,               `$XDG_CONFIG_HOME`   (`~/.config`)
- `cache_root()`     derived, safe-to-delete data. Win `%LOCALAPPDATA%`,          `$XDG_CACHE_HOME`    (`~/.cache`)
- `documents_root()` user-owned, backed-up data.   Win `%USERPROFILE%\\Documents`, `$XDG_DOCUMENTS_DIR` (`~/Documents`)
- `videos_root()`    default project-library home. Win `%USERPROFILE%\\Videos`,    `$XDG_VIDEOS_DIR`    (`~/Videos`)

Every function falls back to a sane default rather than raising if its
env var is unset - matching every `default_*_path()` in this codebase's
existing "a missing env var must never break startup" rule.
"""

from __future__ import annotations

import os
from pathlib import Path


def config_root() -> Path:
    """Roaming per-user config/settings root."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def cache_root() -> Path:
    """Derived, safe-to-delete data root (thumbnails, advertised-model lists,
    proven-model sweeps - anything a version upgrade may discard for free)."""
    return Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"))


def documents_root() -> Path:
    """User-owned, backup-friendly document root - `UserAssets` lives here."""
    return Path(os.environ.get("XDG_DOCUMENTS_DIR") or str(Path.home() / "Documents"))


def videos_root() -> Path:
    """Where a fresh install's project library lands before onboarding has
    stored an explicit choice - see `avg.shell.resolve_output_root`."""
    return Path(os.environ.get("XDG_VIDEOS_DIR") or str(Path.home() / "Videos"))
