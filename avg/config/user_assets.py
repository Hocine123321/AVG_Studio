"""The one root every user-owned, non-cache document under AVG Studio
resolves from: `Documents\\AVG Studio\\UserAssets\\`.

Existing installs kept these documents scattered across `%APPDATA%` and
`%LOCALAPPDATA%`, mirroring the legacy PowerShell app's own layout. This
module is Slice 20's fix: one discoverable, backup-friendly root,
organized by what the data actually is (Config/Secrets/Logs/Projects),
instead of by which Windows folder happens to be reachable without
prompting for elevation. See
`docs/superpowers/specs/2026-08-11-slice-20-userassets-design.md` for the
full directory layout and the reasoning behind it.

**Why `default_errors_path`, `default_model_prefs_path` and
`default_working_models_path` live here rather than beside `ErrorStore`,
`ModelPrefs` and `ModelCache` in `avg/core/`:** this app's layering has
`avg/config/` depend on `avg/core/` and never the reverse (CLAUDE.md's
Architecture section; `avg/config/legacy_import.py` importing
`avg.core.models`/`avg.core.takes` is the existing example of the
allowed direction). A `default_*_path` that calls `user_assets_root()`
cannot live in a module `avg/core/` owns without breaking that. Each of
those three classes stays exactly as agnostic about its own path as it
already was - `__init__(self, path: Path)`, no default of its own -
`avg/api/app.py` is what composes a path and hands it to the
constructor, for every one of the documents this module concerns itself
with, and it already does that for `thumb_cache_dir` too.

**What stays OUT of `UserAssets`:** `avg.providers.models`'s
advertised-catalogue `model_cache.json`, this module's own
`default_working_models_path` (the probe-proven-models cache), and
`avg.media.thumbnails`'s thumbnail cache. All three are derived and
rebuildable - losing one on a version upgrade is a non-event, unlike a
lost channel document or a silently-orphaned settings.json. They keep
resolving from `%APPDATA%`/`%LOCALAPPDATA%` exactly as before; this
module leaves them alone.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from avg.config.platform_paths import cache_root, config_root, documents_root


def user_assets_root() -> Path:
    """`Documents/AVG Studio/UserAssets` - the one root every default in
    this module, and every sibling `default_*_path()` across
    `avg.config`, is composed from.

    `documents_root()` resolves `%USERPROFILE%\\Documents` on Windows and
    `$XDG_DOCUMENTS_DIR` (falling back to `~/Documents`) on Linux - see
    `avg.config.platform_paths` for why callers never branch on platform
    themselves. Triggers `migrate_legacy_files` on every call - see that
    function for why a stateless, filesystem-driven idempotency check is
    safe to run this often.
    """
    root = documents_root() / "AVG Studio" / "UserAssets"
    old_base = config_root() / "AVG Studio"
    migrate_legacy_files(old_base, root)
    return root


# (old %APPDATA%/AVG Studio-relative name, new UserAssets-relative path) -
# every document that existed before this slice and belongs under the new
# root. `working_models.json` and `model_cache.json` are deliberately
# absent - see the module docstring's "What stays OUT" section.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("settings.json", "Config/settings.json"),
    ("channel_defaults.json", "Config/channel_defaults.json"),
    ("quick_settings.json", "Config/quick_settings.json"),
    ("model_prefs.json", "Config/model_prefs.json"),
    ("channels", "Config/channels"),
    ("secrets.dat", "Secrets/secrets.dat"),
    ("errors.json", "Logs/errors.json"),
)


def migrate_legacy_files(old_base: Path, new_root: Path) -> list[dict[str, str]]:
    """Move each `_MIGRATIONS` entry from `old_base` into `new_root`, once.

    Idempotent by construction, not by a remembered flag: an item only
    moves when its old path exists AND its new path doesn't yet, so once
    an item has moved its old path is gone and every later call skips it
    for free - no in-memory guard to accidentally reset between calls or
    leak state across a test session. If BOTH paths exist (a second
    install, a downgrade-then-upgrade, or a previous run of this exact
    function on the same machine), the new path wins and the old one is
    left exactly as it was - never deleted, never overwritten - matching
    "existing installations must not silently lose data".

    A single item failing to move (locked file, permissions) is skipped
    rather than raising - matching every loader in this codebase, a
    partial migration must never be the thing that crashes startup, and
    the remaining items still get their chance, here and on the next
    launch.

    Returns what actually moved this call (`[]` on every call after the
    first, and on a fresh install with nothing old to find). When
    anything moves, `_record_migration` appends one manifest entry to
    `new_root/migration.json`.
    """
    moved: list[dict[str, str]] = []
    for old_name, new_rel in _MIGRATIONS:
        old_path = old_base / old_name
        new_path = new_root / new_rel
        if not old_path.exists() or new_path.exists():
            continue
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))
        except OSError:
            continue
        moved.append({"from": str(old_path), "to": str(new_path)})
    if moved:
        # Best-effort: the files themselves already moved successfully at
        # this point. A manifest-write failure (disk full, a permissions
        # quirk on the newly-created UserAssets root) must not undo that
        # or crash the caller - `user_assets_root()` is on the path of
        # every `default_*_path()` function, so an uncaught exception here
        # would take down app startup itself over losing an audit trail,
        # which contradicts this function's own "must never crash
        # startup" rule for the moves themselves.
        with suppress(Exception):
            _record_migration(new_root, moved)
    return moved


def _record_migration(new_root: Path, moved: list[dict[str, str]]) -> None:
    """Append one `{"at": ..., "moved": [...]}` event to `migration.json`.

    Read-append-write rather than overwrite: a migration that only
    partially completes on one launch (see `migrate_legacy_files`) and
    picks up the remaining items on a later one must not lose the record
    of the first pass. Atomic (temp file + `os.replace`), matching every
    other document this app writes.
    """
    manifest_path = new_root / "migration.json"
    events: list[dict[str, object]] = []
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("events"), list):
            events = existing["events"]
    except Exception:
        events = []

    events.append({"at": datetime.now(timezone.utc).isoformat(), "moved": moved})

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=manifest_path.parent, prefix=".migration-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, indent=2)
        os.replace(tmp_name, manifest_path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def default_errors_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Logs/errors.json` -
    `avg.core.error_store.ErrorStore`'s document. Lives here, not beside
    that class, because `avg/core/` cannot import `avg/config/` (see this
    module's docstring); `avg/api/app.py` is the only caller.
    """
    return user_assets_root() / "Logs" / "errors.json"


def default_model_prefs_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/model_prefs.json` -
    `avg.core.model_prefs.ModelPrefs`'s document, the user's own ticks,
    favourites and notes. Same layering reason as `default_errors_path`.
    Replaces `avg.core.model_prefs.default_prefs_path`, which this slice
    removes - that function was never actually `create_app`'s source for
    this default (it used a `.with_name()` shortcut off
    `default_settings_path()` instead) and had exactly this layering
    problem already, just unexercised.
    """
    return user_assets_root() / "Config" / "model_prefs.json"


def default_working_models_path() -> Path:
    """`%APPDATA%/AVG Studio/working_models.json` on Windows,
    `$XDG_CACHE_HOME/AVG Studio/working_models.json` on Linux - deliberately
    NOT under `UserAssets`. `avg.core.model_cache.ModelCache`'s document:
    which models a probe has actually proven working, replaced wholesale on
    every sweep. A cache, not user data - see this module's docstring for
    why caches stay where they were. Windows keeps the historical `%APPDATA%`
    (roaming config, not `%LOCALAPPDATA%`) location unchanged; Linux resolves
    it as the cache it actually is via `cache_root()` rather than mirroring
    that Windows quirk literally.

    Independent of `default_settings_path` on purpose: before this slice
    both were composed from the same `%APPDATA%/AVG Studio` base by
    coincidence (`create_app` used `default_settings_path().with_name(
    "working_models.json")`), and that coincidence would have silently
    pulled this cache into `UserAssets` the moment `default_settings_path`
    moved, had this stayed a shortcut off it instead of its own function.
    """
    return cache_root() / "AVG Studio" / "working_models.json"


def default_reusable_images_dir() -> Path:
    """`Documents/AVG Studio/UserAssets/ReusableImages` - the hand-
    maintained image recycling pool `avg.config.recycle_pool.
    read_recyclable_pool` reads (`images.json` plus the image files
    themselves). No in-app editor exists for this folder in either app
    version - see `docs/superpowers/specs/2026-09-02-slice-31-recycling-
    design.md` §1.
    """
    return user_assets_root() / "ReusableImages"
