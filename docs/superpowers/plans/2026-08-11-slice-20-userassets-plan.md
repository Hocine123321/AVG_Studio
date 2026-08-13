# Slice 20 — UserAssets storage consolidation: Implementation Plan

Implements `docs/superpowers/specs/2026-08-11-slice-20-userassets-design.md`.
Read that first — this plan assumes its architecture and doesn't re-argue it.

Interpreter throughout: `.venv-build\Scripts\python.exe`. Run the full
suite with `.venv-build\Scripts\python.exe -m pytest tests -q` and type
check with `.venv-build\Scripts\python.exe -m mypy avg` after every task,
not only at the end — each task's own step list already includes the
narrower `pytest <file> -q` command for its own new/changed tests; the
full-suite run is what Task 7 exists for, but running it after Task 2 and
after Task 5 too (the two tasks most likely to have session-wide fallout)
is worth the two minutes rather than discovering a regression five tasks
later.

## Why the task order is what it is

Task 2 (test-harness isolation) lands **before** any real
`default_*_path()` function starts depending on `user_assets_root()`.
`tests/conftest.py`'s session fixture is the only thing standing between
this suite and the developer's real `Documents` folder once Task 4 lands;
sequencing it first means every later task is protected from the moment
its own tests start exercising the real default functions, not just from
whenever someone remembers to add the redirect.

## Files touched

| File | Change |
|---|---|
| `avg/config/user_assets.py` | **New.** `user_assets_root()`, migration, and the three path defaults that can't live in `avg/core/` (layering — see Task 5) |
| `tests/config/test_user_assets.py` | **New.** |
| `tests/conftest.py` | Extend the session fixture to also redirect `USERPROFILE` |
| `tests/test_profile_isolation.py` | Cover the new module; drop the `model_prefs` entry it replaces |
| `avg/config/settings.py` | `default_settings_path()` body; `suggest_output_root()` fallback |
| `avg/config/channels.py` | `default_channels_dir()` body |
| `avg/config/channel_defaults.py` | `default_channel_defaults_path()` body |
| `avg/config/secrets.py` | `default_secrets_path()` body |
| `avg/config/quick_settings.py` | `default_quick_settings_path()` body |
| `avg/core/model_prefs.py` | Remove `default_prefs_path()` (relocated to `user_assets.py` — see Task 5) |
| `avg/api/app.py` | `errors_path`/`model_cache_path`/`model_prefs_path` default composition + imports |
| `tests/config/test_settings.py` | +2 tests |
| `tests/config/test_channels.py` | +1 test |
| `tests/config/test_channel_defaults.py` | +1 test |
| `tests/config/test_secrets.py` | +1 test |
| `tests/config/test_quick_settings.py` | +1 test |

Not touched, deliberately (see spec §1): `avg/media/thumbnails.py`,
`avg/providers/models.py`.

---

## Task 1 — `user_assets_root()`

The root resolver, on its own, with no migration side effect yet — a
reviewer can approve "the root resolves correctly" independently of
"migration is correct" (Task 3).

**Files:** `avg/config/user_assets.py` (new), `tests/config/test_user_assets.py` (new)

**Interfaces produced:** `user_assets_root() -> Path`

### Step 1.1 — failing test

Create `tests/config/test_user_assets.py`:

```python
from pathlib import Path

from avg.config.user_assets import user_assets_root


def test_default_root_is_under_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"


def test_falls_back_to_home_when_userprofile_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert user_assets_root() == tmp_path / "Documents" / "AVG Studio" / "UserAssets"
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/config/test_user_assets.py -q`
— fails with `ModuleNotFoundError: No module named 'avg.config.user_assets'`.

### Step 1.2 — implement

Create `avg/config/user_assets.py`:

```python
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
```

Run the same command — both tests pass.

### Step 1.3 — commit

`feat: user_assets_root() — Slice 20 Task 1`

---

## Task 2 — Test-harness isolation for the new module

Protects every task from here on from touching the developer's real
`Documents` folder, the same way the existing fixture already protects
`%APPDATA%`/`%LOCALAPPDATA%`.

**Files:** `tests/conftest.py`, `tests/test_profile_isolation.py`

### Step 2.1 — extend the session fixture

In `tests/conftest.py`, replace:

```python
**The whole suite is pointed away from the real user profile.**
Every document this app owns resolves its own default location from
`%APPDATA%` or `%LOCALAPPDATA%` at call time - `avg.config.settings`,
`avg.config.channels`, `avg.config.channel_defaults`,
`avg.config.secrets`, `avg.core.model_prefs`, `avg.providers.models`
and `avg.media.thumbnails` all read the environment variable
themselves. `create_app`'s path overrides are how a test *should* stay
isolated, but they are opt-in, and a test that simply omits one gets
the developer's real `%APPDATA%/AVG Studio` with no complaint - it
reads their actual settings, and it can overwrite them. That has to be
impossible rather than merely discouraged, so the environment the whole
session resolves against is redirected here, once. Overriding the two
variables rather than patching seven `default_*_path()` functions is
deliberate: it covers every future document automatically, including
ones nobody has written yet.
```

with:

```python
**The whole suite is pointed away from the real user profile.**
Every document this app owns resolves its own default location from
`%APPDATA%`, `%LOCALAPPDATA%` or `%USERPROFILE%` at call time -
`avg.config.settings`, `avg.config.channels`,
`avg.config.channel_defaults`, `avg.config.secrets`,
`avg.config.user_assets`, `avg.core.model_prefs`, `avg.providers.models`
and `avg.media.thumbnails` all read one of the three environment
variables themselves. `create_app`'s path overrides are how a test
*should* stay isolated, but they are opt-in, and a test that simply
omits one gets the developer's real `%APPDATA%/AVG Studio` or
`Documents/AVG Studio` with no complaint - it reads their actual
settings, and it can overwrite them. That has to be impossible rather
than merely discouraged, so the environment the whole session resolves
against is redirected here, once. Overriding the variables rather than
patching every `default_*_path()` function individually is deliberate:
it covers every future document automatically, including ones nobody
has written yet.
```

Then replace the fixture body:

```python
@pytest.fixture(autouse=True, scope="session")
def _appdata_points_somewhere_disposable() -> Iterator[Path]:
    # Assignment, not `setdefault`: the whole point is to win over the
    # real value. Anything the suite resolves from these two variables
    # lands under a temporary directory that goes away with the session.
    with tempfile.TemporaryDirectory(prefix="avg-tests-appdata-") as tmp:
        root = Path(tmp)
        roaming = root / "Roaming"
        local = root / "Local"
        roaming.mkdir()
        local.mkdir()
        previous = {
            "APPDATA": os.environ.get("APPDATA"),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
        }
        os.environ["APPDATA"] = str(roaming)
        os.environ["LOCALAPPDATA"] = str(local)
        try:
            yield root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
```

with:

```python
@pytest.fixture(autouse=True, scope="session")
def _appdata_points_somewhere_disposable() -> Iterator[Path]:
    # Assignment, not `setdefault`: the whole point is to win over the
    # real value. Anything the suite resolves from these three variables
    # lands under a temporary directory that goes away with the session.
    with tempfile.TemporaryDirectory(prefix="avg-tests-appdata-") as tmp:
        root = Path(tmp)
        roaming = root / "Roaming"
        local = root / "Local"
        home = root / "Home"
        roaming.mkdir()
        local.mkdir()
        home.mkdir()
        previous = {
            "APPDATA": os.environ.get("APPDATA"),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
            "USERPROFILE": os.environ.get("USERPROFILE"),
        }
        os.environ["APPDATA"] = str(roaming)
        os.environ["LOCALAPPDATA"] = str(local)
        os.environ["USERPROFILE"] = str(home)
        try:
            yield root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
```

### Step 2.2 — cover the new module in the isolation tests

In `tests/test_profile_isolation.py`, change the import line:

```python
from avg.config import channel_defaults, channels, secrets, settings
```

to:

```python
from avg.config import channel_defaults, channels, secrets, settings, user_assets
```

In `_real_app_folders()`, replace:

```python
    home = Path.home()
    return [
        home / "AppData" / "Roaming" / "AVG Studio",
        home / "AppData" / "Local" / "AVG Studio",
    ]
```

with:

```python
    home = Path.home()
    return [
        home / "AppData" / "Roaming" / "AVG Studio",
        home / "AppData" / "Local" / "AVG Studio",
        home / "Documents" / "AVG Studio",
    ]
```

In `test_every_default_document_path_avoids_the_real_profile`, add one
entry to `resolved` (the `errors.json`/`model_prefs.json`/
`working_models.json` entries arrive in Task 5, once those functions
exist):

```python
    resolved = {
        "settings.json": settings.default_settings_path(),
        "channels/": channels.default_channels_dir(),
        "channel_defaults.json": channel_defaults.default_channel_defaults_path(),
        "secrets.dat": secrets.default_secrets_path(),
        "model_prefs.json": model_prefs.default_prefs_path(),
        "model_cache.json": models.default_cache_path(),
        "thumbs/": thumbnails.default_cache_dir(),
        "UserAssets/": user_assets.user_assets_root(),
    }
```

Replace `test_both_variables_are_set`:

```python
def test_both_variables_are_set() -> None:
    # Every resolver falls back to `Path.home() / "AppData" / ...` when the
    # variable is unset, which would defeat the redirect entirely, so the
    # fixture must leave both of them populated rather than deleted.
    assert os.environ.get("APPDATA")
    assert os.environ.get("LOCALAPPDATA")
```

with:

```python
def test_all_variables_are_set() -> None:
    # Every resolver falls back to `Path.home() / "AppData" / ...` (or, for
    # avg.config.user_assets, `Path.home() / "Documents" / ...`) when its
    # variable is unset, which would defeat the redirect entirely, so the
    # fixture must leave all three populated rather than deleted.
    assert os.environ.get("APPDATA")
    assert os.environ.get("LOCALAPPDATA")
    assert os.environ.get("USERPROFILE")
```

### Step 2.3 — verify

`.venv-build\Scripts\python.exe -m pytest tests/test_profile_isolation.py -q`
— all pass. Then the full suite once, since this fixture is session-wide:
`.venv-build\Scripts\python.exe -m pytest tests -q`.

### Step 2.4 — commit

`test: redirect USERPROFILE in the session isolation fixture — Slice 20 Task 2`

---

## Task 3 — Migration

The riskiest piece; isolated in its own task/tests before Task 4 makes it
load-bearing for the real default functions.

**Files:** `avg/config/user_assets.py`, `tests/config/test_user_assets.py`

**Interfaces produced:** `migrate_legacy_files(old_base: Path, new_root: Path) -> list[dict[str, str]]`

### Step 3.1 — failing tests

Append to `tests/config/test_user_assets.py`:

```python
import json
import shutil

from avg.config.user_assets import migrate_legacy_files, user_assets_root


def test_migrates_an_old_only_file(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text('{"a": 1}', encoding="utf-8")

    moved = migrate_legacy_files(old_base, new_root)

    assert moved == [{
        "from": str(old_base / "settings.json"),
        "to": str(new_root / "Config" / "settings.json"),
    }]
    assert not (old_base / "settings.json").exists()
    assert (new_root / "Config" / "settings.json").read_text(encoding="utf-8") == '{"a": 1}'


def test_nothing_old_is_a_silent_no_op(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    assert migrate_legacy_files(old_base, new_root) == []
    assert not (new_root / "migration.json").exists()


def test_both_present_leaves_old_alone_and_keeps_new(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("OLD", encoding="utf-8")
    (new_root / "Config").mkdir(parents=True)
    (new_root / "Config" / "settings.json").write_text("NEW", encoding="utf-8")

    moved = migrate_legacy_files(old_base, new_root)

    assert moved == []
    assert (old_base / "settings.json").read_text(encoding="utf-8") == "OLD"
    assert (new_root / "Config" / "settings.json").read_text(encoding="utf-8") == "NEW"


def test_moves_the_channels_directory_as_one_unit(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    channels_dir = old_base / "channels"
    channels_dir.mkdir(parents=True)
    (channels_dir / "documentary.json").write_text("{}", encoding="utf-8")

    migrate_legacy_files(old_base, new_root)

    assert not channels_dir.exists()
    new_channels = new_root / "Config" / "channels"
    assert (new_channels / "documentary.json").read_text(encoding="utf-8") == "{}"


def test_second_call_after_a_full_migration_is_a_no_op(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")

    first = migrate_legacy_files(old_base, new_root)
    second = migrate_legacy_files(old_base, new_root)

    assert len(first) == 1
    assert second == []


def test_a_failed_item_does_not_block_the_rest(tmp_path, monkeypatch):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")
    (old_base / "secrets.dat").write_text("{}", encoding="utf-8")

    real_move = shutil.move

    def flaky_move(src, dst):
        if "settings.json" in str(src):
            raise OSError("locked")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", flaky_move)

    moved = migrate_legacy_files(old_base, new_root)

    assert (old_base / "settings.json").exists()  # skipped, left alone
    assert not (old_base / "secrets.dat").exists()  # still moved
    assert {m["to"] for m in moved} == {str(new_root / "Secrets" / "secrets.dat")}


def test_manifest_records_what_moved(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")

    migrate_legacy_files(old_base, new_root)

    manifest = json.loads((new_root / "migration.json").read_text(encoding="utf-8"))
    assert len(manifest["events"]) == 1
    assert manifest["events"][0]["moved"] == [{
        "from": str(old_base / "settings.json"),
        "to": str(new_root / "Config" / "settings.json"),
    }]


def test_manifest_appends_across_two_migration_passes(tmp_path):
    old_base = tmp_path / "old"
    new_root = tmp_path / "new"
    old_base.mkdir()
    (old_base / "settings.json").write_text("{}", encoding="utf-8")
    migrate_legacy_files(old_base, new_root)

    (old_base / "secrets.dat").write_text("{}", encoding="utf-8")
    migrate_legacy_files(old_base, new_root)

    manifest = json.loads((new_root / "migration.json").read_text(encoding="utf-8"))
    assert len(manifest["events"]) == 2


def test_user_assets_root_triggers_migration_from_appdata(monkeypatch, tmp_path):
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    old_avg = appdata / "AVG Studio"
    old_avg.mkdir(parents=True)
    (old_avg / "settings.json").write_text('{"schema_version": 1}', encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))

    root = user_assets_root()

    assert root == home / "Documents" / "AVG Studio" / "UserAssets"
    assert (root / "Config" / "settings.json").read_text(encoding="utf-8") == '{"schema_version": 1}'
    assert not (old_avg / "settings.json").exists()
```

Run: fails — `migrate_legacy_files` doesn't exist yet, and the last test
finds nothing to migrate since `user_assets_root()` doesn't call it yet.

### Step 3.2 — implement

In `avg/config/user_assets.py`, change the imports:

```python
from __future__ import annotations

import os
from pathlib import Path
```

to:

```python
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
```

Replace `user_assets_root()`'s body:

```python
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
```

with:

```python
def user_assets_root() -> Path:
    """`Documents/AVG Studio/UserAssets` - the one root every default in
    this module, and every sibling `default_*_path()` across
    `avg.config`, is composed from.

    Falls back to `Path.home()` if `USERPROFILE` is somehow unset (never
    true on a real Windows session, but keeps this callable in
    stripped-down environments), matching every other `default_*_path()`
    in this codebase's treatment of `APPDATA`/`LOCALAPPDATA`. Triggers
    `migrate_legacy_files` on every call - see that function for why a
    stateless, filesystem-driven idempotency check is safe to run this
    often.
    """
    base = os.environ.get("USERPROFILE") or str(Path.home())
    root = Path(base) / "Documents" / "AVG Studio" / "UserAssets"
    old_base = Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")) / "AVG Studio"
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
```

Run: `.venv-build\Scripts\python.exe -m pytest tests/config/test_user_assets.py -q`
— all pass.

### Step 3.3 — commit

`feat: migrate_legacy_files — Slice 20 Task 3`

---

## Task 4 — Point the six sibling `default_*_path()` functions at the new root

Mechanically identical change applied to six files - a reviewer approves
or rejects this as one unit.

**Files:** `avg/config/settings.py`, `avg/config/channels.py`,
`avg/config/channel_defaults.py`, `avg/config/secrets.py`,
`avg/config/quick_settings.py`, plus their five test files (`quick_settings`
gets no dedicated test file change beyond what's listed - see 4.6).

### Step 4.1 — `avg/config/settings.py`

Add the import. Replace:

```python
from pydantic import BaseModel, Field
```

with:

```python
from pydantic import BaseModel, Field

from avg.config.user_assets import user_assets_root
```

Replace the function:

```python
def default_settings_path() -> Path:
    """`%APPDATA%/AVG Studio/settings.json`.

    Falls back to `~/AppData/Roaming` if `APPDATA` is somehow unset (never
    true on a real Windows session, but keeps this callable in stripped-down
    environments) rather than raising - mirrors
    `avg.media.thumbnails.default_cache_dir`'s treatment of `LOCALAPPDATA`.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "settings.json"
```

with:

```python
def default_settings_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/settings.json`.

    Composed from `avg.config.user_assets.user_assets_root()` - see that
    module for the fallback behaviour and the one-time migration off the
    pre-Slice-20 `%APPDATA%/AVG Studio/settings.json` this triggers on
    first call.
    """
    return user_assets_root() / "Config" / "settings.json"
```

Test — in `tests/config/test_settings.py`, change the import line:

```python
from avg.config.settings import SettingsDoc, load_settings, save_settings
```

to:

```python
from avg.config.settings import SettingsDoc, default_settings_path, load_settings, save_settings
```

Append:

```python
def test_default_settings_path_is_under_user_assets_config(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.config.settings.user_assets_root", lambda: tmp_path)
    assert default_settings_path() == tmp_path / "Config" / "settings.json"
```

### Step 4.2 — `avg/config/channels.py`

Replace:

```python
from avg.config.settings import load_settings, save_settings
```

with:

```python
from avg.config.settings import load_settings, save_settings
from avg.config.user_assets import user_assets_root
```

Replace the function:

```python
def default_channels_dir() -> Path:
    """`%APPDATA%/AVG Studio/channels/`.

    Falls back to `~/AppData/Roaming` if `APPDATA` is somehow unset,
    mirroring `avg.config.settings.default_settings_path`.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "channels"
```

with:

```python
def default_channels_dir() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/channels/`.

    Composed from `avg.config.user_assets.user_assets_root()`, matching
    `avg.config.settings.default_settings_path`.
    """
    return user_assets_root() / "Config" / "channels"
```

Test — append to `tests/config/test_channels.py`
(`default_channels_dir` is already imported there):

```python
def test_default_channels_dir_is_under_user_assets_config(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.config.channels.user_assets_root", lambda: tmp_path)
    assert default_channels_dir() == tmp_path / "Config" / "channels"
```

### Step 4.3 — `avg/config/channel_defaults.py`

Replace:

```python
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any
```

with:

```python
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from avg.config.user_assets import user_assets_root
```

Replace the function:

```python
def default_channel_defaults_path() -> Path:
    """`%APPDATA%/AVG Studio/channel_defaults.json`.

    Mirrors `avg.config.settings`'s own default; `create_app` passes an
    override so tests never touch the real profile.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "channel_defaults.json"
```

with:

```python
def default_channel_defaults_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/channel_defaults.json`.

    Mirrors `avg.config.settings`'s own default; `create_app` passes an
    override so tests never touch the real profile.
    """
    return user_assets_root() / "Config" / "channel_defaults.json"
```

Test — in `tests/config/test_channel_defaults.py`, change:

```python
from avg.config.channel_defaults import load_defaults, save_defaults
```

to:

```python
from avg.config.channel_defaults import default_channel_defaults_path, load_defaults, save_defaults
```

Append:

```python
def test_default_channel_defaults_path_is_under_user_assets_config(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.config.channel_defaults.user_assets_root", lambda: tmp_path)
    assert default_channel_defaults_path() == tmp_path / "Config" / "channel_defaults.json"
```

### Step 4.4 — `avg/config/secrets.py`

Replace:

```python
import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
```

with:

```python
import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from avg.config.user_assets import user_assets_root
```

Replace the function:

```python
def default_secrets_path() -> Path:
    """`%APPDATA%/AVG Studio/secrets.dat`.

    Falls back to `~/AppData/Roaming` if `APPDATA` is somehow unset, mirroring
    `avg.config.settings.default_settings_path`.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "secrets.dat"
```

with:

```python
def default_secrets_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Secrets/secrets.dat`.

    Composed from `avg.config.user_assets.user_assets_root()`. DPAPI ties
    the ciphertext to the Windows user account, not to the file's path -
    moving this file on the same machine, same account, never invalidates
    it, so this move is exactly as safe as every other document's.
    """
    return user_assets_root() / "Secrets" / "secrets.dat"
```

Test — `tests/config/test_secrets.py` already does `from avg.config import
secrets`. Append:

```python
def test_default_secrets_path_is_under_user_assets_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "user_assets_root", lambda: tmp_path)
    assert secrets.default_secrets_path() == tmp_path / "Secrets" / "secrets.dat"
```

### Step 4.5 — `avg/config/quick_settings.py`

Replace:

```python
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
```

with:

```python
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from avg.config.user_assets import user_assets_root
```

Replace the function:

```python
def default_quick_settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "quick_settings.json"
```

with:

```python
def default_quick_settings_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/quick_settings.json` -
    composed from `avg.config.user_assets.user_assets_root()`."""
    return user_assets_root() / "Config" / "quick_settings.json"
```

Test — in `tests/config/test_quick_settings.py`, change:

```python
from avg.config.quick_settings import STARTER_PRESET, load_quick_settings, save_quick_settings
```

to:

```python
from avg.config.quick_settings import (
    STARTER_PRESET,
    default_quick_settings_path,
    load_quick_settings,
    save_quick_settings,
)
```

Append:

```python
def test_default_quick_settings_path_is_under_user_assets_config(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.config.quick_settings.user_assets_root", lambda: tmp_path)
    assert default_quick_settings_path() == tmp_path / "Config" / "quick_settings.json"
```

### Step 4.6 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/config -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
```

All pass. The full-suite run matters here specifically: every test file
anywhere that builds a `create_app(...)` **without** passing
`settings_path`/`channels_dir`/`secrets_path` now resolves through the
new root for real, protected only by Task 2's fixture - this is the run
that would catch it if that protection had a gap.

### Step 4.7 — commit

`feat: point the six default_*_path() functions at UserAssets — Slice 20 Task 4`

---

## Task 5 — `errors.json`, `model_prefs.json`, `working_models.json`

The three documents `create_app` currently composes via
`default_settings_path().with_name(...)` instead of an owned
`default_*_path()` function. Two of those three (`errors.json`,
`model_prefs.json`) are about to start following `default_settings_path()`
into `UserAssets` whether intended or not, purely as a side effect of
Task 4 - correct for `errors.json` and `model_prefs.json`, wrong for
`working_models.json` (a cache), which needs to be explicitly decoupled
before that happens in practice. `avg.core.model_prefs.default_prefs_path`
gets removed here too: it was already the same layering problem this
task exists to avoid, just latent, since `create_app` never actually
called it (confirmed - its only caller anywhere in the repo is the
isolation test this task also updates).

**Files:** `avg/config/user_assets.py`, `avg/core/model_prefs.py`,
`avg/api/app.py`, `tests/config/test_user_assets.py`,
`tests/test_profile_isolation.py`, `tests/conftest.py`

### Step 5.1 — failing tests

Append to `tests/config/test_user_assets.py`:

```python
import avg.config.user_assets as user_assets_module


def test_default_errors_path_is_under_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(user_assets_module, "user_assets_root", lambda: tmp_path)
    assert user_assets_module.default_errors_path() == tmp_path / "Logs" / "errors.json"


def test_default_model_prefs_path_is_under_config(monkeypatch, tmp_path):
    monkeypatch.setattr(user_assets_module, "user_assets_root", lambda: tmp_path)
    assert user_assets_module.default_model_prefs_path() == tmp_path / "Config" / "model_prefs.json"


def test_default_working_models_path_stays_at_appdata_not_userassets(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "somewhere_else"))

    path = user_assets_module.default_working_models_path()

    assert path == appdata / "AVG Studio" / "working_models.json"
    assert "UserAssets" not in path.parts
```

Run: fails — none of the three functions exist yet.

### Step 5.2 — implement: `avg/config/user_assets.py`

Append (after `_record_migration`):

```python
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
    """`%APPDATA%/AVG Studio/working_models.json` - deliberately NOT under
    `UserAssets`. `avg.core.model_cache.ModelCache`'s document: which
    models a probe has actually proven working, replaced wholesale on
    every sweep. A cache, not user data - see this module's docstring for
    why caches stay where they were.

    Independent of `default_settings_path` on purpose: before this slice
    both were composed from the same `%APPDATA%/AVG Studio` base by
    coincidence (`create_app` used `default_settings_path().with_name(
    "working_models.json")`), and that coincidence would have silently
    pulled this cache into `UserAssets` the moment `default_settings_path`
    moved, had this stayed a shortcut off it instead of its own function.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "working_models.json"
```

### Step 5.3 — implement: remove the now-superseded function

In `avg/core/model_prefs.py`, replace:

```python
def default_prefs_path() -> Path:
    """`%APPDATA%/AVG Studio/model_prefs.json`.

    Mirrors `avg.core.model_cache`'s and `avg.config.settings`'s own
    defaults; `create_app` passes an override so tests never touch the
    real profile.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "model_prefs.json"


class ModelPrefs:
```

with:

```python
class ModelPrefs:
```

### Step 5.4 — implement: wire `avg/api/app.py` to the new functions

Replace:

```python
from avg.config.channel_defaults import default_channel_defaults_path, load_defaults
from avg.config.quick_settings import default_quick_settings_path
from avg.config.settings import SettingsDoc, default_settings_path, load_settings, save_settings, suggest_output_root
from avg.config.settings import load_render_settings
```

with:

```python
from avg.config.channel_defaults import default_channel_defaults_path, load_defaults
from avg.config.quick_settings import default_quick_settings_path
from avg.config.settings import SettingsDoc, default_settings_path, load_settings, save_settings, suggest_output_root
from avg.config.settings import load_render_settings
from avg.config.user_assets import default_errors_path, default_model_prefs_path, default_working_models_path
```

Replace the three composition blocks:

```python
    # Errors live beside the settings document, so a user who moves their
    # profile takes the log with them. Overridable like every other path so
    # tests never write into the real %APPDATA%.
    app.state.error_store = ErrorStore(
        Path(errors_path)
        if errors_path is not None
        else default_settings_path().with_name("errors.json")
    )
    # Which models a probe proved working. Beside the settings document for
    # the same reason the error log is, and NOT the same file as
    # `avg/providers/models.py`'s `model_cache.json`, which caches what a
    # provider *advertises* - see `avg.core.model_cache`'s docstring.
    app.state.model_cache = ModelCache(
        Path(model_cache_path)
        if model_cache_path is not None
        else default_settings_path().with_name("working_models.json")
    )
    # The Live List's user-owned data: ticks, favourites, notes. A separate
    # document from the cache above, which is replaced wholesale on every
    # sweep - see `avg.core.model_prefs`.
    app.state.model_prefs = ModelPrefs(
        Path(model_prefs_path)
        if model_prefs_path is not None
        else default_settings_path().with_name("model_prefs.json")
    )
```

with:

```python
    # Errors live under UserAssets/Logs - see avg.config.user_assets for
    # why this default lives there rather than beside ErrorStore itself
    # (avg/core/ cannot import avg/config/). Overridable like every other
    # path so tests never write into the real profile.
    app.state.error_store = ErrorStore(
        Path(errors_path) if errors_path is not None else default_errors_path()
    )
    # Which models a probe proved working. Deliberately NOT under
    # UserAssets - a rebuildable cache, not user data; stays at its
    # pre-Slice-20 %APPDATA% location - see
    # avg.config.user_assets.default_working_models_path. NOT the same
    # file as `avg/providers/models.py`'s `model_cache.json`, which caches
    # what a provider *advertises* - see `avg.core.model_cache`'s
    # docstring.
    app.state.model_cache = ModelCache(
        Path(model_cache_path) if model_cache_path is not None else default_working_models_path()
    )
    # The Live List's user-owned data: ticks, favourites, notes. Under
    # UserAssets/Config, beside settings.json - see
    # avg.config.user_assets.default_model_prefs_path.
    app.state.model_prefs = ModelPrefs(
        Path(model_prefs_path) if model_prefs_path is not None else default_model_prefs_path()
    )
```

### Step 5.5 — update the isolation test and the conftest docstring

In `tests/test_profile_isolation.py`, remove the now-unused import.
Replace:

```python
from avg.config import channel_defaults, channels, secrets, settings, user_assets
from avg.core import model_prefs
from avg.media import thumbnails
from avg.providers import models
```

with:

```python
from avg.config import channel_defaults, channels, secrets, settings, user_assets
from avg.media import thumbnails
from avg.providers import models
```

Replace the `resolved` dict:

```python
    resolved = {
        "settings.json": settings.default_settings_path(),
        "channels/": channels.default_channels_dir(),
        "channel_defaults.json": channel_defaults.default_channel_defaults_path(),
        "secrets.dat": secrets.default_secrets_path(),
        "model_prefs.json": model_prefs.default_prefs_path(),
        "model_cache.json": models.default_cache_path(),
        "thumbs/": thumbnails.default_cache_dir(),
        "UserAssets/": user_assets.user_assets_root(),
    }
```

with:

```python
    resolved = {
        "settings.json": settings.default_settings_path(),
        "channels/": channels.default_channels_dir(),
        "channel_defaults.json": channel_defaults.default_channel_defaults_path(),
        "secrets.dat": secrets.default_secrets_path(),
        "model_cache.json": models.default_cache_path(),
        "thumbs/": thumbnails.default_cache_dir(),
        "UserAssets/": user_assets.user_assets_root(),
        "errors.json": user_assets.default_errors_path(),
        "model_prefs.json": user_assets.default_model_prefs_path(),
        "working_models.json": user_assets.default_working_models_path(),
    }
```

In `tests/conftest.py`, `avg.core.model_prefs` no longer resolves its own
default - replace:

```python
`avg.config.settings`, `avg.config.channels`,
`avg.config.channel_defaults`, `avg.config.secrets`,
`avg.config.user_assets`, `avg.core.model_prefs`, `avg.providers.models`
and `avg.media.thumbnails` all read one of the three environment
variables themselves.
```

with:

```python
`avg.config.settings`, `avg.config.channels`,
`avg.config.channel_defaults`, `avg.config.secrets`,
`avg.config.user_assets`, `avg.providers.models` and
`avg.media.thumbnails` all read one of the three environment variables
themselves.
```

### Step 5.6 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/config/test_user_assets.py tests/test_profile_isolation.py tests/core/test_model_prefs.py tests/api -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
```

All pass. `tests/api` matters here specifically - it's where every
`create_app(...)` call site in the suite lives, and this task is the one
that changes what three of its default parameters actually resolve to.

### Step 5.7 — commit

`feat: errors.json/model_prefs.json/working_models.json defaults — Slice 20 Task 5`

---

## Task 6 — `suggest_output_root()`'s default fallback

**Files:** `avg/config/settings.py`, `tests/config/test_settings.py`

### Step 6.1 — failing test

Append to `tests/config/test_settings.py` (needs `suggest_output_root`
added to the import already touched in Task 4 — confirm it reads
`from avg.config.settings import SettingsDoc, default_settings_path,
load_settings, save_settings, suggest_output_root` after this step):

```python
def test_suggest_output_root_default_falls_back_to_user_assets_projects(monkeypatch, tmp_path):
    monkeypatch.setattr("avg.config.settings.LEGACY_OUTPUT_ROOT", tmp_path / "no_such_legacy_folder")
    monkeypatch.setattr("avg.config.settings.user_assets_root", lambda: tmp_path / "UserAssets")

    fallback, count, source = suggest_output_root()

    assert source == "default"
    assert fallback == tmp_path / "UserAssets" / "Projects"
    assert count == 0
```

Run: fails - `fallback` is still `Path.home() / "Videos" / "AVG Studio"`.

### Step 6.2 — implement

Replace:

```python
def suggest_output_root() -> tuple[Path, int, str]:
    """The one suggestion the welcome screen offers, plus its project count.

    Order (never silently picked - the caller always states this to the
    user for confirmation):
      1. The legacy folder, if it exists AND actually holds projects -
         `("legacy", <path>, <count>)`.
      2. Otherwise `%USERPROFILE%/Videos/AVG Studio` - `("default", <path>, <count>)`.
         Not created here; the folder is only created when the user accepts
         it (see `avg.api.app`'s `POST /api/settings`), and its count is
         whatever `import_all` finds if it happens to already exist, 0
         otherwise.
    """
    # Imported here rather than at module level: legacy_import has no
    # dependency on this module, so there's no cycle risk, but keeping the
    # import local documents that this is the one function in this file
    # that reaches outside pure settings I/O.
    from avg.config.legacy_import import import_all

    if LEGACY_OUTPUT_ROOT.is_dir():
        report = import_all(LEGACY_OUTPUT_ROOT)
        if report.imported:
            return LEGACY_OUTPUT_ROOT, len(report.imported), "legacy"

    fallback = Path.home() / "Videos" / "AVG Studio"
    count = len(import_all(fallback).imported) if fallback.exists() else 0
    return fallback, count, "default"
```

with:

```python
def suggest_output_root() -> tuple[Path, int, str]:
    """The one suggestion the welcome screen offers, plus its project count.

    Order (never silently picked - the caller always states this to the
    user for confirmation):
      1. The legacy folder, if it exists AND actually holds projects -
         `("legacy", <path>, <count>)`.
      2. Otherwise `UserAssets/Projects` (`avg.config.user_assets.
         user_assets_root()`) - `("default", <path>, <count>)`. Not
         created here; the folder is only created when the user accepts
         it (see `avg.api.app`'s `POST /api/settings`), and its count is
         whatever `import_all` finds if it happens to already exist, 0
         otherwise.
    """
    # Imported here rather than at module level: legacy_import has no
    # dependency on this module, so there's no cycle risk, but keeping the
    # import local documents that this is the one function in this file
    # that reaches outside pure settings I/O.
    from avg.config.legacy_import import import_all

    if LEGACY_OUTPUT_ROOT.is_dir():
        report = import_all(LEGACY_OUTPUT_ROOT)
        if report.imported:
            return LEGACY_OUTPUT_ROOT, len(report.imported), "legacy"

    fallback = user_assets_root() / "Projects"
    count = len(import_all(fallback).imported) if fallback.exists() else 0
    return fallback, count, "default"
```

Run: passes. Also re-run `tests/config/test_settings.py` in full - this
function shares a module with the Task 4.1 change, both now depend on the
same `user_assets_root` import.

### Step 6.3 — commit

`feat: suggest_output_root() defaults under UserAssets/Projects — Slice 20 Task 6`

---

## Task 7 — Full regression, fix fallout

**Files:** whatever the runs below turn up.

### Step 7.1 — run everything

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

The frontend commands are a sanity check, not a target of this slice -
nothing in Tasks 1-6 touches `web/`, so a failure there means something
pre-existing, not a Slice 20 regression; investigate before assuming it's
unrelated rather than after.

### Step 7.2 — fix regressions

Common candidates going in: any test elsewhere in the suite that builds
`create_app(...)` without `errors_path`/`model_cache_path`/
`model_prefs_path` overrides and asserts on the literal path rather than
just round-tripping through it; any fixture that pre-seeds a real
`%APPDATA%/AVG Studio/*.json` file expecting `create_app` to find it at
the old location. Fix the assumption, not the test's intent - matching
this plan's own instruction not to weaken coverage to make it pass.

### Step 7.3 — update `.superpowers/sdd/progress.md`

Record Slice 20 complete: the eight documents now under `UserAssets`
(seven migrated + `Projects`), the three that deliberately stay at
`%APPDATA%`/`%LOCALAPPDATA%`, and the final test/mypy/build results.

### Step 7.4 — commit

`docs: Slice 20 complete — UserAssets storage consolidation`

---

## Deferred / explicitly out of scope

- Making `UserAssets` itself user-relocatable.
- Physically moving an *existing* install's video library - only a fresh
  install's suggested default changes (Task 6).
- The App Health "Logs" button UI - separate backlog item; this slice
  only guarantees `errors.json` lives somewhere that button can point at.
- Any UI work at all - this slice is backend-only.
