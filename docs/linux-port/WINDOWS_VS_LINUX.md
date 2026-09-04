# Windows build vs. Linux port: what changed and why

This is a translation layer, not a design doc. When a feature is added to
`main`/`development` (Windows) and needs porting to `linux-port`, or vice
versa, this is where to look up "what's the equivalent thing on the other
side" before re-deriving it from scratch.

`linux-port` is a hard fork, not a cross-platform codebase: nothing in it
branches on `sys.platform`. Every file below either replaced its Windows
implementation outright (same public API, different internals) or dropped
functionality that has no Linux meaning. The Windows build's own copy of
each file is unchanged and still lives on `main`/`development`.

Update this file in the same commit as any change to the modules it
covers. A stale entry here is worse than no entry - it sends the next
port in the wrong direction.

---

## How to use this

Porting a feature **Windows → Linux**: find the Windows module below,
read what Linux does instead, and apply the *same shape* of change to
whatever new code you're porting (same fail-closed behavior, same
public function names/signatures where listed).

Porting **Linux → Windows**: same table, other direction. Everything here
that has no "not ported" note has a Windows equivalent with an identical
public API - the two are drop-in for each other at the call-site level.

---

## Module-by-module diffs

| Concern | Windows (`main`/`development`) | Linux (`linux-port`) |
|---|---|---|
| Base directories | `avg/config/settings.py` etc. read `%APPDATA%`, `%LOCALAPPDATA%`, `%USERPROFILE%\Documents`, `%USERPROFILE%\Videos` directly, inline, per call site | **New module** `avg/config/platform_paths.py`: `config_root()`, `cache_root()`, `documents_root()`, `videos_root()` - resolve `$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME`, `$XDG_DOCUMENTS_DIR`, `$XDG_VIDEOS_DIR`. A Flatpak sandbox redirects the first three to `~/.var/app/<app-id>/...` automatically; `documents_root()`/`videos_root()` need `--filesystem=xdg-documents`/`--filesystem=xdg-videos`, both granted in `packaging/flatpak/io.github.Hocine123321.AVGStudio.yml` |
| Key/secret storage | `avg/config/secrets.py`: DPAPI (`CryptProtectData`/`CryptUnprotectData` via `ctypes`, `crypt32.dll`) protects each value, current-user scope | **Same file, replaced internals.** `_linux_encryption_key()` sources a random Fernet key from the OS keyring (`keyring` package - GNOME Keyring or KWallet's Secret-Service-compat mode) instead of a DPAPI syscall; `_protect`/`_unprotect` are Fernet, not DPAPI. `get_key()`/`set_key()`/`has_key()`/`delete_key()`/`list_providers()`/`default_secrets_path()` - identical names, identical on-disk `secrets.dat` shape (JSON of provider → base64 ciphertext) |
| Total RAM detection | Two private copies, `avg/pipeline/phases/visuals.py::_total_ram_gb` and `avg/api/app.py::_total_ram_gb`, both `GlobalMemoryStatusEx` via `ctypes` | **New shared module** `avg/core/sysinfo.py::total_ram_gb()` - reads `MemTotal` from `/proc/meminfo`. Both call sites now import it (`visuals.py` aliases it back to `_total_ram_gb` so existing test monkeypatches keep working) |
| Reveal folder in file manager | Two duplicated blocks, `avg/export/capcut.py` and `avg/api/routes_settings.py`, both `os.startfile(path)` guarded by a `PYTEST_CURRENT_TEST` check | **New shared module** `avg/core/os_open.py::reveal_in_file_manager(path)` - `subprocess.Popen(["xdg-open", str(path)])`. Forwards to the OpenURI portal automatically under Flatpak; no manifest permission needed beyond what every Flatpak app gets |
| ffmpeg/ffprobe binary names | `avg/media/environment.py`: `_FFMPEG_EXE_NAME = "ffmpeg.exe"`, `_FFPROBE_EXE_NAME = "ffprobe.exe"` | Same constants, `"ffmpeg"` / `"ffprobe"` (no extension) |
| Fetching the vendored ffmpeg build | `tools/fetch_ffmpeg.py` downloads BtbN's `win64-lgpl-shared.zip` (shared build - `ffmpeg.exe`/`ffprobe.exe` plus every DLL, including `avdevice-*.dll`, from the release's `bin/` folder) | **Same script name, rewritten.** Downloads BtbN's `linux64-gpl.tar.xz` (fully **static** build - `ffmpeg`/`ffprobe` are single self-contained binaries, nothing else to extract). Verified against the real release and smoke-tested (`ffmpeg -version`/`ffprobe -version` both report full codec/filter support including libx264, libx265, libass) |
| Thumbnail cache dir | `avg/media/thumbnails.py::default_cache_dir()` → `%LOCALAPPDATA%/AVG Studio/thumbs` | → `cache_root()` (i.e. `$XDG_CACHE_HOME/AVG Studio/thumbs`) |
| Advertised-model-list cache | `avg/providers/models.py::default_cache_path()` → `%APPDATA%/AVG Studio/model_cache.json` (historically under roaming config, not local cache, on Windows) | → `cache_root()` (i.e. `$XDG_CACHE_HOME/AVG Studio/model_cache.json`) - deliberately **not** a literal mirror of the Windows path; this file is a rebuildable cache, and XDG cache is where a cache belongs |
| Proven-working-models cache | `avg/config/user_assets.py::default_working_models_path()` → `%APPDATA%/AVG Studio/working_models.json` | → `cache_root() / "AVG Studio" / "working_models.json"` - same reasoning as above |
| `UserAssets` root | `avg/config/user_assets.py::user_assets_root()` → `%USERPROFILE%\Documents\AVG Studio\UserAssets`, migrates from `%APPDATA%\AVG Studio` on first call if found | → `documents_root() / "AVG Studio" / "UserAssets"`, migrates from `config_root() / "AVG Studio"` on first call if found. Same `migrate_legacy_files()` function, untouched |
| Project-library fallback | `avg/shell.py::resolve_output_root()` step 4 → `%USERPROFILE%\Videos\AVG Studio` | → `videos_root() / "AVG Studio"` |
| Font-family stem extraction | `avg/pipeline/phases/_common.py::resolve_font_family_value()` used `Path(font).stem`, which only treats `\` as a separator on Windows | Manual separator-agnostic split (`font.replace("\\", "/").rsplit("/", 1)[-1]`, then strip the extension) - **kept on both branches**, not Linux-only: a channel document migrated or copied from a Windows install can still contain a Windows-style font path, so both builds need to parse it correctly. This is data-robustness, not platform code |
| GUI window/rendering backend | `pywebview` with its default Windows backend (Edge WebView2) | `pywebview` with `gui="qt"` passed explicitly in `avg/shell.py::main()`, backed by `PySide6`+`PySide6-Addons` (pip-installable, self-contained - bundles their own Qt6 + QtWebEngine Chromium, no system webkit2gtk/GTK dependency). Chosen over GTK+WebKit2GTK: PySide6 is fully pip-installable with no OS-level GObject-introspection typelib dependency, which keeps the Flatpak build a plain `pip install` rather than needing `org.gnome.Platform`'s system WebKitGTK; Qt also matches the target desktop (KDE Plasma) better for window chrome/theming. Verified end-to-end on this branch: `pip install pyside6 qtpy`, then `webview.create_window()` + `webview.start(gui="qt")` under `QT_QPA_PLATFORM=offscreen` actually creates a window, runs its event loop, and exits cleanly |
| Chromium's internal sandbox vs. Flatpak's own | N/A - no nested sandboxing on Windows | `--env=QTWEBENGINE_DISABLE_SANDBOX=1` in the Flatpak manifest's `finish-args`. Confirmed necessary by direct reproduction on this branch: without it, `webview.start(gui="qt")` fails with `Running as root without --no-sandbox is not supported` - Chromium's own sandbox refuses to nest inside another sandbox (bwrap). Flatpak's outer sandbox is already the real containment boundary for an app that only ever loads its own bundled UI from `127.0.0.1`, so disabling Chromium's inner one is not a meaningful security loss here |
| ffmpeg/ffprobe discovery at runtime | PyInstaller-frozen build: `sys.frozen`/`sys._MEIPASS` is set, so `avg/media/environment.py::bundled_ffmpeg_dir()` finds the copy PyInstaller placed next to the exe | **Not frozen at all** - this branch doesn't use PyInstaller (see "Packaging shape" below), so `bundled_ffmpeg_dir()` naturally returns `None` and `ffmpeg_exe()`/`ffprobe_exe()` already fall back to bare `"ffmpeg"`/`"ffprobe"` (PATH lookup) with zero code changes. The Flatpak manifest installs `tools/fetch_ffmpeg.py`'s output straight to `/app/bin`, which is already on `PATH` for any Flatpak app - the existing not-frozen fallback path is what actually resolves them |
| Packaging shape | `tools/build.py` + `avg.spec`: PyInstaller builds a frozen onefolder exe, then Inno Setup (`installer/avg.iss`) wraps it into an installer | **No PyInstaller.** `packaging/flatpak/io.github.Hocine123321.AVGStudio.yml` does a plain `pip3 install --prefix=/app .` of the `avg` package plus its declared dependencies, copies the pre-built `web/dist` next to wherever `avg` landed under `site-packages` (matching `avg/api/app.py`'s existing `WEB_DIST` path resolution, `parents[2]` from `avg/api/app.py` - verified end-to-end on this branch: real `pip install .` into a throwaway prefix, `WEB_DIST` resolved correctly, `create_app()` constructed successfully), and installs a two-line launcher script. `tools/build_flatpak.sh` is this branch's `Build Installer.bat`/`make_release.ps1` equivalent |
| `pyproject.toml` packaging | No `[build-system]` table - `pip install .` had never actually been run on this repo before (Windows dev flow is PYTHONPATH-based; release flow is PyInstaller, which doesn't build a wheel). Still true on `main`/`development` | Added `[build-system]` + `[tool.setuptools.packages.find] include = ["avg*"]`, needed for the Flatpak manifest's `pip install .` step to work at all - without it, setuptools refuses with "Multiple top-level packages discovered in a flat-layout" (`web`, `avg`, `assets`, `vendor`, `packaging`, `installer` all sit at the repo root). This gap exists on the Windows build too and should probably be back-ported the same way `edge-tts`'s missing dependency was, independent of anything Linux-specific |
| Missing `edge-tts` dependency | Not declared in either build's `pyproject.toml` before this branch found it (imported lazily by `avg/providers/voice/providers.py`, never added to `dependencies`) | Fixed here, should be back-ported to the Windows build's `pyproject.toml` too - this was never actually a Windows/Linux difference, just a pre-existing gap this port happened to surface |

---

## Not ported (deliberately dropped, not deferred)

- **`avg.config.secrets.read_legacy_secrets`** and `LEGACY_SECRETS_PATH`
  (`D:\AI_Video_Gen\config\secrets.json`). Read the old PowerShell app's
  DPAPI-encrypted secrets file for migration. Had **zero callers anywhere
  in the app**, even on Windows - already dead code there. On Linux it's
  doubly meaningless: no `D:\` drive to find the file on, and even if the
  bytes existed, Fernet can't decrypt a DPAPI blob regardless of path. Not
  kept as an inert stub. If a future feature needs migrating secrets from
  a *Linux* legacy install of something, this function's shape (hex/base64
  blob → `_unprotect` → per-provider dict, skip non-string/non-provider
  keys, never raise) is the pattern to reuse, not the code itself.

## Left in, but structurally inert on Linux (not removed, not a gap)

- **`avg.config.settings.LEGACY_OUTPUT_ROOT`** (`D:\AI_Video_Gen\output`)
  and its use in `avg.shell.resolve_output_root()` step 3 and the
  `/api/settings/scan` route. `.is_dir()` on a Windows drive-letter path
  simply returns `False` on Linux - harmless, and left alone because
  ripping out the legacy-import/onboarding-scan feature is a product
  decision nobody has asked for, not an OS-portability one. The one test
  that depends on the real folder existing
  (`test_scan_route_counts_real_legacy_projects`) has a `skipif` guard
  matching the pattern already used for the (now-removed) legacy-secrets
  dev-machine test.
- **`avg.media.environment.suppress_console_windows()`** already guards
  itself with `os.name != "nt"` and no-ops on Linux. Left untouched - it's
  a true no-op with no Linux equivalent to write, not maintained
  dual-platform logic.

## Removed from this branch: committed Windows build outputs

None of this was intentional - found by inspection after a report that
`tools/build_flatpak.sh` "did nothing" (still being diagnosed; unrelated
to this). While checking the tree for anything else Windows-specific
sitting around, three directories turned out to be tracked in git despite
CLAUDE.md already documenting all three as "gitignored, regenerated not
committed" - no `.gitignore` entry for any of them had ever actually
existed:

| Path | What it was | Tracked files | Size |
|---|---|---|---|
| `.venv-build/` | A full **committed Windows virtualenv** - Python interpreter, every pip dependency, compiled `.pyd` files | 7,949 | 262 MB |
| `AVG APP/` | PyInstaller onefolder output - the runnable Windows app tree | 1,295 | 433 MB |
| `dist/` | A second PyInstaller output tree | 1,294 | 433 MB |

All three removed from `linux-port`'s tree (`git rm`) and added to
`.gitignore` here for real. Untouched on `main`/`development` - this is
that pre-existing gap surfacing, not a Linux-specific problem, and fixing
it there is a separate call for whoever owns that branch.

**This does not shrink `.git` itself.** `git rm` stops these from being
in every future checkout of `linux-port` from this commit forward, but
~1.1GB of blob data for them still lives in the branch's history (shared
with `main`/`development`, since they were committed before this branch
diverged) and still gets transferred on a fresh clone. Actually reclaiming
that needs history rewriting (`git filter-repo` or equivalent) - a much
bigger, disruptive operation (rewrites every commit hash from that point
forward, needs a force-push, breaks any other clone's history) that
nobody has asked for yet and this doc isn't recommending unprompted.

## Flatpak packaging files (no Windows equivalent at all)

New in this branch, not a port of anything - Windows has an installer
(`installer/avg.iss`) and a build script instead:

- `packaging/flatpak/io.github.Hocine123321.AVGStudio.yml` - the manifest.
- `packaging/flatpak/io.github.Hocine123321.AVGStudio.desktop` - app-grid launcher entry.
- `packaging/flatpak/io.github.Hocine123321.AVGStudio.metainfo.xml` - AppStream metadata (`project_license` is a `LicenseRef-proprietary` placeholder - no LICENSE file exists in this repo on any branch yet; fix this in the same commit as whichever one adds one).
- `tools/build_flatpak.sh` - builds the frontend, then runs `flatpak-builder --install`. This branch's `Build Installer.bat`/`tools/make_release.ps1` equivalent.

## Still open / not yet done on this branch

- **Not hermetic / not Flathub-submittable.** The manifest's `pip3 install`
  and `tools/fetch_ffmpeg.py` steps both need network access during the
  build (`--share=network` in `build-options`), which Flathub disallows.
  A submittable version would need every pip dependency turned into a
  pinned `sources:` entry (`flatpak-pip-generator`) and the ffmpeg
  tarball's checksum vendored the same way, instead of fetched live. Not
  attempted - this is a real chunk of extra work with no payoff until
  Flathub distribution is actually the goal, and the manifest says so at
  the top.
- **Never built on a real machine yet.** Everything above was validated
  as far as this sandboxed dev container allows: the manifest YAML
  parses and its structure was inspected, the desktop file has its
  required keys, the AppStream XML is well-formed, the build script's
  shell syntax checks out, and the Python side was proven for real - an
  actual `pip install .` into a throwaway prefix, the `web/dist`
  placement logic, and `create_app()` constructing successfully against
  that layout. `pywebview` + `PySide6` was also proven for real under
  `QT_QPA_PLATFORM=offscreen` (window created, event loop ran, exited
  clean) - but `flatpak-builder` itself was never run, because it isn't
  installed in this container and its own runtime/SDK downloads are
  well outside what this environment can reach. A real KDE Plasma
  machine running `tools/build_flatpak.sh` is the next real test, and
  the first one likely to surface something this couldn't catch (a
  wrong permission, a missing runtime piece, GPU-specific QtWebEngine
  behavior no software-rendering fallback exercises).
- **No LICENSE file anywhere in this repo.** Blocks giving
  `metainfo.xml`'s `project_license` a real value - currently
  `LicenseRef-proprietary` as an honest placeholder, not a guess.
