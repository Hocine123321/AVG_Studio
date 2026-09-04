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

RUNTIME_VERSION="25.08"

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

if ! flatpak remote-list | grep -q '^flathub'; then
  echo "==> adding flathub remote"
  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

# Installed directly rather than just reported as missing: this exact
# failure was hit twice running this script by hand (org.kde.Platform,
# before this branch switched to org.freedesktop.Platform - see the
# manifest's header comment for why) not installed, told to run the
# install command, still not installed next run) - whatever the actual
# reason (forgotten step, the install command itself failing silently,
# something else), a script whose whole job is "make this build" should
# not depend on a second manual step succeeding invisibly in between
# two runs of it. `flatpak install` is idempotent (a no-op if already
# present) and its own output - not a canned message here - is what
# actually explains a real failure (wrong version pinned, network
# problem, disk space), so failures now surface with flatpak's real
# error instead of this script's guess at one.
#
# --no-related: verified against a real run, not assumed. Removing
# --device=dri from the manifest's finish-args (see that file) did NOT
# stop GL.default(+extra)/VAAPI.Intel/Platform.codecs-extra/Locale from
# being downloaded here - those are "related refs" flatpak installs
# alongside the base runtime BY DEFAULT on a plain `flatpak install
# org.freedesktop.Platform`, independent of what any app's finish-args
# request (confirmed: flatpak/flatpak#3004 describes exactly this
# behaviour upstream). --device=dri only controls whether the app is
# *permitted* to use GL/DRI once installed, not whether this install
# step downloads it. --no-related is the flag that actually skips them.
# Safe here specifically because --device=dri is already gone from the
# manifest - the app never asks to use hardware GL/VAAPI at runtime
# either, and software rendering was already confirmed working without
# any of these present at all.
for ref in "org.freedesktop.Platform//${RUNTIME_VERSION}" "org.freedesktop.Sdk//${RUNTIME_VERSION}"; do
  if ! flatpak info "$ref" >/dev/null 2>&1; then
    echo "==> installing $ref (--no-related: skipping GL/VAAPI/codec/locale extras this app doesn't use)"
    flatpak install --noninteractive --no-related flathub "$ref"
  fi
done

if [ "$SKIP_WEB_BUILD" -eq 0 ]; then
  command -v npm >/dev/null 2>&1 || fail "npm is not installed (needed to build web/). On CachyOS/Arch: sudo pacman -S npm, or pass --skip-web-build if web/dist already exists."
fi

echo "==> prerequisites OK: flatpak $(flatpak --version), flatpak-builder present, org.freedesktop.Platform/Sdk//${RUNTIME_VERSION} installed"

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
