"""The app-wide defaults a channel-scope setting resolves through when the
active channel's own document doesn't hold that key.

`CLAUDE.md`'s Config scopes rule: every `scope="channel"` setting resolves
through `avg.core.settings_schema.resolve_channel_settings` (Slice 19
§2.3) - the channel's own document wins per field if it has the key, else
this document wins per field if it has the key, else the `SettingSpec`'s
own default. Absence is the only inheritance signal, checked with `in`,
never truthiness, so an explicitly-stored empty string is never treated as
a gap.

Storage only. The resolution itself lives in
`avg.core.settings_schema.resolve_channel_settings`, called from
`avg/api/routes_settings.py` and threaded into the pipeline via
`PhaseContext.channel_values`.
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

from avg.config.user_assets import user_assets_root


def default_channel_defaults_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/channel_defaults.json`.

    Mirrors `avg.config.settings`'s own default; `create_app` passes an
    override so tests never touch the real profile.
    """
    return user_assets_root() / "Config" / "channel_defaults.json"


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
