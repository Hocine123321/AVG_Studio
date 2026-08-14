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

import os
from pathlib import Path


def user_assets_root() -> Path:
    """`Documents/AVG Studio/UserAssets` - the one root every default in
    this module, and every sibling `default_*_path()` across
    `avg.config`, is composed from.

    Falls back to `Path.home()` if `USERPROFILE` is somehow unset (never
    true on a real Windows session, but keeps this callable in
    stripped-down environments), matching every other `default_*_path()`
    in this codebase's treatment of `APPDATA`/`LOCALAPPDATA`.
    """
    base = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(base) / "Documents" / "AVG Studio" / "UserAssets"
