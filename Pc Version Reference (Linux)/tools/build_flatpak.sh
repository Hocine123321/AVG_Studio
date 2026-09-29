#!/usr/bin/env bash
# AVG Studio - build tool.
#
# Two options, matching two genuinely different things rather than one
# script trying to be both:
#
#   1. Launch from source - no packaging at all. Sets up (or reuses) a
#      local dev venv, builds web/, and runs tools/dev_serve_linux.py -
#      the app in a regular browser tab, avg.shell's pywebview/Qt layer
#      never even imported. This branch's equivalent of the Windows
#      build's fast tools/_dev_serve.py loop. Use this for iterating on
#      avg/ or web/ code - the dev venv is an editable install, so code
#      changes are picked up on the next request, no rebuild step at all.
#   2. Build flatpak package - the real thing. A --force-clean
#      flatpak-builder build, installed locally (so `flatpak run` works
#      immediately for testing) AND exported to flatpak-dist/
#      AVGStudio.flatpak (so it can be handed to someone else, who
#      installs it with no manifest, no flatpak-builder, no clone of
#      this repo at all - installable with `flatpak install --user
#      AVGStudio.flatpak`). This is what actually corresponds to the
#      Windows build's AVG-Setup.exe, and it is never going to be fast -
#      flatpak-builder's own manifest schema documents `dir` sources
#      (what this app's own module uses) as "rebuilt each time", no
#      caching possible. That's what option 1 is for.
#
# Usage:
#   tools/build_flatpak.sh                 interactive menu
#   tools/build_flatpak.sh --launch        skip the menu, launch from source
#   tools/build_flatpak.sh --build-package skip the menu, build the flatpak
#   Either non-interactive form also takes --skip-web-build (reuse an
#   existing web/dist instead of running `npm run build` again).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APP_ID="io.github.Hocine123321.AVGStudio"
MANIFEST="packaging/flatpak/${APP_ID}.yml"
RUNTIME_VERSION="25.08"
BRANCH="stable"
BUILD_DIR="build-dir"
DIST_DIR="flatpak-dist"
REPO_DIR="${DIST_DIR}/repo"
BUNDLE_PATH="${DIST_DIR}/AVGStudio.flatpak"
DEV_VENV=".venv-linux"
DEV_PORT=8756
DEV_TOKEN="devtoken"

# ---- output styling --------------------------------------------------
# Colour only when stdout is an actual terminal - piping this script's
# output anywhere (a log file, `| cat`) must not litter it with escape
# codes.
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  BLUE=$'\033[34m'; CYAN=$'\033[36m'
  RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; RESET=""
fi

info()  { printf '%s==>%s %s\n' "${CYAN}${BOLD}" "${RESET}" "$1"; }
ok()    { printf '%s  ✓%s %s\n' "${GREEN}${BOLD}" "${RESET}" "$1"; }
warn()  { printf '%s  !%s %s\n' "${YELLOW}${BOLD}" "${RESET}" "$1"; }
step()  { printf '\n%s%s%s\n' "${BLUE}${BOLD}" "$1" "${RESET}"; }
die()   { printf '%s✗ error:%s %s\n' "${RED}${BOLD}" "${RESET}" "$1" >&2; exit 1; }

banner() {
  printf '\n'
  printf '%s╔══════════════════════════════════════════════════╗%s\n' "${CYAN}${BOLD}" "${RESET}"
  printf '%s║              AVG Studio — Build Tool              ║%s\n' "${CYAN}${BOLD}" "${RESET}"
  printf '%s╚══════════════════════════════════════════════════╝%s\n' "${CYAN}${BOLD}" "${RESET}"
  printf '\n'
}

# ---- shared steps --------------------------------------------------------

build_web() {
  step "Building the frontend"
  if [ "$SKIP_WEB_BUILD" -eq 1 ]; then
    [ -f web/dist/index.html ] || die "web/dist/index.html does not exist and --skip-web-build was passed - nothing to build from."
    info "--skip-web-build: reusing existing web/dist"
    return
  fi
  command -v npm >/dev/null 2>&1 || die "npm is not installed. On CachyOS/Arch: sudo pacman -S npm, or pass --skip-web-build if web/dist already exists."
  if [ ! -d web/node_modules ]; then
    info "web/node_modules missing, running npm install"
    (cd web && npm install)
  fi
  info "npm run build"
  (cd web && npm run build)
  [ -f web/dist/index.html ] || die "npm run build finished but web/dist/index.html is missing - the build did not actually produce output."
  ok "web/dist built"
}

# ---- action 1: launch from source ----------------------------------------

setup_dev_venv() {
  step "Setting up local dev environment"
  command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
  if [ ! -d "$DEV_VENV" ]; then
    info "creating $DEV_VENV"
    python3 -m venv "$DEV_VENV"
    "$DEV_VENV/bin/pip" install -q --upgrade pip
  fi
  # -e (editable): avg/ stays a live link to this source tree, not a
  # copy - a code change is picked up on the next request with no
  # rebuild step, which is the entire point of this option existing.
  # .[dev], not bare .[gui] or .: pyproject.toml splits pywebview/
  # pyside6/qtpy into a `gui` extra that nothing outside avg/shell.py
  # needs (see that extra's own comment) - dev_serve_linux.py only ever
  # imports avg.api.app, so this venv skips ~280-300MB of PySide6/
  # PySide6-Addons it would never use. Fast once already satisfied:
  # pip's own cache means an unchanged dependency set is a no-op here,
  # not a re-download.
  info "pip install -e .[dev] (fast once already satisfied)"
  "$DEV_VENV/bin/pip" install -q -e ".[dev]"
  ok "dev venv ready at $DEV_VENV"
}

run_launch_from_source() {
  setup_dev_venv
  build_web
  step "Launching from source"
  local url="http://127.0.0.1:${DEV_PORT}/?token=${DEV_TOKEN}"
  if command -v xdg-open >/dev/null 2>&1; then
    ( sleep 1; xdg-open "$url" >/dev/null 2>&1 || true ) &
    disown
  fi
  printf '\n%s%s%s\n' "${BOLD}" "$url" "${RESET}"
  printf '%sCtrl+C to stop.%s\n\n' "${DIM}" "${RESET}"
  exec "$DEV_VENV/bin/python" tools/dev_serve_linux.py
}

# ---- action 2: build flatpak package --------------------------------------

check_flatpak_prerequisites() {
  step "Checking flatpak prerequisites"

  command -v flatpak >/dev/null 2>&1 || die "flatpak is not installed. On CachyOS/Arch: sudo pacman -S flatpak"
  command -v flatpak-builder >/dev/null 2>&1 || die "flatpak-builder is not installed. On CachyOS/Arch: sudo pacman -S flatpak-builder"
  command -v npm >/dev/null 2>&1 || die "npm is not installed. On CachyOS/Arch: sudo pacman -S npm, or pass --skip-web-build if web/dist already exists."
  ok "flatpak $(flatpak --version | awk '{print $2}'), flatpak-builder, npm present"

  if ! flatpak remote-list | grep -q '^flathub'; then
    info "adding flathub remote"
    flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
  fi
  ok "flathub remote configured"

  # --no-related: skips GL.default/VAAPI/codec/locale "related" extras
  # for THIS install specifically (the bare runtime, before the app
  # itself exists). Harmless, not a source of the size trimming this
  # branch tried to do earlier - the app's own finish-args now include
  # --device=dri (re-added after real usage showed ~60% constant CPU
  # and visible lag without GPU access - see docs/linux-port/
  # WINDOWS_VS_LINUX.md), so flatpak-builder's own --install step below
  # pulls the matching GL extension in for the app regardless of what
  # happened here.
  for ref in "org.freedesktop.Platform//${RUNTIME_VERSION}" "org.freedesktop.Sdk//${RUNTIME_VERSION}"; do
    if ! flatpak info "$ref" >/dev/null 2>&1; then
      info "installing $ref (--no-related)"
      flatpak install --noninteractive --no-related flathub "$ref"
    fi
  done
  ok "org.freedesktop.Platform/Sdk//${RUNTIME_VERSION} installed"
}

run_build_package() {
  check_flatpak_prerequisites
  build_web
  step "Building (clean build - installs locally and exports a bundle)"
  mkdir -p "$DIST_DIR"
  # --install and --repo= together, one build: installs to the local
  # --user flatpak install (so `flatpak run` works right after this
  # finishes) AND exports the same build to an OSTree repo, which
  # build-bundle below turns into a single shareable file - doing this
  # as two separate flatpak-builder runs would rebuild avg-studio's
  # `dir`-sourced module twice for no reason (see that module's own
  # `skip:` comment for why it always rebuilds regardless).
  flatpak-builder --user --install --repo="$REPO_DIR" --force-clean "$BUILD_DIR" "$MANIFEST"

  step "Bundling into a single .flatpak file"
  flatpak build-bundle "$REPO_DIR" "$BUNDLE_PATH" "$APP_ID" "$BRANCH"

  local size
  size=$(du -h "$BUNDLE_PATH" | cut -f1)
  printf '\n'
  ok "Installed locally and built ${BOLD}${BUNDLE_PATH}${RESET}${GREEN} (${size})"
  printf '\n%sRun it now:%s\n' "${DIM}" "${RESET}"
  printf '    %sflatpak run %s%s\n' "${BOLD}" "$APP_ID" "${RESET}"
  printf '\n%sOr hand %s to someone else - they install it with:%s\n' "${DIM}" "$BUNDLE_PATH" "${RESET}"
  printf '    flatpak install --user %s\n\n' "$BUNDLE_PATH"
}

# ---- entry point --------------------------------------------------------

SKIP_WEB_BUILD=0
ACTION=""
for arg in "$@"; do
  case "$arg" in
    --launch) ACTION="launch" ;;
    --build-package) ACTION="build-package" ;;
    --skip-web-build) SKIP_WEB_BUILD=1 ;;
    *)
      die "unknown argument: $arg"
      ;;
  esac
done

banner

if [ -z "$ACTION" ]; then
  echo "  1) Launch from source     - browser tab, no packaging (fast - use this to iterate)"
  echo "  2) Build flatpak package  - clean build, installs locally + a shareable .flatpak file"
  echo "  3) Quit"
  echo
  PS3="Choose an option [1-3]: "
  select choice in "Launch from source" "Build flatpak package" "Quit"; do
    case "$REPLY" in
      1) ACTION="launch"; break ;;
      2) ACTION="build-package"; break ;;
      3) echo "Bye."; exit 0 ;;
      *) echo "Not a valid choice - enter 1, 2, or 3." ;;
    esac
  done
fi

case "$ACTION" in
  launch) run_launch_from_source ;;
  build-package) run_build_package ;;
esac
