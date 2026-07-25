"""The app's own settings document.

Distinct from the legacy app's `config.json` (imported read-only via
`avg.config.settings_import`) - this is a small file AVG Studio itself owns
and writes, currently holding exactly one durable decision: where the
user's project library lives. Reading and writing only happen here; no
HTTP, no UI concerns - `avg.api.app` calls into this module and shapes the
result into routes, `avg.shell` calls into it to decide `resolve_output_root()`.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

# Task 2's channel work gave the active-channel pointer its own tiny file
# (`active_channel.json`, sibling of settings.json). Task 3 folds that onto
# this document instead - one app-wide state document, not a scatter of
# tiny files - so this is only read once, on first load after upgrading,
# to carry an existing choice forward; see `_consume_legacy_active_channel`.
_LEGACY_ACTIVE_CHANNEL_FILENAME = "active_channel.json"

# The legacy PowerShell app's output folder. Lives here (not in avg.shell)
# so avg.api.app can compute the "found N projects" suggestion without
# importing avg.shell - which itself imports avg.api.app.create_app, and a
# mutual import between the two would be circular. avg.shell imports this
# same constant rather than keeping its own copy.
LEGACY_OUTPUT_ROOT = Path(r"D:\AI_Video_Gen\output")


class SettingsDoc(BaseModel):
    """The on-disk shape: `{"schema_version": 1, "output_root": ..., "onboarded": ...}`.

    `output_root` is `None` until the user has actually chosen one - an
    absent or corrupt file, or one with `onboarded` false, both mean "run
    the welcome screen".
    """

    schema_version: int = SCHEMA_VERSION
    output_root: str | None = None
    onboarded: bool = False
    active_channel_id: str | None = None
    # Values for every scope="app" entry in avg.core.settings_schema, keyed
    # by setting id. An opaque dict here for the same reason Channel.settings
    # is opaque in avg.config.channels: the schema (not this document) is
    # the source of truth for what belongs in it, and this module only
    # guarantees it round-trips.
    values: dict[str, Any] = Field(default_factory=dict)


def default_settings_path() -> Path:
    """`%APPDATA%/AVG Studio/settings.json`.

    Falls back to `~/AppData/Roaming` if `APPDATA` is somehow unset (never
    true on a real Windows session, but keeps this callable in stripped-down
    environments) rather than raising - mirrors
    `avg.media.thumbnails.default_cache_dir`'s treatment of `LOCALAPPDATA`.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "settings.json"


def load_settings(path: Path | None = None) -> SettingsDoc:
    """Read the settings document, or fresh defaults if absent/corrupt.

    A missing file, unreadable JSON, or a validation failure all fall back
    to `SettingsDoc()` (`onboarded=False`) rather than raising - a broken or
    absent settings.json must never crash startup, it must just mean the
    welcome screen runs again.

    If `active_channel_id` isn't already set, this also consumes a legacy
    `active_channel.json` sitting next to `path` (see
    `_consume_legacy_active_channel`) and, if it held a value, persists the
    migrated document immediately - so the migration happens exactly once
    per install rather than depending on some later caller remembering to
    save.
    """
    path = Path(path) if path is not None else default_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        doc = SettingsDoc.model_validate(raw)
    except Exception:
        doc = SettingsDoc()

    if doc.active_channel_id is None:
        migrated = _consume_legacy_active_channel(path)
        if migrated is not None:
            doc.active_channel_id = migrated
            save_settings(doc, path)

    return doc


def _consume_legacy_active_channel(settings_path: Path) -> str | None:
    """Read-and-delete `active_channel.json` next to `settings_path`.

    Returns the id it held, or `None` if the file is absent, corrupt, or
    holds no id. The file is removed whenever it exists, migration
    succeeded or not - a corrupt legacy pointer is dead weight either way,
    and leaving it in place would mean re-running this check (and finding
    the same corrupt file) on every future load forever. Deleting it here,
    rather than leaving it for a caller to notice, is what makes the
    migration genuinely one-time: once gone, a later `set_active(None, ...)`
    on the new document can never be silently overridden by a stale legacy
    value on the next load.
    """
    legacy_path = settings_path.parent / _LEGACY_ACTIVE_CHANNEL_FILENAME
    channel_id: str | None = None
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            value = raw.get("active_channel_id")
            if isinstance(value, str):
                channel_id = value
    except Exception:
        channel_id = None
    finally:
        with suppress(OSError):
            legacy_path.unlink()
    return channel_id


def save_settings(doc: SettingsDoc, path: Path | None = None) -> None:
    """Write the settings document atomically.

    Writes to a temp file in the same directory, then `os.replace`s it over
    the real path - `path` either holds the old complete contents or the
    new complete contents, never a half-written file, even if the process
    is killed mid-write. The temp file is cleaned up on any failure so a
    crash never litters the settings folder.
    """
    path = Path(path) if path is not None else default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(doc.model_dump_json(indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


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
