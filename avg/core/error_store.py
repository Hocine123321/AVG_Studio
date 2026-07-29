"""Every failure the app notices, kept so it can be handed to an assistant.

The legacy PowerShell app wrote timestamped `logs/error_log_*.log` files and
classified conditions as `info` / `warn` / `critical`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`). This is the same idea fitted to
this app: one JSON document, newest first, capped, with the three-level
vocabulary this app uses on screen - `critical` / `needs_review` / `low`.

**Why it exists.** Debugging this app meant the user describing a failure
from memory or a screenshot, and a packaged build makes that worse: it has
no console, so a traceback goes nowhere at all. A recorded error carries the
traceback, the route and the phase, and App Health can hand the whole thing
over as one paste.

**The rule that outranks usefulness:** a stored error must never contain a
key. Tracebacks and HTTP error bodies routinely carry `Authorization: Bearer
...`; `_redact` runs over every field before anything is written, and its
test asserts against the raw file bytes rather than the parsed object - the
point is that the secret never lands, not that a reader hides it.

No printing, no HTTP, no import from `avg.api`: this sits below the API
layer like everything else in `avg/core`.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal

Severity = Literal["critical", "needs_review", "low"]

#: Newest-first cap. Enough for a session's worth of failures without the
#: document growing without bound; the oldest fall off, never the newest.
DEFAULT_LIMIT = 200

# Anything credential-shaped. Deliberately broad: a false positive costs one
# redacted word in a log, a false negative leaks a key.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(Authorization\s*:\s*Bearer\s+)\S+", re.I),
    re.compile(r"((?:x-)?api[-_]?key\s*[:=]\s*)\S+", re.I),
    re.compile(r"\b(?:sk-|gsk_|hf_|AIza)[A-Za-z0-9_\-]{8,}", re.I),
)


def _redact(text: str) -> str:
    """Strip anything credential-shaped. Runs before every write."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + "REDACTED", text
        )
    return text


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    id: str
    when: datetime
    severity: Severity
    #: Which part of the app noticed: "api", "pipeline", "providers", "ui".
    source: str
    summary: str
    detail: str
    context: dict[str, str] = field(default_factory=dict)


def classify(kind: str | None, status: int | None, source: str) -> Severity:
    """Severity for one failure.

    Three levels, mirroring the legacy `info`/`warn`/`critical` vocabulary:

      - `critical`     - the run stopped, or the server raised. Not
                         something the user can act on from the UI.
      - `needs_review` - recoverable, and usually about an account: a rate
                         limit, an expired key, an exhausted quota. The user
                         can do something about it.
      - `low`          - everything else.

    Keyed on the error taxonomy's `ErrorKind` first, because that is already
    the single place this app decides what a failure means
    (`avg/providers/errors.py`), and on HTTP status only as a fallback for
    failures that never went through the taxonomy.
    """
    if kind in ("bug", "dependency", "content_filter"):
        return "critical"
    if kind in ("rate_limit", "quota", "billing", "auth", "network"):
        return "needs_review"
    if status is not None:
        if status >= 500:
            return "critical"
        if status in (401, 402, 403, 429):
            return "needs_review"
    if source == "pipeline" and kind is None:
        return "critical"
    return "low"


class ErrorStore:
    """A capped, newest-first JSON log of app errors.

    Guarded by a lock: errors arrive from request handlers, from pipeline
    worker threads and from the render drain thread, and more than one of
    those can fire at a time.
    """

    def __init__(self, path: Path, limit: int = DEFAULT_LIMIT) -> None:
        self.path = Path(path)
        self.limit = limit
        self._lock = Lock()

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            # An unreadable error log must never be the thing that stops the
            # app - start over rather than raise.
            return []
        if not isinstance(data, dict):
            return []
        rows = data.get("errors", [])
        return rows if isinstance(rows, list) else []

    def record(
        self,
        *,
        severity: Severity,
        source: str,
        summary: str,
        detail: str = "",
        context: dict[str, str] | None = None,
    ) -> ErrorRecord:
        record = ErrorRecord(
            id=uuid.uuid4().hex[:12],
            when=datetime.now(timezone.utc),
            severity=severity,
            source=source,
            summary=_redact(summary),
            detail=_redact(detail),
            context={k: _redact(str(v)) for k, v in (context or {}).items()},
        )
        payload = asdict(record)
        payload["when"] = record.when.isoformat()

        with self._lock:
            rows = self._load()
            rows.insert(0, payload)
            del rows[self.limit :]
            self._write(rows)
        return record

    def all(self) -> list[ErrorRecord]:
        """Newest first."""
        out: list[ErrorRecord] = []
        for row in self._load():
            try:
                out.append(
                    ErrorRecord(
                        id=str(row["id"]),
                        when=datetime.fromisoformat(row["when"]),
                        severity=row["severity"],
                        source=str(row["source"]),
                        summary=str(row["summary"]),
                        detail=str(row.get("detail", "")),
                        context=dict(row.get("context", {})),
                    )
                )
            except Exception:
                # One malformed row must not hide the rest.
                continue
        return out

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"errors": rows}, indent=2), encoding="utf-8")
