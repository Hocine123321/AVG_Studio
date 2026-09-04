#!/bin/sh
# Build and locally install the Flatpak - this branch's equivalent of
# the Windows build's `Build Installer.bat` / `tools/make_release.ps1`.
#
# Checks every prerequisite explicitly before doing anything, with a
# specific error message for whichever one is missing, rather than
# letting a missing command fail deep inside flatpak-builder's own
# (much longer, much less obvious) output - or, worse, exit near-
# silently. If this script ever again produces no visible explanation
# for why it didn't build anything, that is itself a bug in this script.
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

RUNTIME_VERSION="6.9"

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

fail() {
  echo "error: $1" >&2
  exit 1
}

echo "==> checking prerequisites"

command -v flatpak >/dev/null 2>&1 || fail "flatpak is not installed. On CachyOS/Arch: sudo pacman -S flatpak"
command -v flatpak-builder >/dev/null 2>&1 || fail "flatpak-builder is not installed. On CachyOS/Arch: sudo pacman -S flatpak-builder"

if ! flatpak info "org.kde.Platform//${RUNTIME_VERSION}" >/dev/null 2>&1; then
  fail "org.kde.Platform//${RUNTIME_VERSION} is not installed. Run:
  flatpak install flathub org.kde.Platform//${RUNTIME_VERSION} org.kde.Sdk//${RUNTIME_VERSION}"
fi
if ! flatpak info "org.kde.Sdk//${RUNTIME_VERSION}" >/dev/null 2>&1; then
  fail "org.kde.Sdk//${RUNTIME_VERSION} is not installed. Run:
  flatpak install flathub org.kde.Platform//${RUNTIME_VERSION} org.kde.Sdk//${RUNTIME_VERSION}"
fi

if [ "$SKIP_WEB_BUILD" -eq 0 ]; then
  command -v npm >/dev/null 2>&1 || fail "npm is not installed (needed to build web/). On CachyOS/Arch: sudo pacman -S npm, or pass --skip-web-build if web/dist already exists."
fi

echo "==> prerequisites OK: flatpak $(flatpak --version), flatpak-builder present, org.kde.Platform/Sdk//${RUNTIME_VERSION} installed"

if [ "$SKIP_WEB_BUILD" -eq 0 ]; then
  if [ ! -d web/node_modules ]; then
    echo "==> web/node_modules missing, running npm install"
    (cd web && npm install)
  fi
  echo "==> npm run build"
  (cd web && npm run build)
  [ -f web/dist/index.html ] || fail "npm run build finished but web/dist/index.html is missing - the build did not actually produce output."
else
  if [ ! -f web/dist/index.html ]; then
    echo "web/dist/index.html does not exist and --skip-web-build was passed - nothing to build from." >&2
    exit 1
  fi
  echo "==> --skip-web-build: reusing existing web/dist"
fi

echo "==> flatpak-builder (this can take several minutes the first time - PySide6/QtWebEngine alone is a large download)"
flatpak-builder \
  --user \
  --install \
  --force-clean \
  build-dir \
  packaging/flatpak/io.github.Hocine123321.AVGStudio.yml

echo
echo "Build finished and installed. This script does NOT launch the app - run it separately with:"
echo "  flatpak run io.github.Hocine123321.AVGStudio"
