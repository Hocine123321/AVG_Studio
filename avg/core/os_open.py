"""Reveal a path in the desktop's file manager - a best-effort UI
convenience, never something a caller should treat as having side effects
worth checking.

Both call sites this exists for (`avg.export.capcut.export_take`'s reveal
after a CapCut export, `avg.api.routes_settings.open_logs_folder`'s Logs
button) used to each carry their own identical `os.startfile` +
pytest-skip + swallow-failures block on the Windows build (still there -
see `docs/linux-port/WINDOWS_VS_LINUX.md`). Consolidated here on this
fork because both call sites need the exact same `xdg-open` shape, and a
second copy of it is exactly what this repo's own file-consolidation plan
(2026-07-28, `docs/superpowers/plans/`) warns against repeating.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from pathlib import Path


def reveal_in_file_manager(path: Path) -> None:
    """Best-effort: open `path` (a folder) in the OS file manager via
    `xdg-open` - the one cross-desktop-environment launcher every Linux
    desktop (GNOME, KDE Plasma, ...) ships. Inside a Flatpak sandbox it
    forwards to the OpenURI portal automatically - no extra manifest
    permission needed beyond what every Flatpak app already gets.

    A no-op under pytest (`PYTEST_CURRENT_TEST` in the environment) - a
    test run must never pop a real window - and swallows every failure:
    a headless box, a missing `xdg-open`, a sandbox without the portal
    wired up. Nothing here is worth a caller checking; the folder still
    exists and its path is still whatever the caller already had,
    regardless of whether this actually managed to show it.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    with suppress(Exception):
        # Popen, not run(): this must not block on the file manager's own
        # lifetime, matching os.startfile's fire-and-forget behaviour on
        # the Windows build.
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
