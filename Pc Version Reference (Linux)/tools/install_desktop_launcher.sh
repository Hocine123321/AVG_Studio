#!/usr/bin/env bash
# Installs the "AVG Studio - Build / Launch" entry into the standard
# freedesktop.org application directory (~/.local/share/applications/) so
# it shows up in an application launcher (fuzzel, wofi, tofi, rofi-wayland,
# ...) - not just as a file you can double-click in a file manager.
#
# tools/AVG-Studio-Build.desktop (the file right beside this script) is
# built to be double-clicked DIRECTLY from wherever the repo was cloned:
# it finds build_flatpak.sh beside itself via `%k`, the standard field code
# for "this .desktop file's own path". That self-location trick stops
# working once the file is installed as a SYMLINK into
# ~/.local/share/applications/ - depending on the launcher/app-info
# implementation in play, `%k` can resolve to the symlink's own path
# rather than the real file it points at, which would make `dirname`
# land on ~/.local/share/applications/ instead of this repo's tools/
# directory. Rather than depend on that being handled one particular way
# everywhere, this script writes a SEPARATE copy with this repo's actual
# absolute path baked in directly - no `%k`, no dirname, nothing that
# can point at the wrong place regardless of how it was launched.
#
# Safe to re-run any time (e.g. after moving the clone) - it always
# regenerates the installed file from the current repo location.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$REPO_ROOT/tools/build_flatpak.sh"
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DEST_FILE="$DEST_DIR/io.github.Hocine123321.AVGStudio.BuildLaunch.desktop"

if [ ! -x "$SCRIPT_PATH" ]; then
  echo "error: $SCRIPT_PATH not found or not executable - is this script still sitting in tools/ next to build_flatpak.sh?" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"

# Every `"` and `$` below is doubled up on purpose - constructing a
# desktop-entry Exec value, not a normal shell string (see
# AVG-Studio-Build.desktop's own header comment for why the escaping
# rules differ from a real shell's).
cat > "$DEST_FILE" << EOF
[Desktop Entry]
Type=Application
Name=AVG Studio — Build / Launch
Comment=Interactive menu: launch AVG Studio from source, or build the Flatpak package
Exec=bash -c "\"$SCRIPT_PATH\"; echo; read -n 1 -s -r -p \"Press any key to close this window...\""
Icon=utilities-terminal
Terminal=true
Categories=Development;
StartupNotify=true
EOF

echo "Installed: $DEST_FILE"
echo "Runs:      $SCRIPT_PATH"

# Not every launcher needs this (many re-scan on demand), but it's the
# standard way to force an immediate refresh where it does matter, and a
# harmless no-op everywhere else.
if command -v update-desktop-database > /dev/null 2>&1; then
  update-desktop-database "$DEST_DIR" 2>/dev/null || true
fi

echo "Done - it should now be searchable in your application launcher."
