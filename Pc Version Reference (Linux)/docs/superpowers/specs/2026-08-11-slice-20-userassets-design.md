# Slice 20 — UserAssets storage consolidation: Design

**Goal:** every piece of user-owned, non-cache application data lives under one
organized, discoverable root — `Documents\AVG Studio\UserAssets\` — instead of
scattered across `%APPDATA%`, `%LOCALAPPDATA%`, and a separately-chosen video
library folder. Existing installs migrate the small files automatically;
existing video libraries are not physically moved.

---

## 0. Why not just relocate everything into the (already relocatable) video library

The obvious-looking design — make the user's video library folder *be*
UserAssets, since the user already picks where it lives — doesn't work.
`settings.json` is where `output_root` itself is stored; the app has to find
`settings.json` *before* it knows where the video library is. Anything that
gates its own location behind a value it stores has no bootstrap path. So
config/secrets/logs need a fixed, predictable default location, independent
of wherever the user later points their video library. The video library
becomes a subfolder of the new root **by default for new installs only**;
its location remains a plain, independently-configurable pointer exactly as
today.

## 1. Directory structure

```
Documents\AVG Studio\UserAssets\
├── Projects\              new default target for output_root
├── Config\
│   ├── settings.json
│   ├── channel_defaults.json
│   ├── quick_settings.json
│   ├── model_prefs.json
│   └── channels\
│       └── <channel id>.json
├── Secrets\
│   └── secrets.dat
├── Logs\
│   └── errors.json
└── migration.json         one-time record of what moved and when
```

`model_prefs.json` folds into `Config\` rather than getting its own top-level
folder — it's a small user-preference document, same shape as the others; a
fifth top-level folder for one file works against "minimal."

**Explicitly excluded — stays where it is today:**
- `%LOCALAPPDATA%\AVG Studio\thumbs\` (thumbnail cache)
- `%APPDATA%\AVG Studio\model_cache.json` (provider's advertised catalogue)
- `%APPDATA%\AVG Studio\working_models.json` (probe-proven models)

All three are derived and rebuildable — a cache surviving a version upgrade
or getting orphaned in the old location is a non-event, unlike a lost secret
or a silently-dropped channel document. Moving them adds migration risk for
zero user-facing benefit and contradicts the brief's own instruction to
distinguish persistent data from cache.

## 2. Path resolution — one new module, not eight copies

`avg/config/settings.py`, `secrets.py`, `channels.py`, `channel_defaults.py`,
`quick_settings.py`, and `avg/core/model_prefs.py` each currently carry their
own copy of:

```python
base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
return Path(base) / "AVG Studio" / "<file>"
```

Six copies of the same fallback logic is exactly the kind of duplication
CLAUDE.md's "one X, the source of truth" pattern exists to prevent. New
module `avg/config/user_assets.py`:

```python
def user_assets_root() -> Path:
    """Documents\\AVG Studio\\UserAssets — the one root every default below
    is computed from. Overridable via AVG_USER_ASSETS_ROOT for tests that
    want to exercise the real resolution path rather than passing an
    explicit path (most won't need to — create_app's existing per-file
    overrides still work unchanged, see §4)."""
```

Each existing `default_*_path()` becomes a one-line composition —
`user_assets_root() / "Config" / "settings.json"`, etc. Function names,
signatures, and call sites are unchanged; only the body of each moves from
duplicating the `%APPDATA%` fallback to calling `user_assets_root()`.
`avg/media/thumbnails.py` and `avg/providers/models.py` (cache) are
untouched — §1 excludes them.

`errors.json` moves from `%APPDATA%` to `UserAssets\Logs\`. This is also the
answer to what a future "Logs" button (App Health → Recent Errors, tracked
separately) opens: there is no other application log file anywhere in this
codebase — only this and the per-take run log already living inside each
project folder. No new logging pipeline gets introduced by this slice.

## 3. Migration — silent, automatic, logged

Triggered lazily, on first call to `user_assets_root()` in the process, not
hard-wired into `create_app`. `avg.shell` reads `settings.json` (via
`avg.config.settings.load_settings`) *before* `create_app` is ever called,
to resolve `output_root` — so migration has to happen at first path
resolution, not at app construction, or the earliest read would hit the old
location. Idempotent (a `_migrated` sentinel or the simple presence check
below makes a second call in the same process a no-op) and safe to call from
both entry points. For each moved file/dir listed in §1: if the old
`%APPDATA%` / `%LOCALAPPDATA%` path exists AND the new path does not, move it
(`shutil.move`,
safe across drives). If both exist, the new path wins and the old one is left
alone untouched (covers a second launch after a partial/interrupted
migration, and a downgrade-then-upgrade). If neither exists, nothing happens
— a fresh install has nothing to migrate.

After migration, `migration.json` is written (atomic, same temp-file +
`os.replace` pattern every other document here uses) recording what moved,
from where, and when. This is a record on disk, not a new user-facing
surface — reusing `error_store`'s severity taxonomy (`critical` /
`needs_review` / `low`) for a routine one-time bookkeeping event would
misuse a vocabulary that means "something went wrong."

**Video library:** `output_root` in the migrated `settings.json` keeps
pointing at wherever it already pointed. Nothing under the existing library
folder is touched. Only a **fresh install's** `suggest_output_root()` default
changes, from `%USERPROFILE%\Videos\AVG Studio` to
`UserAssets\Projects` — still offered as a suggestion the user confirms on
the Welcome screen, never silently picked, exactly as today.

## 4. Test isolation

`create_app`'s existing per-file override parameters
(`settings_path`, `channels_dir`, `secrets_path`, `errors_path`,
`model_cache_path`, `model_prefs_path`, `channel_defaults_path`,
`quick_settings_path`, `thumb_cache_dir`) are unchanged — every existing test
that passes explicit paths keeps working with zero edits. Tests that want to
exercise `user_assets_root()` itself set `AVG_USER_ASSETS_ROOT` to a
`tmp_path`. Migration logic gets its own tests against a scratch directory
tree standing in for `%APPDATA%`/`%LOCALAPPDATA%`/Documents — never the real
profile.

## 5. Testing

- `user_assets_root()`: default resolution, `AVG_USER_ASSETS_ROOT` override,
  `APPDATA`/`USERPROFILE`-unset fallback.
- Each `default_*_path()`: still returns the same filename under the new
  root.
- Migration: old-only → moves; new-only → no-op; both-present → new wins,
  old left alone; neither-present → no-op; `migration.json` written and
  shaped correctly; a simulated cross-drive move (mock `shutil.move`) still
  succeeds.
- `suggest_output_root()`: fresh-install default now resolves under
  `UserAssets\Projects`; an existing `output_root` value is never rewritten
  by anything in this slice.
- Regression: full `pytest tests -q` — no existing test should need its
  path assumptions changed beyond what §4 already covers.

## 6. Out of scope for this slice

- Making `UserAssets` root itself user-relocatable (a harder feature, not
  requested).
- Touching cache locations (§1).
- The App Health "Logs" button UI itself — tracked as its own item, this
  slice only makes sure `errors.json` lives somewhere that button can
  sensibly point at.
