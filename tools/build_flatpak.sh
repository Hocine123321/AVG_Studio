#!/usr/bin/env bash
# AVG Studio - Flatpak build tool.
#
# Two things this does, matching the two things anyone touching this
# branch actually needs day to day:
#
#   1. Refresh  - rebuild from whatever is on disk right now and update
#      the local --user install in place. What you use after changing
#      code, to try it again without a from-scratch reinstall. Skips
#      --force-clean on purpose: flatpak-builder's own incremental
#      build-dir cache is what makes this fast on the second-and-later
#      run, and --force-clean would throw that away every time.
#   2. Build installer - a single .flatpak file someone else can install
#      without flatpak-builder, a manifest, or this repo at all. Runs a
#      real --force-clean build first (no stale incremental state in
#      something meant to be handed to someone else), exports it to a
#      local OSTree repo, then bundles that repo into one file via
#      `flatpak build-bundle`.
#
# Bash, not POSIX sh, from this rewrite on - the interactive menu uses
# `select`, a bash builtin with no clean POSIX equivalent worth hand-
# rolling. CachyOS ships bash by default; this no longer needs to run
# anywhere that doesn't.
#
# Usage:
#   tools/build_flatpak.sh                    interactive menu
#   tools/build_flatpak.sh --refresh          skip the menu, refresh
#   tools/build_flatpak.sh --build-installer  skip the menu, build installer
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
  printf '%s║           AVG Studio — Flatpak Builder           ║%s\n' "${CYAN}${BOLD}" "${RESET}"
  printf '%s╚══════════════════════════════════════════════════╝%s\n' "${CYAN}${BOLD}" "${RESET}"
  printf '\n'
}

# ---- shared steps --------------------------------------------------------

check_prerequisites() {
  step "Checking prerequisites"

  command -v flatpak >/dev/null 2>&1 || die "flatpak is not installed. On CachyOS/Arch: sudo pacman -S flatpak"
  command -v flatpak-builder >/dev/null 2>&1 || die "flatpak-builder is not installed. On CachyOS/Arch: sudo pacman -S flatpak-builder"
  ok "flatpak $(flatpak --version | awk '{print $2}'), flatpak-builder present"

  if ! flatpak remote-list | grep -q '^flathub'; then
    info "adding flathub remote"
    flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
  fi
  ok "flathub remote configured"

  # --no-related: skips GL.default/VAAPI/codec/locale extras this app
  # doesn't use (see docs/linux-port/WINDOWS_VS_LINUX.md's "Size budget"
  # - --device=dri alone does NOT stop these downloading, this flag does).
  for ref in "org.freedesktop.Platform//${RUNTIME_VERSION}" "org.freedesktop.Sdk//${RUNTIME_VERSION}"; do
    if ! flatpak info "$ref" >/dev/null 2>&1; then
      info "installing $ref (--no-related)"
      flatpak install --noninteractive --no-related flathub "$ref"
    fi
  done
  ok "org.freedesktop.Platform/Sdk//${RUNTIME_VERSION} installed"

  if [ "$SKIP_WEB_BUILD" -eq 0 ]; then
    command -v npm >/dev/null 2>&1 || die "npm is not installed (needed to build web/). On CachyOS/Arch: sudo pacman -S npm, or pass --skip-web-build if web/dist already exists."
    ok "npm present"
  fi
}

build_web() {
  step "Building the frontend"
  if [ "$SKIP_WEB_BUILD" -eq 1 ]; then
    [ -f web/dist/index.html ] || die "web/dist/index.html does not exist and --skip-web-build was passed - nothing to build from."
    info "--skip-web-build: reusing existing web/dist"
    return
  fi
  if [ ! -d web/node_modules ]; then
    info "web/node_modules missing, running npm install"
    (cd web && npm install)
  fi
  info "npm run build"
  (cd web && npm run build)
  [ -f web/dist/index.html ] || die "npm run build finished but web/dist/index.html is missing - the build did not actually produce output."
  ok "web/dist built"
}

run_refresh() {
  step "Refreshing local install (incremental build, no --force-clean)"
  flatpak-builder --user --install "$BUILD_DIR" "$MANIFEST"
  printf '\n'
  ok "Refreshed."
  printf '%sThis does NOT launch the app - run it with:%s\n' "${DIM}" "${RESET}"
  printf '    %sflatpak run %s%s\n\n' "${BOLD}" "$APP_ID" "${RESET}"
}

run_build_installer() {
  step "Building installer package (clean build, exported to a repo)"
  mkdir -p "$DIST_DIR"
  flatpak-builder --repo="$REPO_DIR" --force-clean "$BUILD_DIR" "$MANIFEST"

  step "Bundling into a single .flatpak file"
  flatpak build-bundle "$REPO_DIR" "$BUNDLE_PATH" "$APP_ID" "$BRANCH"

  local size
  size=$(du -h "$BUNDLE_PATH" | cut -f1)
  printf '\n'
  ok "Built ${BOLD}${BUNDLE_PATH}${RESET}${GREEN} (${size})"
  printf '\n%sTo install it on another machine:%s\n' "${DIM}" "${RESET}"
  printf '    flatpak install --user %s\n\n' "$BUNDLE_PATH"
  printf '%sThat machine needs flathub configured and network access the first%s\n' "${DIM}" "${RESET}"
  printf "%stime, to fetch org.freedesktop.Platform/Sdk//${RUNTIME_VERSION} if it doesn't%s\n" "${DIM}" "${RESET}"
  printf '%salready have them - this bundle does not carry the runtime itself.%s\n\n' "${DIM}" "${RESET}"
}

# ---- entry point --------------------------------------------------------

SKIP_WEB_BUILD=0
ACTION=""
for arg in "$@"; do
  case "$arg" in
    --refresh) ACTION="refresh" ;;
    --build-installer) ACTION="build-installer" ;;
    --skip-web-build) SKIP_WEB_BUILD=1 ;;
    *)
      die "unknown argument: $arg"
      ;;
  esac
done

banner

if [ -z "$ACTION" ]; then
  echo "  1) Refresh app       - rebuild from source, update the local install"
  echo "     (use this after changing code, to try it again)"
  echo "  2) Build installer   - a .flatpak file you can hand to someone else"
  echo "  3) Quit"
  echo
  PS3="Choose an option [1-3]: "
  select choice in "Refresh app" "Build installer" "Quit"; do
    case "$REPLY" in
      1) ACTION="refresh"; break ;;
      2) ACTION="build-installer"; break ;;
      3) echo "Bye."; exit 0 ;;
      *) echo "Not a valid choice - enter 1, 2, or 3." ;;
    esac
  done
fi

check_prerequisites
build_web

case "$ACTION" in
  refresh) run_refresh ;;
  build-installer) run_build_installer ;;
esac
