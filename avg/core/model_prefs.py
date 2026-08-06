"""Which models and voices the user actually wants offered, and what they
think of them.

The Live List's own data: per entry, whether it is ticked (the pickers
offer only ticked entries), whether it is a favourite (favourites sort
first), and a free-text note (shown as the field's help text at the moment
the model is picked).

**Why this is its own document.** `working_models.json` and the provider
catalogues are machine-owned: every refresh REPLACES them, which is the
rule `avg.core.model_cache` inherited from the legacy app precisely so a
stale entry cannot outlive a probe. Folding user data into that document
would force a merge on every refresh - the exact mechanism that rule
exists to prevent - and a merge bug would lose the one thing here that
cannot be regenerated: what the user wrote.

Storage only. No network, no HTTP, no knowledge of which models exist -
an entry for a model that no longer exists is harmless and stays until the
user removes it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: The three things the Live List curates. A kind is part of the document
#: shape, so an unknown one is rejected rather than silently written into a
#: section nothing reads back.
Kind = Literal["text", "image", "voice"]
KINDS: tuple[str, ...] = ("text", "image", "voice")

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Entry:
    """One model's or voice's user-owned state."""

    selected: bool = False
    favourite: bool = False
    note: str = ""


#: Serialises every `_save` / `set_suggestions` read-modify-write against
#: every other one that targets the same file, keyed by resolved path
#: rather than held on a `ModelPrefs` instance. An instance lock would not
#: do it: `routes_settings._prefs_for_scope` constructs a brand new
#: `ModelPrefs` per request for the channel-scoped case, so the suggest
#: worker's `ModelPrefs` and a PATCH request's `ModelPrefs` are two
#: different objects that happen to point at the same file - the only
#: thing they share is the path, so that is what the lock has to be keyed
#: on. Without this, a worker that reads the document, then a user's tick
#: that reads-modifies-writes it, then the worker's own stale write, loses
#: the tick - see Important 3 of the 2026-08-06 slice-15 review.
_path_locks_guard = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _path_locks[key] = lock
        return lock


def default_prefs_path() -> Path:
    """`%APPDATA%/AVG Studio/model_prefs.json`.

    Mirrors `avg.core.model_cache`'s and `avg.config.settings`'s own
    defaults; `create_app` passes an override so tests never touch the
    real profile.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "model_prefs.json"


class ModelPrefs:
    """Read/write access to one `model_prefs.json`."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, dict[str, dict]]:
        """The document's `entries`, or `{}`.

        A missing file, unreadable JSON or a malformed shape all mean
        "nothing curated yet" rather than an exception - the same
        tolerance every other document in this app is read with. Losing a
        tick is recoverable; refusing to open the screen is not.
        """
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {k: v for k, v in entries.items() if k in KINDS and isinstance(v, dict)}

    def _load_document(self) -> dict[str, Any]:
        """The whole document, not just `entries`.

        `_load` deliberately returns only the entry sections; suggestions are
        a sibling of those, so reading and writing them needs the document
        itself.
        """
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a broken file means "nothing curated yet"
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, entries: dict[str, dict[str, dict]]) -> None:
        """Write atomically - temp file then `os.replace` - matching
        `avg.pipeline.create.save_project` and `avg.config.settings`.

        Reads the document first and rewrites only `entries`, so a tick
        never wipes a `suggested` section a prior recommendation run wrote -
        the same "touch nothing else" rule `set_suggestions` follows in the
        other direction.

        The read and the write happen under `_lock_for(self.path)`, held for
        the whole read-modify-write: without it, this read could happen
        before a concurrent `set_suggestions` call's write and this write
        could land after it, silently reverting the suggestion run it just
        stored (or the reverse). See the module-level lock's docstring.
        """
        with _lock_for(self.path):
            document = self._load_document()
            document["schema_version"] = SCHEMA_VERSION
            document["entries"] = entries
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".model_prefs-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(document, f, indent=2, sort_keys=True)
                os.replace(tmp_name, self.path)
            except BaseException:
                with suppress(OSError):
                    os.remove(tmp_name)
                raise

    def suggestions(self) -> dict[str, dict[str, Any]]:
        """What an AI last recommended, by entry id.

        An OPINION, stored here rather than in `working_models.json` because
        that document records what was MEASURED and is replaced wholesale by
        every probe. See `avg.providers.recommend`.
        """
        raw = self._load_document().get("suggested")
        if not isinstance(raw, dict):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def set_suggestions(self, rows: list[dict[str, Any]], *, kinds: set[str] | None = None) -> None:
        """Replace the suggestions for `kinds` with `rows`, touching nothing
        else - not the other kinds' suggestions, and not `entries`.

        `kinds=None` (the default) replaces every suggestion wholesale, like
        a probe sweep: the original behaviour, and still correct for a run
        that actually covers everything. A caller that only asked about one
        slice of the catalogue - `routes_settings.suggest_live_list`'s
        "Suggest voices", which never touches text or image ids - must pass
        `kinds={"voice"}` so that call cannot delete what "Suggest models"
        found. Without this, pressing the second button always cleared the
        first button's rows even though the two runs share nothing: see
        Important 4 of the 2026-08-06 slice-15 review, and Important 2's
        `kind`-trust fix, which is what makes a stored row's `kind` reliable
        enough to filter by here.

        The user's ticks, favourites and notes live in `entries` and are
        rewritten untouched - they are the one thing in this document that
        cannot be regenerated. Locked the same way `_save` is, for the same
        reason: see `_lock_for`'s docstring.
        """
        scope = set(KINDS) if kinds is None else set(kinds)
        with _lock_for(self.path):
            document = self._load_document()
            document["schema_version"] = SCHEMA_VERSION
            document.setdefault("entries", {})
            existing = document.get("suggested")
            existing = existing if isinstance(existing, dict) else {}
            kept = {
                entry_id: hint
                for entry_id, hint in existing.items()
                if isinstance(hint, dict) and hint.get("kind") not in scope
            }
            new = {
                str(row["entry_id"]): {
                    "kind": str(row.get("kind", "")),
                    "tier": row.get("tier"),
                    "reason": str(row.get("reason", "")),
                }
                for row in rows
                if row.get("entry_id")
            }
            document["suggested"] = {**kept, **new}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".model_prefs-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, indent=2, sort_keys=True)
                os.replace(tmp_name, self.path)
            except BaseException:
                with suppress(OSError):
                    os.remove(tmp_name)
                raise

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r} (expected one of {KINDS})")

    def all(self, kind: str) -> dict[str, Entry]:
        """Every entry of `kind`, by id (`provider/model`)."""
        self._check_kind(kind)
        out: dict[str, Entry] = {}
        for entry_id, raw in self._load().get(kind, {}).items():
            if not isinstance(raw, dict):
                continue
            out[str(entry_id)] = Entry(
                selected=bool(raw.get("selected", False)),
                favourite=bool(raw.get("favourite", False)),
                note=str(raw.get("note", "")),
            )
        return out

    def set(
        self,
        kind: str,
        entry_id: str,
        *,
        selected: bool | None = None,
        favourite: bool | None = None,
        note: str | None = None,
    ) -> Entry:
        """Update one entry, leaving unnamed fields as they were.

        A partial update, not a replace: the screen sends one field at a
        time (a tick, a star, a note losing focus), and a replace would
        make ticking a box erase the note next to it.
        """
        self._check_kind(kind)
        entries = self._load()
        section = dict(entries.get(kind, {}))
        current = section.get(entry_id, {})
        merged = {
            "selected": bool(current.get("selected", False)) if selected is None else bool(selected),
            "favourite": bool(current.get("favourite", False)) if favourite is None else bool(favourite),
            "note": str(current.get("note", "")) if note is None else str(note),
        }
        section[entry_id] = merged
        entries[kind] = section
        self._save(entries)
        return Entry(**merged)

    def remove(self, kind: str, entry_id: str) -> None:
        """Forget one entry. Absent is not an error - the caller's intent
        (this should not be here) is already satisfied."""
        self._check_kind(kind)
        entries = self._load()
        section = dict(entries.get(kind, {}))
        if entry_id not in section:
            return
        del section[entry_id]
        entries[kind] = section
        self._save(entries)

    def selected_ids(self, kind: str) -> list[str]:
        """The ids the pickers should offer: ticked only, favourites first.

        The ordering lives here rather than in the UI so the settings
        fields and the Live List screen cannot disagree about it.
        """
        entries = self.all(kind)
        chosen = [(entry_id, entry) for entry_id, entry in entries.items() if entry.selected]
        chosen.sort(key=lambda pair: (not pair[1].favourite, pair[0].lower()))
        return [entry_id for entry_id, _ in chosen]
