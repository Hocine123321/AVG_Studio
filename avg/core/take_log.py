"""One run log per take: what the app did, in order, with the detail.

The old app wrote a per-project `session.log` (`D:\\AI_Video_Gen\\info.txt:727`)
and this one wrote nothing at all, so a phase that failed on a user's machine
left no trace beyond a one-line `error` string in `project.json`. Ten real
projects on disk have no record of what happened to them.

**Per take, not per project.** A take is one attempt; two attempts
interleaved in one file is exactly the confusion takes exist to remove.
Deliberately NOT in `avg.core.takes.PHASE_ARTIFACTS`: a clone copies files,
not another take's history, however many of its artifacts it inherits.

**One scrubber.** `avg.core.error_store._redact` is reused rather than
matched again here. A second pattern list would drift out of step with the
first, and the first is the one that has been reviewed.

Writing a file is not printing: the "nothing below `avg/api/` may print" rule
is about seizing the user's attention, and a log is the opposite of that - it
is there for when they come looking.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

from avg.core.error_store import _redact
from avg.core.models import ProjectDoc
from avg.core.takes import take_root

#: Long field values are truncated rather than wrapped: a log line is meant to
#: be skimmable, and a 40KB prompt belongs in a `block` behind `verbose`.
_MAX_FIELD_CHARS = 400


def log_path_for(project: ProjectDoc, take_id: int) -> Path:
    """Where `take_id`'s log lives.

    Through `take_root`, so a root-storage take logs at the project folder and
    an own-storage take inside its own - the same rule every other reader of a
    take's files follows. Never `take_folder`, which only ever names the
    `takes/<id>/` path.
    """
    return take_root(project, take_id) / "take.log"


class TakeLog:
    """Append-only, line-oriented, and never fatal.

    Guarded by a lock because one run writes from the pipeline thread, the
    render drain thread and the transport layer at once.
    """

    def __init__(self, path: Path, *, verbose: bool = False) -> None:
        self.path = Path(path)
        self.verbose = verbose
        self._lock = Lock()
        self._last: str | None = None
        self._repeats = 0
        #: Held open for the job's lifetime rather than reopened per line. A
        #: pipeline run emits hundreds of lines, and an open/write/close for
        #: each one is slow enough to matter: it delayed a run's completion
        #: far enough that a following request saw the job still running.
        self._handle: TextIO | None = None

    # ── levels ────────────────────────────────────────────────────────────

    def info(self, message: str, **fields: Any) -> None:
        self._write("INFO", message, fields)

    def warn(self, message: str, **fields: Any) -> None:
        self._write("WARN", message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._write("ERROR", message, fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._write("DEBUG", message, fields)

    # ── shapes worth their own method ─────────────────────────────────────

    def event(self, payload: dict[str, Any]) -> None:
        """One typed event, by type, with its own fields.

        Events are the app's existing vocabulary for "something happened";
        logging them by hand at each emit site would let the log and the
        socket describe different runs. A `*_failed` type is logged at ERROR
        so that skimming for it finds the phase that broke.
        """
        kind = str(payload.get("type") or "event")
        fields = {key: value for key, value in payload.items() if key != "type"}
        level = "ERROR" if kind.endswith("_failed") else "INFO"
        self._write(level, kind, fields)

    def call(self, method: str, url: str, status: int | None, ms: int, **fields: Any) -> None:
        """One outbound HTTP call.

        The line every "why did this phase fail" question ends at: which
        provider was asked, what it answered, how long it took, and what the
        app decided to do about it. The host rather than the whole URL - the
        path carries nothing worth the width.
        """
        host = url.split("/")[2] if "://" in url else url
        self._write("INFO", f"{method} {host}", {"status": status, "ms": ms, **fields})

    def block(self, title: str, body: str) -> None:
        """A multi-line body - a prompt, a response, a child's stderr.

        Only when `verbose`, which is also exactly where a key is most likely
        to turn up (a request body echoed into the log), so the body goes
        through the same scrubber every other line does.
        """
        if not self.verbose:
            return
        self._write("DEBUG", f"--- {title} ---", {})
        with self._lock:
            self._append(_redact(body).rstrip() + "\n")

    def close(self) -> None:
        """Flush a pending repeat count and release the file. Safe twice."""
        with self._lock:
            self._flush_repeats()
            handle, self._handle = self._handle, None
            if handle is not None:
                with suppress(Exception):
                    handle.close()

    # ── internals ─────────────────────────────────────────────────────────

    def _write(self, level: str, message: str, fields: dict[str, Any]) -> None:
        rendered = f"{level} {message}"
        if fields:
            rendered += " " + " ".join(
                f"{key}={self._render(value)}" for key, value in fields.items() if value is not None
            )
        rendered = _redact(rendered)

        with self._lock:
            if rendered == self._last:
                self._repeats += 1
                return
            # Flushed BEFORE the new line, so a reader following the file sees
            # the repeat count where the repeats actually happened rather than
            # at the end of the run.
            self._flush_repeats()
            self._last = rendered
            self._append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {rendered}\n")

    def _flush_repeats(self) -> None:
        if self._repeats:
            self._append(f"  (x{self._repeats + 1})\n")
            self._repeats = 0

    def _render(self, value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "…"

    def _append(self, text: str) -> None:
        # A log that cannot be written must never stop a run - the same
        # tolerance `ErrorStore` applies to its own unreadable file. Covers a
        # read-only folder, a path whose parent is a file, and a locked file.
        # A failed OPEN is remembered as `None`, so an unwritable path costs
        # one failed attempt per line rather than a raised exception, and the
        # run carries on either way.
        with suppress(Exception):
            if self._handle is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8")
            self._handle.write(text)
            # Flushed per line: a log read while the job is still running -
            # which is the whole point of the log route - must show the run
            # as far as it has got, not as far as a buffer happened to fill.
            self._handle.flush()
