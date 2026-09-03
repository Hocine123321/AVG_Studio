#!/bin/sh
# Build and locally install the Flatpak - this branch's equivalent of
# the Windows build's `Build Installer.bat` / `tools/make_release.ps1`.
#
# Two host-side prerequisites this script does NOT install for you:
#   1. flatpak + flatpak-builder (from your distro's package manager -
#      on CachyOS/Arch: `sudo pacman -S flatpak flatpak-builder`)
#   2. The KDE runtime/SDK this manifest builds against:
#      flatpak install flathub org.kde.Platform//6.9 org.kde.Sdk//6.9
#
# Usage: tools/build_flatpak.sh [--skip-web-build]
#   --skip-web-build   Reuse an existing web/dist instead of running
#                       `npm run build` again (mirrors tools/build.py's
#                       web/.build-hash skip-if-unchanged behaviour on
#                       the Windows build, but simpler: this is just an
#                       explicit opt-out flag, not a hash check, since a
#                       Flatpak rebuild is already a deliberate,
#                       infrequent action here, unlike the Windows fast
#                       dev-build loop that behaviour was written for).

set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_WEB_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --skip-web-build) SKIP_WEB_BUILD=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [ "$SKIP_WEB_BUILD" -eq 0 ]; then
  if [ ! -d web/node_modules ]; then
    echo "==> web/node_modules missing, running npm install"
    (cd web && npm install)
  fi
  echo "==> npm run build"
  (cd web && npm run build)
else
  if [ ! -d web/dist ]; then
    echo "web/dist does not exist and --skip-web-build was passed - nothing to build from." >&2
    exit 1
  fi
  echo "==> --skip-web-build: reusing existing web/dist"
fi

echo "==> flatpak-builder"
flatpak-builder \
  --user \
  --install \
  --force-clean \
  build-dir \
  packaging/flatpak/io.github.Hocine123321.AVGStudio.yml

echo
echo "Done. Run with:  flatpak run io.github.Hocine123321.AVGStudio"
