"""The app's own settings document, and the read-only import of the legacy
app's `config.json`.

Two directions over the same subject. The first half is a small file AVG
Studio itself owns and writes, currently holding exactly one durable
decision: where the user's project library lives. The second half
(`load_render_settings`) only ever *reads*, from the old PowerShell app's
`config.json`, and never writes anything - it exists so a project imported
from `D:\\AI_Video_Gen` renders with the settings it was made under.

Reading and writing only happen here; no HTTP, no UI concerns -
`avg.api.app` calls into this module and shapes the result into routes,
`avg.shell` calls into it to decide `resolve_output_root()`.

Deliberately NOT folded in here: `avg.config.legacy_import`, which imports
whole legacy *project folders* rather than settings. It depends on
`avg.pipeline.registry` for phase order, and this module sits below
`avg.config.channels` -> `avg.pipeline.phase` -> the phases -> that same
registry, so merging it would close an import cycle. `suggest_output_root`
below reaches it through a function-local import for the same reason.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avg.config.user_assets import user_assets_root

# ── App Settings Document ────────────────────────────

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
    """`Documents/AVG Studio/UserAssets/Config/settings.json`.

    Composed from `avg.config.user_assets.user_assets_root()` - see that
    module for the fallback behaviour and the one-time migration off the
    pre-Slice-20 `%APPDATA%/AVG Studio/settings.json` this triggers on
    first call.
    """
    return user_assets_root() / "Config" / "settings.json"


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


# ── Legacy Render-Settings Import ───────────────────────────
#
# Reads only four groups from the old 21-key config.json: video.*, edit.*,
# video_format.*, runtime.*. Everything else (AI providers, API keys, voice,
# images, channel profiles) is out of scope - those are owned by
# `avg.core.settings_schema` and `avg.config.channels` in this app, not
# imported from the legacy one.


class RenderSettings(BaseModel):
    """Render settings extracted from legacy app config."""

    fps: int
    codec: str
    aspect_ratio: str
    short_edge: int
    ken_burns: bool
    kb_intensity: int
    kb_mode: str
    render_engine: str
    render_opts: dict
    worker_cap: int
    drop_grain: bool
    skip_prebuild: bool


def _get(cfg: dict, dotted_path: str, default=None):
    """Get a value from nested dict using dotted path notation.

    Never raises on missing branches. Returns default if any intermediate
    level is missing or is not a dict (e.g., null, a scalar).

    Examples:
        _get({"video": {"fps": 24}}, "video.fps", 24) -> 24
        _get({}, "video.fps", 24) -> 24
        _get({"video": None}, "video.fps", 24) -> 24
    """
    parts = dotted_path.split(".")
    current = cfg

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            # current is not a dict (None, scalar, list, etc.)
            return default

    return current if current is not None else default


def load_render_settings(config_path: Path, total_ram_gb: float) -> RenderSettings:
    """Load render settings from legacy app config, applying RAM gates.

    Args:
        config_path: Path to config.json
        total_ram_gb: Total system RAM in GB (used for RAM gates)

    Returns:
        RenderSettings object with all render-relevant settings

    RAM gates:
        - worker_cap = 2 if total_ram_gb <= 8 else 4
        - drop_grain = total_ram_gb <= 8.5
        - skip_prebuild = total_ram_gb < 12
    """
    config_path = Path(config_path)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    # Extract values with sensible defaults
    fps = _get(cfg, "video.fps", 24)
    codec = _get(cfg, "video.codec", "libx264")
    aspect_ratio = _get(cfg, "video_format.aspect_ratio", "9:16")
    short_edge = _get(cfg, "video_format.short_edge", 1080)
    ken_burns = _get(cfg, "video.ken_burns", False)
    kb_intensity = _get(cfg, "video.kb_intensity", 100)
    kb_mode = _get(cfg, "video.kb_mode", "flat")
    render_engine = _get(cfg, "video.render_engine", "auto")
    render_opts = _get(cfg, "runtime.render_opts", {})

    # Apply RAM gates
    worker_cap = 2 if total_ram_gb <= 8 else 4
    drop_grain = total_ram_gb <= 8.5
    skip_prebuild = total_ram_gb < 12

    return RenderSettings(
        fps=fps,
        codec=codec,
        aspect_ratio=aspect_ratio,
        short_edge=short_edge,
        ken_burns=ken_burns,
        kb_intensity=kb_intensity,
        kb_mode=kb_mode,
        render_engine=render_engine,
        render_opts=render_opts,
        worker_cap=worker_cap,
        drop_grain=drop_grain,
        skip_prebuild=skip_prebuild,
    )
