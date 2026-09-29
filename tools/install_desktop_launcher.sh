#!/usr/bin/env bash
# Installs the "AVG Studio - Build / Launch" entry into the standard
# freedesktop.org application directory (~/.local/share/applications/) so
# it shows up in an application launcher (fuzzel, wofi, tofi, rofi, ...) -
# not just as a file you can double-click in a file manager.
#
# tools/AVG-Studio-Build.desktop (beside this script) finds
# build_flatpak.ps1 via `%k`, the field code for "this .desktop file's own
# path". That stops being trustworthy once the file is symlinked into
# ~/.local/share/applications/ - some launchers resolve `%k` to the
# symlink's own location, not the real file's directory. So this writes a
# SEPARATE copy with this clone's absolute path baked in - no `%k`, no
# dirname, nothing that can point at the wrong place.
#
# This installer is deliberately the one bash script left in tools/: it is
# the bootstrap that makes the PowerShell build tool clickable, and it has
# no dependencies beyond bash itself. Safe to re-run any time (e.g. after
# moving the clone).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$REPO_ROOT/tools/build_flatpak.ps1"
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DEST_FILE="$DEST_DIR/io.github.Hocine123321.AVGStudio.BuildLaunch.desktop"

if [ ! -f "$SCRIPT_PATH" ]; then
  echo "error: $SCRIPT_PATH not found - is this script still in tools/ next to build_flatpak.ps1?" >&2
  exit 1
fi

# Desktop Entry Exec quoting: inside a double-quoted token, \ ` $ and "
# must each be backslash-escaped (Exec is not run through a shell).
# Escaped programmatically because hand-counting backslashes is exactly
# the kind of thing that goes subtly wrong.
desktop_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\$/\\\$}"
  s="${s//\`/\\\`}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

MISSING_MSG="pwsh (PowerShell 7+) is not installed - build_flatpak.ps1 needs it. On CachyOS/Arch: yay -S powershell-bin"
INNER="if command -v pwsh >/dev/null 2>&1; then pwsh -NoProfile -File \"$SCRIPT_PATH\"; else echo \"$MISSING_MSG\"; fi; echo; read -n 1 -s -r -p \"Press any key to close this window...\""
EXEC_LINE="bash -c \"$(desktop_escape "$INNER")\""

mkdir -p "$DEST_DIR"
cat > "$DEST_FILE" << EOF
[Desktop Entry]
Type=Application
Name=AVG Studio — Build / Launch
Comment=Interactive menu: launch AVG Studio from source, or build the Flatpak package
Exec=$EXEC_LINE
Icon=utilities-terminal
Terminal=true
Categories=Development;
StartupNotify=true
EOF

echo "Installed: $DEST_FILE"
echo "Runs:      pwsh -File $SCRIPT_PATH"
command -v pwsh >/dev/null 2>&1 || echo "note: pwsh is not installed yet - on CachyOS/Arch: yay -S powershell-bin"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DEST_DIR" 2>/dev/null || true
fi
echo "Done - it should now be searchable in your application launcher."
