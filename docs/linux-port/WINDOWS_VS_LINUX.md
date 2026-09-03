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
| Base directories | `avg/config/settings.py` etc. read `%APPDATA%`, `%LOCALAPPDATA%`, `%USERPROFILE%\Documents`, `%USERPROFILE%\Videos` directly, inline, per call site | **New module** `avg/config/platform_paths.py`: `config_root()`, `cache_root()`, `documents_root()`, `videos_root()` - resolve `$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME`, `$XDG_DOCUMENTS_DIR`, `$XDG_VIDEOS_DIR`. A Flatpak sandbox redirects the first three to `~/.var/app/<app-id>/...` automatically; `documents_root()`/`videos_root()` need `--filesystem=xdg-documents`/`--filesystem=xdg-videos` in the manifest (not yet written - see "Still open" below) |
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

## Still open / not yet done on this branch

Nothing here has Windows-side Linux-equivalent code yet - these are the
remaining Flatpak-porting tasks, not Windows/Linux behavior diffs:

- **pywebview GUI backend.** Needs GTK+WebKit2GTK (`org.gnome.Platform`
  runtime) or Qt+QtWebEngine (`org.kde.Platform` runtime). Leaning Qt/KDE
  runtime given the target desktop is KDE Plasma, not yet decided.
- **Flatpak manifest itself** (`org.<id>.AVGStudio.yml` or similar) -
  runtime/SDK choice, `--filesystem=xdg-documents`/`xdg-videos`
  permissions for `documents_root()`/`videos_root()`, `--talk-name`s if
  any turn out to be needed for the keyring backend beyond what the
  Secret portal grants for free.
- **Packaging shape for the Python app itself** inside the sandbox -
  PyInstaller-built Linux ELF bundled as a Flatpak module (reuses
  `tools/build.py`) vs. a from-source build via `flatpak-pip-generator`.
- **`tools/build.py`/`tools/make_release.ps1`/`Build Installer.bat`** -
  Windows-only build/installer scripts, not yet given a Linux/Flatpak
  equivalent (`flatpak-builder` driven, presumably a new `tools/
  make_flatpak.sh` or `tools/build.py flatpak` subcommand - not started).
