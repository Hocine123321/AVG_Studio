# Slice 7 — Repair, Diagnostics and Model Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the packaged app actually generate and render video again, then give it an error-handling system that makes every future failure self-diagnosing, a real dependency check, a working-models cache, and the UI fixes the 3rd redesign pass left behind.

**Architecture:** Three layers, in order. First a one-line-class defect fix that unblocks every AI call the shipped app makes. Then a diagnostics layer (`avg/core/errors_store.py`, App Health) that captures whatever else is broken instead of asking the user to describe it. Then the features that build on top: a probed model cache that turns the free-text model fields into a picker, and the UI corrections.

**Tech Stack:** Python 3.12 + FastAPI + pydantic on the backend, React 19 + Vite + TypeScript in WebView2, PyInstaller for packaging. No new runtime dependencies.

## Global Constraints

- `.venv-build\Scripts\python.exe` is the interpreter for everything. System `python` lacks the dependencies.
- **No live API calls in tests, ever.** `avg/providers/transport.py` replays recorded fixtures; recording needs `AVG_HTTP_RECORD=1`. Image and voice providers cost real money.
- **`avg/media/vendor/` is protected.** Byte-for-byte copies of the old render engine. Never edit.
- **Nothing below `avg/api/` may print or prompt.** Lower layers emit typed events; the API layer decides what reaches the user.
- **Every `SettingSpec` must declare `invalidates`** — required, no default.
- Tests must not write to the real `%APPDATA%/AVG Studio` — pass the `create_app` path overrides.
- **One filled terracotta (`#e08a4e`) primary action per screen.** `web/src/tokens.css`'s header is the authority on the accent.
- Never add a Google Fonts `@import`. Caprasimo and Figtree are self-hosted in `web/public/fonts`.
- A secret value must never reach a log line, an exception message, a `repr()`, a settings response, or a recorded fixture. This constraint is load-bearing for Tasks 2–4, which persist error text to disk.
- Work is **local**. Commit to `main`. No PRs, no pushing.
- Run after every task: `.venv-build/Scripts/python.exe -m pytest tests -q` (573 passing at the start of this slice), then `npm run build && npm run lint` in `web/` for any frontend change.

---

## Findings this plan is built on

Each was verified against the code or the running app on 2026-07-29. Do not re-derive them; do verify anything you are about to change.

| # | Finding | Evidence |
|---|---|---|
| F1 | **`Transport` defaults to `replay` mode in production.** `avg/providers/transport.py:261` reads `"record" if AVG_HTTP_RECORD else "replay"`. Every backend builds a bare `Transport()` when none is injected (`providers/text/community.py:88,179,285`, `providers/images/community.py:81,175,309`). The shipped app therefore cannot make a real API call — it looks for a test fixture and fails. This is the Brief failure in the user's screenshot, and Visuals' "no finished scenes.json" is its downstream consequence. | Screenshot: `No recorded fixture for POST https://api.groq.com/...` |
| F2 | **`POST /render` returns 202 in the dev server.** Reproduced against a real project on 2026-07-29. The user's 500 is specific to the packaged build, so it cannot be diagnosed by reading dev-mode code. Task 3 makes it report itself. | Live reproduction |
| F3 | **`edge_tts` costs ~2.0s of a ~4.3s startup.** Imported at module scope by `providers/voice/providers.py`, pulled in by `pipeline/registry` → `config/legacy_import` → `api/app`. `edge_tts.communicate` alone is 0.53s, `aiohttp` 0.83s. | `python -X importtime -c "import avg.shell"` |
| F4 | **The library size walk is on the first request's critical path.** `_library_bytes` runs inside `_load_library` before `library_loaded.set()`, so the first `GET /api/projects` waits for a full recursive stat of every project folder (481 MB / 16 projects here) on top of the import scan. Introduced 2026-07-29 with the Overview row. | `avg/api/app.py` `_load_library` |
| F5 | **The health check covers 4 packages.** FFmpeg, Pillow, moviepy, NumPy. The app also needs fastapi, uvicorn, pywebview, httpx, pydantic, edge_tts, pydub and imageio_ffmpeg, none of which are checked. | `avg/core/health.py` `_PACKAGES` |
| F6 | **`.settings-group__list` sets `overflow: hidden`**, which clips the custom select's absolutely-positioned list — the "dropdown goes under the card" bug. | `web/src/tokens.css:1692`, screenshot |
| F7 | **No scrollbar styling anywhere** (`grep -c scrollbar web/src/tokens.css` → 0), so WebView2 renders the classic Windows scrollbar. | Screenshot |
| F8 | **No application icon.** `tools/avg.spec` passes no `icon=`, and `web/public/` holds only `fonts`. | `tools/avg.spec` |
| F9 | **The legacy working-models cache** is `config/working_models_cache.json`, shape `{"updated": iso8601, "models": {provider: {model_id: bool}}}`, built by `Invoke-WorkingModelsCacheUpdate` (`D:\AI_Video_Gen\modules\visuals.ps1:1792`) which lists each keyed provider's models live and probes each with a 1-token call. `Get-CachedWorkingModelIds` returns only `true` entries and returns empty rather than inventing model names. | `D:\AI_Video_Gen` |
| F10 | **The legacy severity vocabulary** is `info` / `warn` / `critical` with categories `RESOURCE`/`STATE`/`ACTION`/`DEPENDENCY`/`CONFIG`. | `D:\AI_Video_Gen\modules\utilities.ps1:2592` |

---

## Scope note

This plan covers everything in the request **except** the final item — "simplify the app codebase / write an App Coding Guide". That is a whole-repo refactor whose right shape depends on what Tasks 1–12 add, and mixing it in would mean refactoring code that is still moving. It gets its own plan once this one lands; Task 12 writes the guide's first version so the refactor plan has something to work from.

---

## File Structure

**New files**

| Path | Responsibility |
|---|---|
| `avg/core/error_store.py` | Recording, severity classification, persistence and retrieval of app errors. Pure data + file I/O; no HTTP, no printing. |
| `avg/core/model_cache.py` | Reading and writing the working-models cache document. No network. |
| `avg/providers/probe.py` | Probing one provider's models with a minimal call and reporting which answer. The only new module allowed to touch the network. |
| `web/src/ErrorLog.tsx` | The App Health error list, severity tags, and the copy-for-AI button. |
| `web/src/ModelPicker.tsx` | The cache-backed model selector that replaces the free-text model fields. |
| `docs/APP_CODING_GUIDE.md` | The structural blueprint (the legacy `info.txt`'s job). |
| `assets/icon.svg`, `assets/icon.ico` | The application icon, source and packaged form. |

**Modified files**

| Path | Change |
|---|---|
| `avg/providers/transport.py` | Add a third mode, `live`, and make it the default. |
| `avg/core/health.py` | Full dependency list split into essential/optional; error-store surface for the report. |
| `avg/api/app.py` | Exception middleware that records to the error store; move the size walk off the first-request path. |
| `avg/api/routes_settings.py` | Error-log, model-cache and probe routes. |
| `avg/providers/voice/providers.py` | Lazy `edge_tts` import. |
| `avg/core/settings_schema.py` | Model fields become `choice`-backed by the cache. |
| `web/src/Select.tsx` | Portal the open list so no ancestor can clip it. |
| `web/src/tokens.css` | Thin scrollbars; spacing corrections. |
| `web/src/AppHealth.tsx` | Mount `ErrorLog` and the cache-build button. |
| `tools/avg.spec`, `web/index.html` | Wire the icon. |

---

## Task 1: Transport gains a live mode and defaults to it

The defect behind the user's first screenshot. Nothing else in this plan matters until this lands.

**Files:**
- Modify: `avg/providers/transport.py:253-320`
- Test: `tests/providers/test_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Transport(mode: Literal["live", "record", "replay"] | None = None)`. `None` resolves to `record` when `AVG_HTTP_RECORD` is truthy, `replay` when `AVG_REPLAY_FIXTURES` is truthy, and `live` otherwise. Tests that need replay must now ask for it.

- [ ] **Step 1: Write the failing test**

Add to `tests/providers/test_transport.py`:

```python
def test_default_mode_is_live_not_replay(monkeypatch):
    """The shipped app must make real calls.

    This is a regression test for a defect that made the packaged app
    unusable: the default was `replay`, so every provider call in
    production looked for a test fixture and failed with "No recorded
    fixture for POST https://api.groq.com/...". A test-only default had
    become the production default.
    """
    monkeypatch.delenv("AVG_HTTP_RECORD", raising=False)
    monkeypatch.delenv("AVG_REPLAY_FIXTURES", raising=False)
    assert Transport().mode == "live"


def test_replay_is_opt_in_by_env(monkeypatch):
    monkeypatch.delenv("AVG_HTTP_RECORD", raising=False)
    monkeypatch.setenv("AVG_REPLAY_FIXTURES", "1")
    assert Transport().mode == "replay"


def test_record_still_wins_over_replay(monkeypatch):
    monkeypatch.setenv("AVG_HTTP_RECORD", "1")
    monkeypatch.setenv("AVG_REPLAY_FIXTURES", "1")
    assert Transport().mode == "record"


def test_live_mode_performs_a_real_request_through_the_client():
    """Live mode must not consult the fixture directory at all.

    Uses a MockTransport so this makes no network call - what is being
    proven is that the request reaches the httpx client rather than the
    replay lookup.
    """
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    transport = Transport(
        mode="live",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )
    response = transport.request("POST", "https://example.test/v1/chat", json={"a": 1})
    assert response.status_code == 200
    assert seen == ["https://example.test/v1/chat"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv-build/Scripts/python.exe -m pytest tests/providers/test_transport.py -q -k "live or opt_in or record_still"`

Expected: FAIL — `assert 'replay' == 'live'`.

- [ ] **Step 3: Implement the third mode**

In `avg/providers/transport.py`, change the `Mode` alias and the constructor. Replace the existing mode resolution (line ~261):

```python
Mode = Literal["live", "record", "replay"]
```

```python
        # Default is LIVE. This used to default to `replay`, which meant the
        # shipped app could not make a single real API call - every provider
        # call looked for a test fixture and failed with "No recorded
        # fixture for POST https://api.groq.com/...". A default that only
        # made sense inside the test suite had become the production
        # default.
        #
        # Replay is now opt-in, and `tests/conftest.py` is what opts in, so
        # the no-live-calls rule is enforced by the test harness rather than
        # by a default that leaks into the product.
        if mode is not None:
            self.mode: Mode = mode
        elif _env_truthy("AVG_HTTP_RECORD"):
            self.mode = "record"
        elif _env_truthy("AVG_REPLAY_FIXTURES"):
            self.mode = "replay"
        else:
            self.mode = "live"
        if self.mode not in ("live", "record", "replay"):
            raise ValueError(
                f"unknown transport mode {self.mode!r} (expected 'live', 'record' or 'replay')"
            )
```

Then in `request()`, where the replay branch is (line ~304), make live perform the call without recording:

```python
        if self.mode == "replay":
            return self._replay(method, url, body_bytes)

        response = self._perform(method, url, headers=headers, json=json, content=content)
        if self.mode == "record":
            self._record(method, url, body_bytes, response)
        return response
```

Adjust `_perform`/`_record` to match the existing private helpers' real names — read the file rather than assuming these two exist under exactly these names.

- [ ] **Step 4: Make the test suite opt into replay**

Add to `tests/conftest.py` (create it if absent):

```python
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _replay_fixtures_everywhere():
    """The whole suite runs in replay mode.

    Transport defaults to LIVE now (see `avg/providers/transport.py`), so
    the no-live-calls rule has to be asserted by the harness rather than
    relied on as a default. Set here, session-wide and automatically, so a
    new test file cannot forget it and quietly start spending money.
    """
    os.environ.setdefault("AVG_REPLAY_FIXTURES", "1")
    yield
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q`

Expected: `575 passed` (573 + the new transport tests). If any provider test now fails with a real connection attempt, it is constructing a `Transport` before the fixture applies — fix that test, never the default.

- [ ] **Step 6: Prove it end to end against the dev server**

```bash
.venv-build/Scripts/python.exe tools/_dev_serve.py
```

Then create a video from the UI at `http://127.0.0.1:8756/?token=devtoken` and watch the Brief phase. Expected: it calls Groq for real and completes, instead of failing with "No recorded fixture". Groq is the only text provider with quota (see `CLAUDE.md`'s account state).

- [ ] **Step 7: Commit**

```bash
git add avg/providers/transport.py tests/providers/test_transport.py tests/conftest.py
git commit -m "Make Transport default to live, not replay

The packaged app could not make a single real API call: Transport's mode
defaulted to `replay` unless AVG_HTTP_RECORD was set, and every provider
builds a bare Transport() when none is injected. Every AI call in the
shipped app therefore looked for a test fixture and failed with 'No
recorded fixture for POST https://api.groq.com/...' - which is why Brief
failed and Visuals then had no scenes.json to work from.

A test-only default had become the production default. Live is now the
default, replay is opt-in via AVG_REPLAY_FIXTURES, and tests/conftest.py
opts the whole suite in - so the no-live-calls rule is enforced by the
harness rather than by a default that leaks into the product."
```

---

## Task 2: The error store

**Files:**
- Create: `avg/core/error_store.py`
- Test: `tests/core/test_error_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Severity = Literal["critical", "needs_review", "low"]`
  - `@dataclass(frozen=True) ErrorRecord(id: str, when: datetime, severity: Severity, source: str, summary: str, detail: str, context: dict[str, str])`
  - `ErrorStore(path: Path, limit: int = 200)` with `record(...) -> ErrorRecord`, `all() -> list[ErrorRecord]`, `clear() -> None`
  - `classify(kind: str | None, status: int | None, source: str) -> Severity`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_error_store.py`:

```python
"""Tests for the app error store (`avg.core.error_store`).

The store exists so a user can hand a failure to an AI assistant without
having to describe it. That makes two properties load-bearing, and both
are tested here: it must never lose the newest error, and it must never
persist a secret.
"""

from __future__ import annotations

from datetime import datetime, timezone

from avg.core.error_store import ErrorStore, classify


def test_records_survive_a_reload(tmp_path):
    store = ErrorStore(tmp_path / "errors.json")
    store.record(severity="critical", source="render", summary="boom", detail="traceback")

    reopened = ErrorStore(tmp_path / "errors.json")
    records = reopened.all()
    assert len(records) == 1
    assert records[0].summary == "boom"
    assert records[0].severity == "critical"


def test_newest_first_and_capped(tmp_path):
    store = ErrorStore(tmp_path / "errors.json", limit=3)
    for i in range(5):
        store.record(severity="low", source="ui", summary=f"e{i}", detail="")

    summaries = [r.summary for r in store.all()]
    # Newest first, and the cap drops the OLDEST - a store that dropped the
    # newest would be worse than no store at all.
    assert summaries == ["e4", "e3", "e2"]


def test_api_keys_are_redacted_before_anything_is_written(tmp_path):
    """A traceback can carry an Authorization header. It must not reach disk."""
    path = tmp_path / "errors.json"
    store = ErrorStore(path)
    store.record(
        severity="critical",
        source="providers",
        summary="auth failed",
        detail="Authorization: Bearer gsk_livekeyvalue123456 \nx-api-key: sk-abcdef123456",
    )

    raw = path.read_text(encoding="utf-8")
    assert "gsk_livekeyvalue123456" not in raw
    assert "sk-abcdef123456" not in raw
    assert "REDACTED" in raw


def test_clear_empties_the_store(tmp_path):
    store = ErrorStore(tmp_path / "errors.json")
    store.record(severity="low", source="ui", summary="x", detail="")
    store.clear()
    assert store.all() == []


def test_a_corrupt_store_file_does_not_break_the_app(tmp_path):
    path = tmp_path / "errors.json"
    path.write_text("{not json", encoding="utf-8")
    store = ErrorStore(path)
    # Starts empty rather than raising: an unreadable error log must never
    # itself become the thing that stops the app.
    assert store.all() == []
    store.record(severity="low", source="ui", summary="fine", detail="")
    assert len(store.all()) == 1


def test_classification_follows_the_legacy_vocabulary():
    # Anything that stops a run outright.
    assert classify(kind="bug", status=None, source="pipeline") == "critical"
    assert classify(kind=None, status=500, source="api") == "critical"
    # Recoverable, but the user should look.
    assert classify(kind="rate_limit", status=429, source="providers") == "needs_review"
    assert classify(kind="auth", status=401, source="providers") == "needs_review"
    # Everything else.
    assert classify(kind=None, status=404, source="api") == "low"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_error_store.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'avg.core.error_store'`.

- [ ] **Step 3: Implement the store**

Create `avg/core/error_store.py`:

```python
"""Every failure the app notices, kept so it can be handed to an assistant.

The legacy PowerShell app wrote timestamped `logs/error_log_*.log` files
and classified conditions as `info` / `warn` / `critical`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`). This is the same idea fitted
to this app: one JSON document, newest first, capped, with the severity
vocabulary the user asked for - `critical` / `needs_review` / `low`.

**Why it exists.** Debugging this app currently means the user describing
a failure from memory or a screenshot. A recorded error carries the
traceback, the route, and the phase, and the App Health screen can hand
the whole thing over as one paste.

**The one rule that outranks usefulness:** a stored error must never
contain a key. Tracebacks and HTTP error bodies routinely carry
`Authorization: Bearer ...`; `_redact` runs over every field before
anything is written, and the test for it asserts against the raw file
bytes rather than the parsed object.

No printing, no HTTP, no imports from `avg.api` - this sits below the API
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

#: Newest-first cap. Enough to cover a session's worth of failures without
#: the document growing without bound; the oldest fall off, never the newest.
DEFAULT_LIMIT = 200

# Anything shaped like a credential. Deliberately broad - a false positive
# costs a redacted word in a log, a false negative leaks a key.
_SECRET_PATTERNS = (
    re.compile(r"(Authorization\s*:\s*Bearer\s+)\S+", re.I),
    re.compile(r"((?:x-)?api[-_]?key\s*[:=]\s*)\S+", re.I),
    re.compile(r"\b(sk-|gsk_|hf_|AIza)[A-Za-z0-9_\-]{8,}", re.I),
)


def _redact(text: str) -> str:
    """Strip anything credential-shaped. Runs before every write."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "REDACTED", text)
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

    Three levels, matching what the user asked for and mirroring the
    legacy `info`/`warn`/`critical` vocabulary:

      - `critical`     - the run stopped, or the server raised. Nothing the
                         user can do about it from the UI.
      - `needs_review` - recoverable and usually about an account: a rate
                         limit, an expired key, a quota. The user can act.
      - `low`          - everything else.

    Keyed on the error taxonomy's `ErrorKind` first, since that is already
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
    worker threads and from the render drain thread, and two of those can
    fire at once.
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
            # An unreadable error log must never be the thing that stops
            # the app - start over rather than raise.
            return []
        return data.get("errors", []) if isinstance(data, dict) else []

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
        with self._lock:
            rows = self._load()
            payload = asdict(record)
            payload["when"] = record.when.isoformat()
            rows.insert(0, payload)
            del rows[self.limit :]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"errors": rows}, indent=2), encoding="utf-8"
            )
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"errors": []}, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run the tests**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_error_store.py -q`

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add avg/core/error_store.py tests/core/test_error_store.py
git commit -m "Add the app error store

The legacy app kept timestamped error logs and classified conditions as
info/warn/critical. This is the same idea fitted to this app: one capped,
newest-first JSON document with the severity vocabulary the user asked
for - critical / needs_review / low - so a failure can be handed to an
assistant instead of described from memory.

Severity keys on the error taxonomy's ErrorKind first, since that is
already the one place this app decides what a failure means, and on HTTP
status only for failures that never went through it.

Redaction runs over every field before anything is written, and its test
asserts against the raw file bytes rather than the parsed object: a
traceback carrying 'Authorization: Bearer ...' must not put a key on disk."
```

---

## Task 3: Wire the store into the API and record every failure

This is what makes the packaged-build 500 (F2) diagnose itself.

**Files:**
- Modify: `avg/api/app.py` (create_app body; add middleware)
- Modify: `avg/api/routes_settings.py` (add the error routes)
- Test: `tests/api/test_error_routes.py`

**Interfaces:**
- Consumes: `avg.core.error_store.ErrorStore`, `classify` from Task 2.
- Produces:
  - `app.state.error_store: ErrorStore`
  - `create_app(..., errors_path: Path | None = None)`
  - `GET /api/errors -> {"errors": [{id, when, severity, source, summary, detail, context}, ...]}`
  - `DELETE /api/errors -> {"cleared": true}`
  - `POST /api/errors -> {"recorded": id}` body `{severity, source, summary, detail?, context?}` — how the frontend reports its own failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_error_routes.py`:

```python
"""Tests for the error surface (`GET/POST/DELETE /api/errors`).

The point of the store is that a failure the user did not witness is
still recoverable afterwards, so the test that matters most is the one
asserting an unhandled 500 lands in it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def paths(tmp_path):
    return {
        "settings_path": tmp_path / "settings.json",
        "channels_dir": tmp_path / "channels",
        "secrets_path": tmp_path / "secrets.dat",
        "output_root": tmp_path / "output",
        "thumb_cache_dir": tmp_path / "thumbs",
        "errors_path": tmp_path / "errors.json",
    }


@pytest.fixture
def client(paths):
    return TestClient(create_app(token=TOKEN, **paths), raise_server_exceptions=False)


def test_errors_route_requires_a_token(client):
    assert client.get("/api/errors").status_code == 401


def test_an_unhandled_server_error_is_recorded(client, paths):
    """The whole reason this exists.

    The packaged build returns 500 from POST /render and nothing in the
    app remembers why. After this, the App Health screen can show the
    traceback that produced it.
    """
    app = client.app

    @app.get("/api/_boom")
    async def _boom() -> dict:
        raise RuntimeError("deliberate explosion")

    assert client.get("/api/_boom", headers=AUTH).status_code == 500

    errors = client.get("/api/errors", headers=AUTH).json()["errors"]
    assert len(errors) == 1
    assert errors[0]["severity"] == "critical"
    assert errors[0]["source"] == "api"
    assert "deliberate explosion" in errors[0]["detail"]
    assert "/api/_boom" in errors[0]["context"]["path"]


def test_the_frontend_can_report_its_own_failures(client):
    posted = client.post(
        "/api/errors",
        headers=AUTH,
        json={"severity": "low", "source": "ui", "summary": "thumbnail failed to load"},
    )
    assert posted.status_code == 201
    errors = client.get("/api/errors", headers=AUTH).json()["errors"]
    assert errors[0]["summary"] == "thumbnail failed to load"


def test_clear_empties_the_log(client):
    client.post("/api/errors", headers=AUTH, json={"severity": "low", "source": "ui", "summary": "x"})
    assert client.delete("/api/errors", headers=AUTH).status_code == 200
    assert client.get("/api/errors", headers=AUTH).json()["errors"] == []


def test_a_recorded_error_never_carries_a_key(client):
    client.post(
        "/api/errors",
        headers=AUTH,
        json={
            "severity": "critical",
            "source": "ui",
            "summary": "call failed",
            "detail": "Authorization: Bearer gsk_shouldnotappear999",
        },
    )
    body = client.get("/api/errors", headers=AUTH).text
    assert "gsk_shouldnotappear999" not in body
    assert "REDACTED" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-build/Scripts/python.exe -m pytest tests/api/test_error_routes.py -q`

Expected: FAIL — `create_app() got an unexpected keyword argument 'errors_path'`.

- [ ] **Step 3: Add the store and the middleware to `create_app`**

In `avg/api/app.py`, add the parameter to the signature (match the existing override style used by `settings_path` / `channels_dir` / `secrets_path`), then near the other `app.state` assignments:

```python
    # Errors are stored beside the settings document, so a user who moves
    # their profile takes the log with them. Overridable like every other
    # path so tests never write to the real %APPDATA%.
    app.state.error_store = ErrorStore(
        errors_path
        if errors_path is not None
        else Path(app.state.settings_path).with_name("errors.json")
    )
```

Then add the middleware, immediately after the app is constructed:

```python
    @app.middleware("http")
    async def _record_unhandled(request: Request, call_next):
        """Every unhandled exception becomes a stored, severity-tagged error.

        Without this, a 500 from a packaged build is a dead end: the user
        sees "500 internal server error", the traceback goes to a console
        that a windowed build does not have, and nobody can say what
        happened. Now the App Health screen can show it - which is exactly
        how the render 500 in this slice's own findings is meant to be
        diagnosed.

        Re-raises after recording. This observes; it does not swallow.
        """
        try:
            return await call_next(request)
        except Exception as exc:
            with suppress(Exception):
                app.state.error_store.record(
                    severity=classify(kind=None, status=500, source="api"),
                    source="api",
                    summary=f"{type(exc).__name__}: {exc}",
                    detail="".join(traceback.format_exception(exc)),
                    context={"path": str(request.url.path), "method": request.method},
                )
            raise
```

Imports to add at the top of `avg/api/app.py`:

```python
import traceback

from fastapi import Request

from avg.core.error_store import ErrorStore, classify
```

- [ ] **Step 4: Add the three routes**

In `avg/api/routes_settings.py`, alongside the existing health routes:

```python
class ErrorReport(BaseModel):
    """`POST /api/errors` - how the frontend reports what only it can see.

    A thumbnail that would not load, a WebSocket that dropped: failures
    the backend never hears about, and exactly the ones a user cannot
    describe accurately afterwards.
    """

    severity: Literal["critical", "needs_review", "low"]
    source: str
    summary: str
    detail: str = ""
    context: dict[str, str] = Field(default_factory=dict)


@router.get("/api/errors")
async def list_errors(request: Request) -> dict:
    return {
        "errors": [
            {
                "id": r.id,
                "when": r.when.isoformat(),
                "severity": r.severity,
                "source": r.source,
                "summary": r.summary,
                "detail": r.detail,
                "context": r.context,
            }
            for r in request.app.state.error_store.all()
        ]
    }


@router.post("/api/errors", status_code=status.HTTP_201_CREATED)
async def report_error(body: ErrorReport, request: Request) -> dict:
    record = request.app.state.error_store.record(
        severity=body.severity,
        source=body.source,
        summary=body.summary,
        detail=body.detail,
        context=body.context,
    )
    return {"recorded": record.id}


@router.delete("/api/errors")
async def clear_errors(request: Request) -> dict:
    request.app.state.error_store.clear()
    return {"cleared": True}
```

Add `Literal` to the `typing` import and `Field` to the `pydantic` import at the top of that file.

- [ ] **Step 5: Run the tests**

Run: `.venv-build/Scripts/python.exe -m pytest tests/api/test_error_routes.py tests -q`

Expected: `582 passed`.

- [ ] **Step 6: Commit**

```bash
git add avg/api/app.py avg/api/routes_settings.py tests/api/test_error_routes.py
git commit -m "Record every unhandled failure to the error store

A 500 from a packaged build was a dead end: the user saw '500 internal
server error', the traceback went to a console a windowed build does not
have, and nothing in the app remembered it. Middleware now records every
unhandled exception with its traceback, path and method, then re-raises -
it observes, it does not swallow.

The frontend gets POST /api/errors for the failures only it can see, a
dropped socket or an image that would not load, which are precisely the
ones a user cannot describe accurately afterwards."
```

---

## Task 4: The App Health error log with copy-for-AI

**Files:**
- Create: `web/src/ErrorLog.tsx`
- Modify: `web/src/AppHealth.tsx`, `web/src/api.ts`, `web/src/tokens.css`

**Interfaces:**
- Consumes: `GET/DELETE /api/errors` from Task 3.
- Produces: `web/src/api.ts` exports `AppError`, `getErrors()`, `clearErrors()`, `reportError(body)`.

- [ ] **Step 1: Add the client functions**

In `web/src/api.ts`:

```ts
export type ErrorSeverity = "critical" | "needs_review" | "low";

export interface AppError {
  id: string;
  when: string;
  severity: ErrorSeverity;
  source: string;
  summary: string;
  detail: string;
  context: Record<string, string>;
}

export function getErrors(): Promise<{ errors: AppError[] }> {
  return apiGet<{ errors: AppError[] }>("/api/errors");
}

export function clearErrors(): Promise<{ cleared: boolean }> {
  return apiDelete<{ cleared: boolean }>("/api/errors");
}

/** Report a failure only the frontend can see. Fire-and-forget by
 * convention - a failure to report a failure must never surface. */
export function reportError(body: {
  severity: ErrorSeverity;
  source: string;
  summary: string;
  detail?: string;
  context?: Record<string, string>;
}): Promise<{ recorded: string }> {
  return apiPost<{ recorded: string }>("/api/errors", body);
}
```

- [ ] **Step 2: Write `ErrorLog.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { clearErrors, getErrors, type AppError } from "./api";

const SEVERITY_LABEL: Record<AppError["severity"], string> = {
  critical: "Critical",
  needs_review: "Needs review",
  low: "Low",
};

const SEVERITY_CLASS: Record<AppError["severity"], string> = {
  critical: "chip chip--bad",
  needs_review: "chip chip--amber",
  low: "chip",
};

/**
 * Turns one recorded error into a paste an assistant can act on without
 * any further back-and-forth.
 *
 * The prompt is part of the payload on purpose. A bare traceback makes the
 * assistant ask which app, which version, what the user was doing; every
 * one of those round trips is the delay this feature exists to remove.
 */
function promptFor(error: AppError): string {
  const context = Object.entries(error.context)
    .map(([k, v]) => `- ${k}: ${v}`)
    .join("\n");
  return [
    "I hit an error in AVG Studio (Python + FastAPI backend, React 19 in a",
    "WebView2 window, Windows desktop). Please find the cause and propose a fix.",
    "",
    `Severity: ${SEVERITY_LABEL[error.severity]}`,
    `Where: ${error.source}`,
    `When: ${error.when}`,
    `Summary: ${error.summary}`,
    context ? `\nContext:\n${context}` : "",
    error.detail ? `\nDetail:\n\`\`\`\n${error.detail}\n\`\`\`` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * The latest failures, newest first, at the bottom of App Health.
 *
 * Every row carries its severity and a Copy button that puts a
 * ready-to-send prompt on the clipboard. The list is deliberately plain
 * text rather than a link to a log file: the user should never have to
 * find a file to report a bug.
 */
export default function ErrorLog() {
  const [errors, setErrors] = useState<AppError[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    getErrors()
      .then((r) => setErrors(r.errors))
      .catch(() => setErrors([]));
  }, []);

  useEffect(load, [load]);

  async function copy(error: AppError) {
    try {
      await navigator.clipboard.writeText(promptFor(error));
      setCopied(error.id);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // Clipboard can be refused. Say nothing rather than raise an error
      // about the error reporter.
    }
  }

  return (
    <section className="errorlog" aria-label="Recent errors">
      <header className="errorlog__head">
        <div>
          <h2 className="health-stage__title">Recent errors</h2>
          <p className="health-stage__detail">
            Newest first. Copy sends the whole thing, with a prompt, ready to paste.
          </p>
        </div>
        <span className="home__spacer" />
        <button
          type="button"
          className="btn"
          disabled={!errors || errors.length === 0}
          onClick={() => void clearErrors().then(load)}
        >
          Clear
        </button>
      </header>

      {errors !== null && errors.length === 0 && (
        <p className="home__empty">Nothing has gone wrong yet.</p>
      )}

      <ul className="errorlog__list">
        {(errors ?? []).map((error) => (
          <li key={error.id} className="errorlog__row">
            <span className={SEVERITY_CLASS[error.severity]}>{SEVERITY_LABEL[error.severity]}</span>
            <button
              type="button"
              className="errorlog__summary"
              onClick={() => setExpanded(expanded === error.id ? null : error.id)}
              title="Show the full detail"
            >
              {error.summary}
            </button>
            <span className="errorlog__meta mono">{new Date(error.when).toLocaleString()}</span>
            <button type="button" className="btn btn--sm" onClick={() => void copy(error)}>
              {copied === error.id ? "Copied" : "Copy"}
            </button>
            {expanded === error.id && error.detail && (
              <pre className="errorlog__detail">{error.detail}</pre>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 3: Mount it at the bottom of App Health**

In `web/src/AppHealth.tsx`, after the `</div>` that closes `.health-stages`:

```tsx
      <ErrorLog />
```

and `import ErrorLog from "./ErrorLog";` at the top.

- [ ] **Step 4: Style it**

Append to `web/src/tokens.css`:

```css
/* ---- recent errors (App Health) ---- */

.errorlog {
  margin-top: var(--gap);
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: var(--surface);
  padding: 18px 22px;
  box-shadow: var(--shadow-sm);
}

.errorlog__head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
}

.errorlog__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.errorlog__row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 10px 0;
  border-top: 1px solid var(--line);
  font-size: 13px;
}

/* The summary is the disclosure control - the whole row would be a large
   target for something that only expands text. */
.errorlog__summary {
  flex: 1;
  min-width: 0;
  text-align: left;
  background: none;
  border: 0;
  padding: 0;
  color: var(--text);
  font: inherit;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.errorlog__meta {
  color: var(--dim);
  font-size: 11.5px;
}

.errorlog__detail {
  flex-basis: 100%;
  margin: 8px 0 0;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  background: var(--canvas);
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.5;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
```

- [ ] **Step 5: Build, lint, and look at it**

Run: `npm --prefix web run build && npm --prefix web run lint`

Then restart `tools/_dev_serve.py` (it does not reload on backend changes), open App Health, and confirm: the section renders, "Nothing has gone wrong yet." appears on a clean store, and after triggering a failure a row appears with the right severity tag and Copy puts the prompt on the clipboard.

- [ ] **Step 6: Commit**

```bash
git add web/src/ErrorLog.tsx web/src/AppHealth.tsx web/src/api.ts web/src/tokens.css
git commit -m "Show recent errors in App Health with a copy-for-AI button

Newest first, severity-tagged, each row expanding to its full detail. The
Copy button puts a ready-to-send prompt on the clipboard rather than a
bare traceback: a traceback alone makes an assistant ask which app, which
version and what the user was doing, and every one of those round trips
is the delay this feature exists to remove."
```

---

## Task 5: Diagnose and fix the packaged-build render 500

Only attempt this **after** Tasks 1–4 are merged. The error store is the instrument.

**Files:**
- Modify: whatever Step 3 identifies. Do not pre-empt it.
- Test: a regression test in `tests/api/test_app.py` for the specific cause found.

- [ ] **Step 1: Build the installer from current source**

```bash
"Build Installer.bat"
```

Takes about 4 minutes and produces `AVG APP/` and `installer/AVG-Setup.exe`.

- [ ] **Step 2: Reproduce**

Launch `AVG APP/AVG Studio.exe`, open a project that has images and audio, and press the render CTA. Expect the 500 the user reported.

- [ ] **Step 3: Read the cause out of App Health**

Open App Health → Recent errors. The middleware from Task 3 recorded the traceback. Copy it.

- [ ] **Step 4: Write a failing test for that exact cause**

Reproduce the condition in a test against `create_app`, not against the frozen build. If the traceback shows, for example, that `channels_config.active_channel_id` raises on a settings document written by an older build, the test writes that document and asserts `POST /render` returns 202.

- [ ] **Step 5: Fix it, run the suite, rebuild, re-verify**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
"Build Installer.bat"
```

Launch the built app again and confirm the render starts.

- [ ] **Step 6: Commit**

Message must name the actual cause found, not "fix render 500".

---

## Task 6: Cut startup time

Two measured costs (F3, F4). No guessing.

**Files:**
- Modify: `avg/providers/voice/providers.py`
- Modify: `avg/api/app.py` (`_load_library`)
- Test: `tests/core/test_startup_cost.py`

- [ ] **Step 1: Record the baseline**

Run: `.venv-build/Scripts/python.exe -X importtime -c "import avg.shell" 2>&1 | sort -t'|' -k2 -rn | head -5`

Write the total for `avg.shell` into the commit message at the end. It is ~4.33s today.

- [ ] **Step 2: Write the failing test**

Create `tests/core/test_startup_cost.py`:

```python
"""Startup must not pay for what it does not use.

A structural test, not a benchmark: it asserts that importing the app
does not drag in the heavy optional libraries. Timing assertions would
be flaky on a loaded machine; "was this module imported at all" is exact.
"""

from __future__ import annotations

import subprocess
import sys

CODE = (
    "import sys; import avg.shell; "
    "print('edge_tts' in sys.modules, 'aiohttp' in sys.modules)"
)


def test_importing_the_app_does_not_import_edge_tts():
    """edge_tts cost ~2.0s of a ~4.3s startup and is not needed until the
    voice phase actually runs - which is minutes into a pipeline, if ever."""
    result = subprocess.run(
        [sys.executable, "-c", CODE], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False False", result.stdout
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_startup_cost.py -q`

Expected: FAIL — `assert 'True True' == 'False False'`.

- [ ] **Step 4: Make the edge_tts import lazy**

In `avg/providers/voice/providers.py`, remove the module-scope `import edge_tts` and move it inside the method that synthesises:

```python
    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        # Imported here, not at module scope: edge_tts pulls in aiohttp and
        # costs about two seconds of a four-second startup, and the phase
        # registry imports this module transitively from `avg.api.app` - so
        # every launch paid for a TTS library that is not touched until the
        # voice phase runs, minutes in, if at all.
        import edge_tts  # noqa: PLC0415
```

Keep any type-only references behind `if TYPE_CHECKING:`.

- [ ] **Step 5: Move the size walk off the first request's path**

In `avg/api/app.py`'s `_load_library`, the `_library_bytes` call currently runs before `library_loaded.set()`. Move it after, so the project list is answerable as soon as the scan is done:

```python
        try:
            report = import_all(output_root)
            app.state.projects = {p.id: p for p in report.imported}
        except Exception:
            pass
        finally:
            app.state.projects_loading = False
            # Released BEFORE the size walk: `GET /api/projects` waits on
            # this, and a full recursive stat of every project folder is
            # not something the library list should be held up by. The
            # Overview card shows a dash until the walk lands, which is
            # what `storage_bytes` being absent already means to it.
            app.state.library_loaded.set()

        with suppress(Exception):
            app.state.storage_bytes = _library_bytes(app.state.projects.values())
        _warm_thumbnails()
```

- [ ] **Step 6: Run the tests**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q`

Expected: all pass. If `test_storage_bytes_sums_the_whole_library` becomes flaky, it is now racing the walk — make it poll for a non-zero value with a bounded deadline rather than reading once.

- [ ] **Step 7: Measure again**

Run: `.venv-build/Scripts/python.exe -X importtime -c "import avg.shell" 2>&1 | sort -t'|' -k2 -rn | head -5`

Expected: `avg.shell` total drops by roughly 2s.

- [ ] **Step 8: Commit**

```bash
git add avg/providers/voice/providers.py avg/api/app.py tests/core/test_startup_cost.py
git commit -m "Cut two seconds off startup

Measured, not guessed. edge_tts was imported at module scope by the voice
provider, which the phase registry imports transitively from the API app -
so every launch paid ~2.0s of a ~4.3s startup for a TTS library nothing
touches until the voice phase runs, minutes in if at all.

The library size walk also sat in front of the first GET /api/projects: it
ran before library_loaded was set, so the project list waited on a full
recursive stat of every project folder on top of the import scan. It now
runs after, and the Overview card shows a dash until it lands.

Guarded by a structural test rather than a timing one - 'was this module
imported at all' is exact, and a benchmark would be flaky on a loaded
machine."
```

---

## Task 7: A real dependency check

**Files:**
- Modify: `avg/core/health.py`
- Modify: `web/src/AppHealth.tsx` (render the two groups)
- Test: `tests/core/test_health.py`

**Interfaces:**
- Produces: `Component` gains `required: bool`. `check_all()` returns essential components first, then optional ones.

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_health.py`:

```python
def test_every_runtime_dependency_is_checked():
    """The check covered four packages and the app needs a dozen.

    A dependency check that only looks at the render path cannot answer
    "why won't it start" - which is the question it exists for.
    """
    names = {c.name for c in health.check_all(refresh=True)}
    assert {
        "FFmpeg",
        "Pillow",
        "moviepy",
        "NumPy",
        "FastAPI",
        "uvicorn",
        "pywebview",
        "httpx",
        "pydantic",
        "edge-tts",
        "pydub",
        "imageio-ffmpeg",
    } <= names


def test_essential_and_optional_are_distinguished():
    by_name = {c.name: c for c in health.check_all(refresh=True)}
    # Without these there is no app and no render.
    assert by_name["FFmpeg"].required is True
    assert by_name["FastAPI"].required is True
    # Voice has a second provider; captions degrade to synthetic timing.
    assert by_name["pydub"].required is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_health.py -q -k "runtime_dependency or essential_and_optional"`

Expected: FAIL — `AttributeError: 'Component' object has no attribute 'required'`.

- [ ] **Step 3: Extend the component list**

In `avg/core/health.py`, add `required: bool = True` to `Component`, and replace `_PACKAGES`:

```python
# (label, import name, distribution name, required)
#
# `required` means "the app cannot do its job without this", not "pip
# installed it". The split exists because a dependency check that reports
# a dozen equal-weight rows cannot answer the question it is for - which
# of these is why nothing works.
_PACKAGES: tuple[tuple[str, str, str, bool], ...] = (
    # Rendering.
    ("Pillow", "PIL", "Pillow", True),
    ("moviepy", "moviepy", "moviepy", True),
    ("NumPy", "numpy", "numpy", True),
    ("imageio-ffmpeg", "imageio_ffmpeg", "imageio-ffmpeg", True),
    # The app itself - without any of these there is no window and no API.
    ("FastAPI", "fastapi", "fastapi", True),
    ("uvicorn", "uvicorn", "uvicorn", True),
    ("pywebview", "webview", "pywebview", True),
    ("pydantic", "pydantic", "pydantic", True),
    ("httpx", "httpx", "httpx", True),
    # Degradable: Edge TTS is the free voice provider but ElevenLabs
    # exists, and pydub only refines silence handling.
    ("edge-tts", "edge_tts", "edge-tts", False),
    ("pydub", "pydub", "pydub", False),
)
```

Update `_check_package` to take and pass through `required`, and `check_all` to sort essential first:

```python
    components = [_check_ffmpeg()]
    components.extend(
        _check_package(label, module, dist, required)
        for label, module, dist, required in _PACKAGES
    )
    components.sort(key=lambda c: (not c.required, c.name.lower()))
```

Note that `_check_package` importing `edge_tts` reintroduces the startup cost from Task 6 — but only when a health check actually runs, which is on demand, never at import. Add that as a comment so nobody "optimises" it back.

- [ ] **Step 4: Surface `required` on the route and in the UI**

Add `"required": c.required` to the component dicts in `avg/api/routes_settings.py`'s `get_health` and `get_health_report`, add `required: boolean` to `HealthComponent` in `web/src/api.ts`, and in `AppHealth.tsx` render optional rows with a muted "Optional" tag so a missing optional dependency does not read as a broken app.

- [ ] **Step 5: Run everything**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q && npm --prefix web run build && npm --prefix web run lint`

- [ ] **Step 6: Commit**

```bash
git add avg/core/health.py avg/api/routes_settings.py web/src/api.ts web/src/AppHealth.tsx tests/core/test_health.py
git commit -m "Check every runtime dependency, not just the render path

The check covered FFmpeg, Pillow, moviepy and NumPy. The app also needs
FastAPI, uvicorn, pywebview, pydantic, httpx, imageio-ffmpeg, edge-tts and
pydub, none of which were checked - so the screen could not answer 'why
won't it start', which is the question it exists for.

Essential and optional are now distinguished, because a dozen
equal-weight rows cannot say which missing one is the reason nothing
works. Edge TTS and pydub are optional: there is a second voice provider,
and pydub only refines silence handling."
```

---

## Task 8: The working-models cache — probing and storage

Ported from `Invoke-WorkingModelsCacheUpdate` (F9).

**Files:**
- Create: `avg/core/model_cache.py`
- Create: `avg/providers/probe.py`
- Test: `tests/core/test_model_cache.py`, `tests/providers/test_probe.py`

**Interfaces:**
- Produces:
  - `model_cache.ModelCache(path: Path)` with `working(provider: str) -> list[str]`, `save(results: dict[str, dict[str, bool]]) -> None`, `updated_at() -> datetime | None`
  - `probe.probe_provider(provider: str, *, transport: Transport | None = None) -> dict[str, bool]`
  - `probe.probe_all(providers: Iterable[str], *, transport=None) -> dict[str, dict[str, bool]]`

- [ ] **Step 1: Write the failing cache tests**

Create `tests/core/test_model_cache.py`:

```python
"""Tests for the working-models cache.

Ported from the legacy app's `working_models_cache.json`, whose shape is
{"updated": iso8601, "models": {provider: {model_id: bool}}}. The rule
that matters is the legacy one: only models a probe PROVED working are
returned, and an empty result stays empty rather than falling back to a
hardcoded catalogue - the old app's built-in list went stale and made it
impossible to tell what had actually been verified.
"""

from __future__ import annotations

from avg.core.model_cache import ModelCache


def test_only_proven_models_come_back(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    cache.save({"groq": {"llama-3.3-70b-versatile": True, "broken-model": False}})
    assert cache.working("groq") == ["llama-3.3-70b-versatile"]


def test_an_unprobed_provider_is_empty_never_invented(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    cache.save({"groq": {"a": True}})
    assert cache.working("gemini") == []


def test_a_missing_cache_file_is_empty_not_an_error(tmp_path):
    assert ModelCache(tmp_path / "nothing.json").working("groq") == []


def test_saving_stamps_the_time(tmp_path):
    cache = ModelCache(tmp_path / "models.json")
    assert cache.updated_at() is None
    cache.save({"groq": {"a": True}})
    assert cache.updated_at() is not None


def test_results_survive_a_reload(tmp_path):
    path = tmp_path / "models.json"
    ModelCache(path).save({"groq": {"a": True, "b": False}})
    assert ModelCache(path).working("groq") == ["a"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_model_cache.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'avg.core.model_cache'`.

- [ ] **Step 3: Implement the cache**

Create `avg/core/model_cache.py`:

```python
"""Which models a provider actually answered on, last time we asked.

Ported from the legacy app's `config/working_models_cache.json`
(`D:\\AI_Video_Gen\\modules\\visuals.ps1`), same document shape:

    {"updated": "<iso8601>", "models": {"<provider>": {"<model id>": bool}}}

**The rule inherited verbatim, because the legacy app learned it the hard
way:** only models the probe PROVED working are ever returned, and a
provider with nothing proven returns an empty list rather than a
hardcoded catalogue. The old app's built-in model list went stale and it
became impossible to tell what had actually been verified - so an empty
result must read as "build your cache", never as a list of guesses.

Storage only. Probing lives in `avg.providers.probe`; nothing here
touches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class ModelCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def working(self, provider: str) -> list[str]:
        """Model ids this provider proved working, sorted. Empty when
        unprobed - never a guess."""
        models = self._load().get("models", {})
        entry = models.get(provider)
        if not isinstance(entry, dict):
            return []
        return sorted(model for model, ok in entry.items() if ok is True)

    def providers(self) -> list[str]:
        models = self._load().get("models", {})
        return sorted(models) if isinstance(models, dict) else []

    def updated_at(self) -> datetime | None:
        raw = self._load().get("updated")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def save(self, results: dict[str, dict[str, bool]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "models": {p: dict(sorted(m.items())) for p, m in results.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Write the failing probe test**

Create `tests/providers/test_probe.py`:

```python
"""Tests for model probing.

No network: every call goes through an injected Transport backed by an
httpx MockTransport. A probe that reached a real provider would cost
money on the paid ones, which is the rule this whole suite exists under.
"""

from __future__ import annotations

import httpx

from avg.providers.probe import probe_provider
from avg.providers.transport import Transport


def _mock_transport(handler) -> Transport:
    return Transport(
        mode="live",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_model_that_answers_is_marked_working(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "fixture-key")
    monkeypatch.setattr(
        "avg.providers.probe.list_models", lambda provider, transport: ["good", "bad"]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if b"bad" in request.content:
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    results = probe_provider("groq", transport=_mock_transport(handler))
    assert results == {"good": True, "bad": False}


def test_a_provider_with_no_key_probes_nothing(monkeypatch):
    monkeypatch.setattr("avg.providers.probe.get_key", lambda _p: "")
    # No key means no call at all - never a probe that fails for the wrong
    # reason and marks every model broken.
    assert probe_provider("gemini", transport=None) == {}
```

- [ ] **Step 5: Implement the probe**

Create `avg/providers/probe.py`. It must:
- read the provider's key via `avg.config.secrets.get_key` and return `{}` immediately when there is none;
- list the provider's models through the existing catalogue helper in `avg/providers/text/` (read that module and reuse it — do not write a second lister);
- issue the smallest possible generation per model (`max_tokens=1`) through the injected `Transport`;
- treat any `AvgError` or non-2xx as `False` and a 2xx as `True`;
- never raise: one dead model must not abort the sweep.

Keep it under ~120 lines. If a provider needs a differently-shaped probe request, branch on the provider name in one `match` — the legacy app does exactly this and says why.

- [ ] **Step 6: Run the tests**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_model_cache.py tests/providers/test_probe.py -q`

Expected: `7 passed`.

- [ ] **Step 7: Commit**

```bash
git add avg/core/model_cache.py avg/providers/probe.py tests/core/test_model_cache.py tests/providers/test_probe.py
git commit -m "Add the working-models cache and the probe that fills it

Ported from the legacy app's working_models_cache.json, same document
shape, and with the rule it learned the hard way kept verbatim: only
models the probe PROVED working are returned, and a provider with nothing
proven returns empty rather than a hardcoded catalogue. The old built-in
list went stale and made it impossible to tell what had actually been
verified, so empty has to read as 'build your cache', never as a guess.

Storage and probing are separate modules - nothing in avg/core touches
the network, and the probe takes an injected Transport so no test can
reach a real provider."
```

---

## Task 9: Build-cache button and the model picker

**Files:**
- Modify: `avg/api/app.py` (`app.state.model_cache`, `model_cache_path` override)
- Modify: `avg/api/routes_settings.py` (two routes)
- Create: `web/src/ModelPicker.tsx`
- Modify: `web/src/AppHealth.tsx`, `web/src/fields/Fields.tsx`, `web/src/fields/index.tsx`, `web/src/api.ts`
- Test: `tests/api/test_model_cache_routes.py`

**Interfaces:**
- Produces:
  - `GET /api/models/cache -> {"updated": iso|null, "providers": {provider: [model, ...]}}`
  - `POST /api/models/cache/build -> 202 {"started": true}`, runs on the existing one-job slot pattern and broadcasts progress over `/ws/events`
  - `web/src/api.ts`: `getModelCache()`, `buildModelCache()`

- [ ] **Step 1: Write the failing route tests**

```python
def test_cache_route_reports_empty_before_any_build(client):
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["updated"] is None
    assert body["providers"] == {}


def test_build_is_refused_while_one_is_running(client, monkeypatch):
    """Probing every model of every keyed provider takes 30-90 seconds in
    the legacy app. Two at once would double the spend for nothing."""
    started = threading.Event()
    release = threading.Event()

    def slow_probe(providers, transport=None):
        started.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr("avg.api.routes_settings.probe_all", slow_probe)
    try:
        assert client.post("/api/models/cache/build", headers=AUTH).status_code == 202
        assert started.wait(timeout=5)
        assert client.post("/api/models/cache/build", headers=AUTH).status_code == 409
    finally:
        release.set()
```

- [ ] **Step 2: Run to verify failure, then implement the routes**

Follow the existing background-worker pattern in `avg/api/app.py`'s `start_render` — a thread, a state slot, a 409 on a second start, and progress broadcast through `app.state.manager`. Do not invent a second job mechanism.

- [ ] **Step 3: Make the model settings a picker**

`text_model_default`, `<phase>_model` and `<phase>_backup` are `type="text"` today (that is what the user means by "the model selector is a writeable blank"). Add a `source` field to `SettingSpec`:

```python
    #: Where a choice-like field gets its options at runtime, when they
    #: cannot be enumerated in the catalogue. `"model_cache"` means the
    #: working-models cache fills it - which is why the cache has to be
    #: built before a model can be picked.
    source: str | None = None
```

Set `source="model_cache"` on those eight settings, leaving `type="text"` so an unbuilt cache still allows a typed value rather than locking the user out.

- [ ] **Step 4: Write `ModelPicker.tsx`**

A `Select` (from Task 11's portalled version) whose options are `provider/model` pairs assembled from `GET /api/models/cache`. When the cache is empty it renders a disabled control reading "Build the model cache first" with a link to App Health — the legacy behaviour, and the reason the cache must exist before a model can be chosen.

- [ ] **Step 5: Add the build button to App Health**

Next to "Re-run everything": a plain (not terracotta) button reading "Build model cache", showing "Probing…" while running, and the cache's `updated` timestamp underneath.

- [ ] **Step 6: Run everything and verify against the real account**

`groq` is the only text provider with quota. Build the cache from the UI and confirm the Groq models come back with a mix of `true` and `false`, and that the Models table's pickers then offer exactly the `true` ones.

- [ ] **Step 7: Commit**

---

## Task 10: Portal the select so nothing can clip it

Fixes F6.

**Files:**
- Modify: `web/src/Select.tsx`
- Modify: `web/src/tokens.css`

- [ ] **Step 1: Reproduce**

Open Settings → Runtime, open the "Color theme" dropdown, and confirm the list is cut off by the card edge. Cause: `.settings-group__list` sets `overflow: hidden` to clip its rows to the rounded corners, which also clips the absolutely-positioned list.

- [ ] **Step 2: Render the list through a portal**

In `web/src/Select.tsx`, import `createPortal` from `react-dom` and render the open list into `document.body`, positioning it from the button's `getBoundingClientRect()`:

```tsx
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!open) return;
    const measure = () => setRect(buttonRef.current?.getBoundingClientRect() ?? null);
    measure();
    // The list is positioned in viewport coordinates, so any scroll or
    // resize invalidates it. It already closes on ancestor scroll; this
    // keeps it correct for the window's own.
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);
```

and wrap the `<ul>` in `createPortal(..., document.body)` with `position: fixed`, `top: rect.bottom + 5`, `left`/`minWidth` from the rect. Flip above the button when `rect.bottom + 260 > window.innerHeight`.

`.sel__list` loses `position: absolute` in favour of `position: fixed`; keep `z-index: 40`.

Portalling is the general fix rather than removing `overflow: hidden` from that one card — any future container with clipping would reintroduce the bug, and the card's rounded corners genuinely need the clip.

- [ ] **Step 3: Verify in all three places**

Settings fields, the Create screen's chips, and the New-take panel's re-roll picker. Confirm each opens over the card, closes on outside click and on scroll, and still supports arrows, Enter, Escape and type-ahead.

- [ ] **Step 4: Commit**

---

## Task 11: Thin scrollbars and the UI corrections

Fixes F7 and the spacing issues.

**Files:**
- Modify: `web/src/tokens.css`

- [ ] **Step 1: Add scrollbar styling**

```css
/* ---- scrollbars ----
   WebView2 renders the classic Windows scrollbar by default: a grey
   channel with square ends and arrow buttons, which is the one piece of
   OS chrome left in an otherwise fully themed app. Styled for both
   engines - `scrollbar-*` is the standard, the `::-webkit-` rules are
   what Chromium (and therefore WebView2) actually honours today. */

* {
  scrollbar-width: thin;
  scrollbar-color: var(--line-2) transparent;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: var(--line-2);
  border-radius: 999px;
  /* Inset via a transparent border so the thumb reads as a thin pill
     rather than filling the gutter. */
  border: 3px solid transparent;
  background-clip: content-box;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--muted);
  background-clip: content-box;
}

::-webkit-scrollbar-corner {
  background: transparent;
}
```

- [ ] **Step 2: Fix the spacing inconsistencies**

Walk every screen at 1280×820 and at 900×700 and fix what the redesign pass left:
- the Overview row's cards should keep equal heights when one wraps to two chip rows (`align-items: stretch` is already implied by grid; verify);
- `.errorlog` and `.health-stage` should share one padding value, not two;
- the status pill must not overlap the Channels header's "+ New channel" button — the same class of collision fixed for Studio.

Change nothing that is already consistent. Record what you changed in the commit message.

- [ ] **Step 3: Verify in both themes**

Toggle night/day and confirm the scrollbar thumb is visible against both grounds.

- [ ] **Step 4: Commit**

---

## Task 12: App icon and the coding guide

**Files:**
- Create: `assets/icon.svg`, `assets/icon.ico`
- Modify: `tools/avg.spec`, `web/index.html`
- Create: `docs/APP_CODING_GUIDE.md`

- [ ] **Step 1: Draw the icon**

An "A" in Caprasimo, `--canvas` (#1c1a16) on a terracotta (#e08a4e) rounded square at a 22% corner radius — the rail's brand mark, which already exists in `Rail.tsx` as `.rail__logo`, promoted to the app icon. Save as `assets/icon.svg` at 512×512.

- [ ] **Step 2: Generate the .ico**

Pillow is already a dependency:

```python
from PIL import Image
img = Image.open("assets/icon-512.png")
img.save("assets/icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
```

Windows needs every one of those sizes: without 16 and 32 the taskbar and title bar scale the 256 down badly.

- [ ] **Step 3: Wire it into the build and the window**

In `tools/avg.spec`, add `icon=str(REPO / "assets" / "icon.ico")` to the `EXE(...)` call. Add a `<link rel="icon" href="/icon.svg">` to `web/index.html` and copy the SVG into `web/public/`.

- [ ] **Step 4: Verify**

Run `"Build Installer.bat"` and confirm the icon appears on `AVG Studio.exe`, in the taskbar, and in Add/Remove Programs.

- [ ] **Step 5: Write the coding guide**

Create `docs/APP_CODING_GUIDE.md`. It is a **blueprint**, distinct from `CLAUDE.md`:

- `CLAUDE.md` says what the rules are and what must never be done.
- `APP_CODING_GUIDE.md` says where everything is and how a change flows through it, so an assistant can find the right file without reading the tree.

Sections, in this order:
1. **One-paragraph system map** — process model, the four layers, what talks to what.
2. **Every module, one line each** — path, responsibility, and what it must not know about.
3. **Route table** — method, path, what it returns, which module owns the work.
4. **The five recipes** — "add a setting", "add a phase", "add a provider", "add a screen", "add an event". Each names the exact files to touch in order, and nothing else.
5. **Data documents** — `project.json`, `settings.json`, channel documents, `errors.json`, the model cache. Shape and owner for each.
6. **Where state lives** — `app.state` keys and their lifetimes.

Keep it under 400 lines. It is a map, not a manual; anything explained twice belongs in one place with a pointer from the other.

- [ ] **Step 6: Commit**

---

## Deferred to its own plan

**Codebase simplification.** The request's final item. `APP_CODING_GUIDE.md` (Task 12) is its input: once the map exists, the places where the structure fights the map are what the refactor targets. Write that plan after this one lands, against a codebase that has stopped moving.

---

## Verification for the whole slice

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — expect roughly 600 passing, never fewer than 573.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] `"Build Installer.bat"` — produces `AVG APP/` and the installer.
- [ ] From the **installed** app, not the dev server: create a video end to end. Brief must call Groq for real (Task 1), and no phase may fail with "No recorded fixture".
- [ ] Render an existing project from the installed app. No 500 (Task 5).
- [ ] App Health: three stages pass, every dependency listed with essential/optional, Recent errors populated, Copy produces a paste-ready prompt.
- [ ] Build the model cache, then pick a model from the Models table's dropdown.
- [ ] No console window appears at any point during a render.
