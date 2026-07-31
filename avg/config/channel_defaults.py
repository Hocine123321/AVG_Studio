"""The app-wide defaults a channel can read INSTEAD of its own document.

`CLAUDE.md`'s Config scopes rule deletes the legacy copy mechanism: "No
global copy, no apply/capture step, no absent-means-inherit. Three bugs in
the legacy app came from the copy mechanism this deletes." Those three bugs
came from PER-FIELD, IMPLICIT inheritance - a field missing from a channel
silently resolving to a global one.

This document is not that. A channel reads its own settings or it reads
this one, whole, chosen explicitly by `settings_source` and defaulting to
its own. No field is ever resolved by looking in one document and falling
back to the other, so there is no per-field resolution to get wrong. The
shape is the one `live_list_source` already ships
(`avg/api/routes_settings.py`'s `_prefs_for_scope`).

Storage only. The either/or resolution lives in `avg/api/routes_settings.py`.
Never holds a secret: `type="secret"` values live in `avg.config.secrets`
alone and the porting action skips them.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def default_channel_defaults_path() -> Path:
    """`%APPDATA%/AVG Studio/channel_defaults.json`.

    Mirrors `avg.config.settings`'s own default; `create_app` passes an
    override so tests never touch the real profile.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "channel_defaults.json"


def load_defaults(path: Path | None = None) -> dict[str, Any]:
    """The stored values, or `{}`.

    A missing file, unreadable JSON or a malformed shape all mean "nothing
    set yet" rather than an exception - the same tolerance
    `avg.core.model_cache` and `avg.core.error_store` are read with. A
    broken document must never be the thing that stops the app.
    """
    target = Path(path) if path is not None else default_channel_defaults_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - any read failure means "nothing set yet"
        return {}
    values = data.get("values") if isinstance(data, dict) else None
    return values if isinstance(values, dict) else {}


def save_defaults(values: dict[str, Any], path: Path | None = None) -> None:
    """Replace the document with `values`, atomically.

    A wholesale replace, not a merge: this document is a snapshot the user
    took, and merging would quietly turn it into the per-field inheritance
    the module docstring rules out. Temp file then `os.replace`, matching
    `avg.pipeline.create.save_project` and `avg.config.settings`.
    """
    target = Path(path) if path is not None else default_channel_defaults_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".channel-defaults-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "values": values}, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise
