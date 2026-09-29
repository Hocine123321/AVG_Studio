"""Channel documents — the per-channel creative-settings scope.

The legacy PowerShell app had "channel profiles" that worked by *copying* a
bundle of settings into the global config on activation and folding them
back on save. A 2026-07-24 code review found seven bugs in that app, three
of which came directly from the copy mechanism (a catalog id that never
expanded into the text readers expected, Quick Setup writing straight to
disk and bypassing the active profile, and a new profile not saving the
outgoing one first). This module deletes that mechanism outright: **a
channel's document is the only place its settings live.** There is no
global layer, no apply step, no capture step, no absent-means-inherit
rule, and no auto-save. Reading a creative setting means reading the
active channel's `settings` dict, full stop.

A fourth bug from that same review is also this module's job to prevent:
the old app re-created deleted built-in profiles on every launch because
config defaults were back-filled into the saved config. Here, seeding the
three starter channels is gated on the channels *folder* being entirely
absent (see `_ensure_channels_dir`) - once it exists, even empty, seeding
never runs again, so a deleted channel stays deleted across restarts.

One responsibility: channel documents plus the active-channel pointer. No
HTTP, no UI, no settings schema (that is a separate, later concern), no
merging logic of any kind.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avg.config.settings import load_settings, save_settings
from avg.config.user_assets import user_assets_root

SCHEMA_VERSION = 1

# Labels for the three starter channels, seeded only when the channels
# folder is entirely absent (see _ensure_channels_dir). Chosen to slugify
# to the same ids the legacy app used (documentary, news_report,
# zack_d_films) purely as a familiar label - Slice 2 owns no settings
# schema, so every seed's `settings` dict starts empty regardless of what
# the legacy profile used to hold.
_SEED_LABELS: tuple[str, ...] = ("Documentary", "News Report", "Zack D Films")


class Channel(BaseModel):
    """One channel's on-disk document: `channels/<id>.json`.

    `settings` is an opaque dict on purpose - Slice 2 defines no creative
    settings schema (a later task owns that). This module only guarantees
    the dict round-trips exactly as given; it never reads or interprets
    any key inside it.
    """

    schema_version: int = SCHEMA_VERSION
    id: str
    label: str
    note: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


def default_channels_dir() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/channels/`.

    Composed from `avg.config.user_assets.user_assets_root()`, matching
    `avg.config.settings.default_settings_path`.
    """
    return user_assets_root() / "Config" / "channels"


def _resolve_dir(channels_dir: Path | None) -> Path:
    return Path(channels_dir) if channels_dir is not None else default_channels_dir()


def _channel_path(channels_dir: Path, channel_id: str) -> Path:
    return channels_dir / f"{channel_id}.json"


def _slugify(label: str) -> str:
    """Lowercase, ASCII-ish slug of `label`; never empty."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "channel"


def _unique_id(label: str, channels_dir: Path) -> str:
    """`_slugify(label)`, deduplicated against ids already on disk.

    "Documentary" and "documentary" both slugify to "documentary" - the
    second one saved gets "documentary_2", not a silent collision that
    overwrites the first.
    """
    base = _slugify(label)
    existing = {p.stem for p in channels_dir.glob("*.json")} if channels_dir.exists() else set()
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _atomic_write_json(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (temp file + `os.replace`),
    matching `avg.config.settings.save_settings`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def _write_channel(ch: Channel, channels_dir: Path) -> None:
    _atomic_write_json(_channel_path(channels_dir, ch.id), ch.model_dump_json(indent=2))


def _read_channel_file(path: Path) -> Channel | None:
    """A channel document, or `None` if missing/corrupt/invalid.

    A single mangled channel file must not crash the app or take down
    every other channel with it - `list_channels` simply omits it.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Channel.model_validate(raw)
    except Exception:
        return None


def _ensure_channels_dir(channels_dir: Path) -> None:
    """Create the channels folder and seed the three starter channels -
    but only the very first time this folder is touched, ever.

    The guarantee against the legacy app's "deleted profile comes back"
    bug lives entirely in this one check: seeding is gated on the folder
    not existing *at all*, never on it being empty. The folder is created
    (and never removed) the first time any public function in this module
    runs, so after that first run this branch is permanently dead for
    that install - deleting every channel leaves an empty-but-present
    folder, which reads back as "the user has zero channels", not as
    "re-seed me".
    """
    if channels_dir.exists():
        return
    channels_dir.mkdir(parents=True, exist_ok=True)
    for label in _SEED_LABELS:
        seed_id = _unique_id(label, channels_dir)
        _write_channel(Channel(id=seed_id, label=label, note="", settings={}), channels_dir)


def list_channels(channels_dir: Path | None = None) -> list[Channel]:
    """All channel documents currently on disk, sorted by id.

    Triggers first-run seeding (see `_ensure_channels_dir`) if the folder
    has never existed. Corrupt individual files are silently skipped
    rather than raising.
    """
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    channels = []
    for path in sorted(resolved.glob("*.json")):
        ch = _read_channel_file(path)
        if ch is not None:
            channels.append(ch)
    return channels


def get_channel(channel_id: str, channels_dir: Path | None = None) -> Channel | None:
    """One channel by id, or `None` if it doesn't exist / is corrupt."""
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    return _read_channel_file(_channel_path(resolved, channel_id))


def save_channel(ch: Channel, channels_dir: Path | None = None) -> None:
    """Persist `ch` to `<id>.json`, overwriting any existing document for
    that id atomically."""
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    _write_channel(ch, resolved)


def update_channel(
    channel_id: str,
    label: str | None = None,
    note: str | None = None,
    channels_dir: Path | None = None,
) -> Channel | None:
    """Rename a channel and/or edit its note in place.

    Unlike `create_channel`, this never touches `id` - a channel's id is
    also its settings-scope key (`avg.core.settings_schema`'s
    `scope="channel"` values live keyed by the *active* channel's document,
    not by label), so re-slugging on rename would silently orphan whatever
    settings the channel already pins. `label`/`note` left as `None` are
    left unchanged; passing `""` for either is a deliberate clear, not a
    no-op. Returns `None` (no-op, nothing written) if the channel doesn't
    exist, matching `get_channel`'s "missing means None" contract rather
    than raising.
    """
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    ch = _read_channel_file(_channel_path(resolved, channel_id))
    if ch is None:
        return None
    if label is not None:
        ch.label = label
    if note is not None:
        ch.note = note
    _write_channel(ch, resolved)
    return ch


def create_channel(label: str, note: str = "", channels_dir: Path | None = None) -> Channel:
    """Create and persist a new channel with an empty `settings` dict.

    The id is `_slugify(label)`, deduplicated against ids already on
    disk. Callers wanting to seed real settings must `save_channel` the
    returned document themselves - Slice 2 defines no default settings.
    """
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    new_id = _unique_id(label, resolved)
    ch = Channel(id=new_id, label=label, note=note, settings={})
    _write_channel(ch, resolved)
    return ch


def delete_channel(
    channel_id: str,
    channels_dir: Path | None = None,
    settings_path: Path | None = None,
) -> None:
    """Delete a channel's document. A no-op if it doesn't exist.

    If `channel_id` is the active channel, clears the active pointer too
    (`set_active(None, ...)`) - the app must never be left pointing at a
    channel that no longer exists.
    """
    resolved = _resolve_dir(channels_dir)
    _ensure_channels_dir(resolved)
    with suppress(FileNotFoundError):
        _channel_path(resolved, channel_id).unlink()

    if active_channel_id(settings_path) == channel_id:
        set_active(None, settings_path)


def active_channel_id(settings_path: Path | None = None) -> str | None:
    """The active channel's id, or `None` if none is set.

    Storage: `avg.config.settings`'s `SettingsDoc.active_channel_id` (Task 2
    originally gave this its own `active_channel.json` file; Task 3 folded
    it onto the app's one settings document instead - see
    `avg.config.settings.load_settings`'s migration of any existing value).
    This function and `set_active` are kept as this module's public API so
    callers written against Task 2's shape do not need to change; only the
    storage moved.

    Every reader of a creative setting starts here: `None` means "no
    channel selected", and every function in this module and its callers
    must treat that as a valid, ordinary state rather than an error.
    """
    return load_settings(settings_path).active_channel_id


def set_active(channel_id: str | None, settings_path: Path | None = None) -> None:
    """Set (or, with `None`, clear) the active-channel pointer.

    Does not verify `channel_id` exists as a channel - callers that want
    "activate this channel" semantics should confirm via `get_channel`
    first. Passing `None` explicitly clears the pointer, matching what
    `delete_channel` does when the active channel is removed. Writes
    through `avg.config.settings.save_settings`, so this is atomic the
    same way every other document in this app is.
    """
    doc = load_settings(settings_path)
    doc.active_channel_id = channel_id
    save_settings(doc, settings_path)
