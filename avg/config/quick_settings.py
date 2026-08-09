"""avg/config/quick_settings.py - which settings the Quick panel shows.

Mirrors avg.config.channel_defaults exactly: one document, one owner,
replaced wholesale, atomic write. The one difference from that module: a
missing file reads back as STARTER_PRESET, not [] - this document always
has SOMETHING to show, per the design spec's "ships with a starter preset."

Storage only. Filtering an id that no longer exists in the catalogue
happens in avg/api/routes_settings.py, which is the one place that already
knows the catalogue - this module has no import on avg.core.settings_schema
and never will, for the same layering reason channel_defaults.py stays
storage-only.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

STARTER_PRESET: list[str] = [
    "video_form",
    "length_preset",
    "voice_id",
    "image_provider",
    "captions_enabled",
]


def default_quick_settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "quick_settings.json"


def load_quick_settings(path: Path | None = None) -> list[str]:
    target = Path(path) if path is not None else default_quick_settings_path()
    if not target.is_file():
        return list(STARTER_PRESET)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken document is not a crash
        return list(STARTER_PRESET)
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return list(STARTER_PRESET)
    return ids


def save_quick_settings(ids: list[str], path: Path | None = None) -> None:
    target = Path(path) if path is not None else default_quick_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".quick-settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "ids": ids}, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise
