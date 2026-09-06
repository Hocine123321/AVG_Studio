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
| Missing `httpx` dependency | Declared only under `[project.optional-dependencies].dev`, despite being imported directly by `avg/providers/transport.py` and every provider base class (text/images/voice) for every live HTTP call this app makes - a hard runtime requirement, not a test tool. Masked on every platform until now: the Windows dev/build venv always installs with `[dev]` extras too (see CLAUDE.md's Commands), so `httpx` was always present there by coincidence. Found the same way as `edge-tts` above - a real `pip install .` with no extras, the first one this repo has ever had run against it on any platform, surfaced by an actual `flatpak run` crashing with `No module named 'httpx'` after the krb5 fix let the app get that far | Moved to the main `dependencies` list. Same back-port recommendation as `edge-tts` - checked every other third-party import in `avg/` against `pyproject.toml` after finding this one (via an AST walk over every `.py` file, not by inspection) and found no further gaps |

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
- `tools/build_flatpak.sh` - interactive (`select`-based) menu with two
  actions, matching two different real needs rather than one script
  trying to be both: **Refresh app** (`flatpak-builder --user --install`,
  no `--force-clean` - reuses `mit-krb5`'s cached build and skips
  needless bwrap teardown, though `avg-studio`'s own module always
  rebuilds regardless, see the dev-loop entry below for why and what to
  use instead for fast iteration) and **Build installer**
  (`--force-clean` then `flatpak build-bundle` into a single
  `flatpak-dist/AVGStudio.flatpak` file - what actually corresponds to
  the Windows build's `AVG-Setup.exe`, something to hand to someone else
  who runs `flatpak install --user AVGStudio.flatpak` with no manifest,
  no flatpak-builder, no clone of this repo at all). Also takes
  `--refresh`/`--build-installer` directly to skip the menu. This
  branch's `Build Installer.bat`/`tools/make_release.ps1` equivalent -
  **not** its `tools/_dev_serve.py` equivalent, which is the next entry.
- `tools/dev_serve_linux.py` - the actual equivalent of the Windows
  build's fast dev loop (`tools/_dev_serve.py`). No packaging step at
  all - bare `uvicorn.run(create_app(...))`, opened in a regular
  browser tab. Use this while iterating on `avg/`/`web/` code;
  `tools/build_flatpak.sh` answers a different question (does the
  actual sandboxed package still work) and was never going to be fast
  for this one - see the dev-loop entry below.

## Size budget

Asked for directly, so recorded rather than left as tribal knowledge.
Real figures (Flathub listings + this branch's own downloads), not
estimates, as of this write-up:

| Item | Size | Shared/one-time, or private to this app? |
|---|---|---|
| `org.freedesktop.Platform//25.08` | 241 MiB | Shared across every Flatpak using it |
| `org.freedesktop.Sdk//25.08` | ~500-600 MiB | Build-time only - never in the shipped app |
| `GL.default`+`GL.default-extra`+`VAAPI.Intel`+`openh264` | ~350-400 MiB | Would be shared, but **not installed** - see below |
| `PySide6`+`PySide6-Addons` (pip, at build time) | ~280-300 MiB | Bundled into `/app`, private to this app |
| ffmpeg static build | ~120 MiB | Bundled into `/app`, private to this app |
| `mit-krb5` (built from source) | a few MiB | Bundled into `/app`, private to this app - required, not optional (see the real-machine-bug section below) |

Considered and rejected: switching the GUI backend to GTK+WebKit2GTK
for size. Checked against real Flathub numbers rather than assumed -
`org.gnome.Platform` pulls a comparable ~350-500 MiB of runtime weight,
plus the same GL/VAAPI/codec extension row above either way. The one
real structural difference is that WebKitGTK ships inside the GNOME
runtime (shared across GNOME-ecosystem apps) where PySide6's
QtWebEngine is bundled privately per-app - but for a single first
install the totals roughly wash out. Reversing the GUI-backend decision
again for an unclear win, onto a completely unverified path (see the
GUI-backend row above - the Qt path was smoke-tested for real; GTK
never was), isn't a good trade.

Cut instead: `--device=dri` removed from `finish-args`. **First attempt
at this was wrong about the mechanism, corrected after a real run
proved it** - removing `--device=dri` alone did not stop the GL/VAAPI/
codec row from downloading, because those are "related refs" flatpak
installs alongside the base runtime *by default* whenever
`org.freedesktop.Platform` itself is installed, independent of what any
app's finish-args request (this is a known, if surprising, upstream
behaviour - see flatpak/flatpak#3004). `--device=dri` only controls
whether the app is *permitted* to use GL/DRI once already installed,
not whether `flatpak install` downloads it in the first place. The
actual fix is `flatpak install --no-related`, now what
`tools/build_flatpak.sh` uses for both the Platform and Sdk install.
Safe specifically because `--device=dri` is *also* gone from the
manifest - the app never asks to use hardware GL/VAAPI at runtime
either, and software rendering was already confirmed working with none
of these extensions present at all.

One line to reverse (`--device=dri` back in the manifest, drop
`--no-related` from the script) if the embedded browser turns out
sluggish in practice on real hardware.

Not reduced, not attempted: PySide6/PySide6-Addons' own bundled
QtWebEngine (~280-300 MiB) and the ffmpeg static build (~120 MiB) are
both real, load-bearing weight with no smaller substitute in either
case (see their own divergence-table rows above for why each was
chosen) - the size budget's actual floor, not something left on the
table.

## Corrected after real-machine testing: runtime was over-specified

The manifest originally targeted `org.kde.Platform`/`org.kde.Sdk`, on the
reasoning that Qt + a KDE Plasma target desktop meant KDE integration
would be better. Actually building it on a real machine surfaced the
real cost of that choice: `org.kde.Sdk` is a full C++/Qt *development*
toolchain (compiler, Qt/KDE Frameworks headers, cmake) plus several
multimedia extensions (`org.freedesktop.Platform.GL.default`,
`.VAAPI.Intel`, `.openh264`) pulled in alongside it - a large first-time
download for a build whose every `build-commands` step is `pip install`
plus file copies. Nothing here compiles anything, and PySide6/
PySide6-Addons (see the GUI-backend row above) already bundle their own
private Qt6 + QtWebEngine, so nothing links against system Qt at all,
KDE or otherwise. Desktop icons, portals, and theming are unaffected by
which base runtime is chosen either way - none of that reasoning
actually held up.

Switched to `org.freedesktop.Platform`/`org.freedesktop.Sdk//25.08` - a
strict subset of what `org.kde.Platform` pulls in (KDE's runtime is
itself built on top of the freedesktop one), so nothing this app
actually uses is lost, and the base install shrinks from KDE's
runtime+SDK to freedesktop's ~241 MiB `Platform` image. `24.08` was
considered and rejected - it goes EOL 2026-09-08, days away as of this
write-up; `25.08` has security support into 2027.

## "Refresh app" is slow, and that's structural, not a bug to chase

Reported directly: repeat `tools/build_flatpak.sh` refreshes felt like a
full clean build every time, re-downloading things a previous run had
already fetched, and were nowhere near the Windows build's dev-loop
speed. Two separate things were actually going on.

**The `avg-studio` module can never be incrementally rebuilt, by
flatpak-builder's own design.** Its source is `type: dir` (`path: ../..`
- this working tree). flatpak-builder's own manifest schema documents
directory sources explicitly: *"directory sources don't currently
support caching, so they will be rebuilt each time"* - unlike `archive`
or `git` sources, there is no hash-based skip-if-unchanged for this
module, full stop. No manifest option changes that. `mit-krb5` (an
`archive` source with a pinned URL+checksum) does NOT have this
limitation and was already being cached/skipped correctly across
repeat builds before this fix - it was never part of the slowness.

**What actually was fixable, and is fixed:** two real, avoidable sources
of waste in `avg-studio`'s own rebuild, neither related to the `dir`
source limitation above:
- `pip3 install --no-cache-dir --prefix=/app .` was forcing a full
  re-download of every wheel (PySide6/PySide6-Addons alone ~280-300MB)
  on every refresh, even when `pyproject.toml`'s dependency list hadn't
  changed at all. `--no-cache-dir` removed - pip's own cache persists
  across builds the same way flatpak-builder's ccache does, so an
  unchanged dependency set now installs from cache instead of
  re-fetching from PyPI.
- The `dir` source had no `skip:` list, so every refresh was copying
  `.venv-linux` (hundreds of MB), `build-dir` (this build's own scratch
  output - a previous build's leftovers growing the *next* build's
  source tree), `flatpak-dist`, `web/node_modules`, `.git`, and every
  `__pycache__` into the sandbox, on top of the actual source. Added a
  `skip:` list excluding all of it. Doesn't touch the "must rebuild"
  limitation above; does substantially cut what gets copied and hashed
  before that unavoidable rebuild starts.

**The real fix is a different tool, not a faster manifest.** The
Windows build's own fast dev loop (`tools/_dev_serve.py`) was never
rebuilding `AVG-Setup.exe` either - it runs `uvicorn` directly against
source with zero packaging step, opened in a regular browser tab. This
branch's `Build Installer.bat` equivalent was always going to be a
full-repackage operation; comparing "Refresh app"'s speed to it was
comparing the wrong two things. **`tools/dev_serve_linux.py`** is the
actual equivalent of the Windows dev loop: same pattern (bare
`uvicorn.run(create_app(...))`, `avg.shell`'s pywebview/Qt layer never
even imported), adapted only in its `output_root` default
(`avg.shell.resolve_output_root()` instead of a hardcoded `D:\...`
path). Verified directly in this container: starts, serves a real
HTTP 200, on a source layout without any Flatpak packaging involved at
all. `tools/build_flatpak.sh` is for the question only it can answer -
does the actual sandboxed package still work (permissions, the bundled
Qt/krb5 resolution) - not for iterating on `avg/`/`web/` code.

## Real-machine bug: PySide6.QtNetwork needs libgssapi_krb5, runtime doesn't ship it

First actual `flatpak run` of a successfully built app - got through the
build, installed, launched, and immediately failed:

```
[pywebview] QT cannot be loaded
...
ImportError: libgssapi_krb5.so.2: cannot open shared object file: No such file or directory
[pywebview] GTK cannot be loaded
...
ModuleNotFoundError: No module named 'gi'
webview.errors.WebViewException: You must have either QT or GTK with Python extensions installed in order to use pywebview.
```

Two things happening at once here, both expected once read closely: (1)
pywebview honoured `gui="qt"` first, as instructed, and only fell back
to trying GTK once Qt's import itself failed - GTK failing too is by
design (`gi`/PyGObject was never installed, see the GUI-backend
decision above); (2) the *reason* Qt's import failed is unrelated to
anything this branch chose - PySide6's own official manylinux wheels
dynamically link `PySide6.QtNetwork` against `libgssapi_krb5.so.2`
(MIT Kerberos/GSSAPI), and neither `org.freedesktop.Platform` nor
`org.kde.Platform` ship that library in the *runtime* image (only in
their much larger `Sdk` images, build-time only). Nothing in this app
uses Kerberos - the dependency is baked into the wheel Qt for Python
themselves publish.

Confirmed as a known, reproducible upstream gap before treating it as
one: an flatpak/flatpak bug report (#5103) reproduces the identical
failure with a from-scratch minimal manifest (`pip3 install --prefix=
/app PySide6` against `org.freedesktop.Platform`, nothing else) - not
specific to this app or this manifest. `io.qt.qtwebengine.BaseApp`
(Flathub's own project for packaging QtWebEngine) carries a dedicated
`mit-krb5` module for exactly this reason.

Fixed by adding that same module, sourced from `io.qt.qtwebengine.
BaseApp`'s own currently-in-use module definition rather than
improvised or copied from an outdated blog post: `krb5-1.22.1` (current
MIT Kerberos stable as of this write-up, not the older `1.21.3` an
otherwise-correct two-year-old blog post about this same issue used),
URL and sha256 taken directly from a merged PR in that repo. Builds
from source (`./configure --prefix=/app --disable-static --enable-
shared && make && make install`) rather than extracting a distro
package - matches the reference exactly rather than improvising a
lighter-seeming alternative for an issue this deep in Qt's own binary
dependencies. Module order (before `avg-studio`) doesn't affect
correctness - neither module's build depends on the other's output,
`libgssapi_krb5.so.2` is only needed at *runtime* import, not at pip-
install time - it's there for readability.

Not independently re-verified by downloading and re-hashing the tarball
in this container - `kerberos.org` isn't a domain this environment can
reach. The URL+checksum are sourced from Flathub's own actively-
maintained repository for this exact purpose, which is the strongest
verification practically available here; flagging the limitation
rather than overclaiming "verified."

## Real-machine bug: pip-install site-packages resolution shadowed by CWD

Found on a real `flatpak-builder` run - the first one to get all the way
through `pip3 install --prefix=/app .` and every dependency download,
failing at the very next step: `cp: cannot copy a directory, 'web/dist',
into itself, '/run/build/avg-studio/web/dist/dist'`.

The manifest's `web/dist` placement step used to do
`SITE_PACKAGES=$(python3 -c "import avg, pathlib; print(pathlib.Path
(avg.__file__).resolve().parent.parent)")`. That step's CWD is the
source checkout itself (the module's `type: dir` source), which
contains a real `avg/` directory - and `python3 -c` prepends CWD to
`sys.path[0]` ahead of everything else, so `import avg` silently
resolved to the *source* copy instead of the one `pip install` had just
placed under `/app/lib/python3.13/site-packages/avg/`. `SITE_PACKAGES`
became the checkout root instead, and the follow-up `cp -r web/dist
"$SITE_PACKAGES/web/dist"` tried to copy `web/dist` into itself.

This exact bug had already been dodged once, not fixed, during this
branch's own container-side verification of this same build-command
(this doc's "Packaging shape" row above) - that check ran its
equivalent `import avg` test from `/tmp` specifically to avoid the CWD
problem, which proved the *logic* worked but never exercised the CWD
flatpak-builder actually uses, so the bug shipped anyway.

Fixed twice, differently, once each real occurrence was reproduced and
confirmed directly rather than assumed:

- **Build time** (the actual failure): `SITE_PACKAGES` is now found via
  a filesystem glob against what `pip install` actually wrote to disk
  (`find /app/lib -mindepth 1 -maxdepth 1 -type d -name 'python3.*'`),
  not by importing anything - sidesteps the whole class of bug rather
  than depending on any particular Python import-resolution behaviour.
- **Runtime launcher** (`python3 -m avg.shell`, same class of bug in
  principle, much lower real risk - Flatpak's own filesystem sandboxing
  means an arbitrary host CWD generally isn't even visible inside the
  sandbox unless explicitly granted, which this manifest doesn't):
  `-P` added (PEP 706 - don't prepend CWD/script-dir to `sys.path`).
  Verified directly: `python3 -c "import avg"` from a directory
  containing a real, installed `avg` package's source tree resolves to
  the wrong copy; `python3 -P -c` from the identical directory resolves
  correctly.

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
- **Currently being tested on a real machine (in progress).** Everything
  in this container was validated as far as a sandboxed dev container
  allows - manifest YAML/structure, desktop file keys, AppStream XML
  well-formedness, build script shell syntax, and the Python side for
  real (`pip install .` into a throwaway prefix, `web/dist` placement,
  `create_app()` constructing successfully, `pywebview`+`PySide6`
  actually creating a window and running its event loop under
  `QT_QPA_PLATFORM=offscreen`) - but `flatpak-builder` itself was never
  run here; this container has neither the tool nor a path to the
  runtime downloads. First real run (CachyOS/KDE Plasma) found
  `flatpak-builder` itself wasn't installed, then the runtime/SDK
  weren't - both now fixed (`build_flatpak.sh` checks and installs each
  itself) - and surfaced the `org.kde.Platform` → `org.freedesktop.Platform`
  correction above, plus the CWD-shadowing bug in the section right
  above this one. Still not confirmed: an actual completed build and a
  working launched window on real hardware - the CWD fix is unverified
  beyond this container's own reproduction of the failure; it hasn't
  yet been run through `flatpak-builder` for real.
- **No LICENSE file anywhere in this repo.** Blocks giving
  `metainfo.xml`'s `project_license` a real value - currently
  `LicenseRef-proprietary` as an honest placeholder, not a guess.
