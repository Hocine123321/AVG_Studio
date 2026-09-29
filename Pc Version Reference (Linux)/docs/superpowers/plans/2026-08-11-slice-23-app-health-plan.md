# Slice 23 — App Health: Implementation Plan

Implements `docs/superpowers/specs/2026-08-11-slice-23-app-health-design.md`.
Read that first.

Backend verify: `.venv-build\Scripts\python.exe -m pytest tests -q` and
`.venv-build\Scripts\python.exe -m mypy avg`. Frontend verify:
`cd web && npm run build && npm run lint` (no test runner exists - see
Slice 22's plan for why, still true here).

## Files touched

| File | Change |
|---|---|
| `web/src/ErrorLog.tsx` | Expansion no longer gates on `error.detail` alone |
| `avg/api/routes_settings.py` | New `POST /api/health/open-logs` |
| `tests/api/test_routes_settings.py` | +2 tests |
| `web/src/api.ts` | New `openLogsFolder()` client function |
| `web/src/AppHealth.tsx` | Per-stage collapse/expand, default from `stage.ok` |
| `web/src/tokens.css` | `.health-stage__chevron`; `.health-checks` gains a transition + `--collapsed` variant |

Three independent tasks, no ordering dependency between them - doing the
Recent Errors fix first only because it's the smallest.

---

## Task 1 — Recent Errors: always show something

**Files:** `web/src/ErrorLog.tsx`

### Step 1.1 — implement

Replace `promptFor`'s context-building (extract it so both `promptFor` and
the new on-screen expansion use the identical formatting - the spec's
point that this is the same data, shown twice, not two representations of
it). Replace:

```typescript
function promptFor(error: AppError): string {
  const context = Object.entries(error.context)
    .map(([key, value]) => `- ${key}: ${value}`)
    .join("\n");

  return [
```

with:

```typescript
/** The same formatting `promptFor` has always used for the clipboard
 * prompt's Context section - reused by the on-screen expansion (Slice 23)
 * so an error with no `detail` (every `low`-severity degradation - see
 * `avg.core.error_store.ErrorStore.degraded`) still shows the one thing
 * this app already knows about it, instead of nothing. */
function contextText(error: AppError): string {
  return Object.entries(error.context)
    .map(([key, value]) => `- ${key}: ${value}`)
    .join("\n");
}

/** What a click on a row reveals: real detail (a traceback, most often)
 * if there is one, otherwise the same context `promptFor` already puts on
 * the clipboard, otherwise a plain admission there's nothing further -
 * never nothing at all, which read as the click doing nothing. */
function expandedContent(error: AppError): string {
  return error.detail || contextText(error) || "No further detail recorded for this error.";
}

function promptFor(error: AppError): string {
  const context = contextText(error);

  return [
```

Replace the row's summary button and expansion block:

```tsx
            <button
              type="button"
              className="errorlog__summary"
              onClick={() => setExpanded(expanded === error.id ? null : error.id)}
              title={error.detail ? "Show the full detail" : error.summary}
            >
              {error.summary}
            </button>
```

with:

```tsx
            <button
              type="button"
              className="errorlog__summary"
              onClick={() => setExpanded(expanded === error.id ? null : error.id)}
              title={error.detail || Object.keys(error.context).length > 0 ? "Show the full detail" : error.summary}
            >
              {error.summary}
            </button>
```

and:

```tsx
            {expanded === error.id && error.detail && (
              <pre className="errorlog__detail">{error.detail}</pre>
            )}
```

with:

```tsx
            {expanded === error.id && (
              <pre className="errorlog__detail">{expandedContent(error)}</pre>
            )}
```

### Step 1.2 — verify

```
cd web && npm run build && npm run lint
```

**Manual check:** trigger a degradation (or just open App Health with an
existing `low`-severity row present - a prior run's fallback), click its
summary. Confirm the detail block now appears (context, or the fallback
sentence), where it previously showed nothing.

### Step 1.3 — commit

`fix: Recent Errors expansion shows context/fallback, not just detail — Slice 23 Task 1`

---

## Task 2 — Logs button

**Files:** `avg/api/routes_settings.py`, `tests/api/test_routes_settings.py`,
`web/src/api.ts`, `web/src/ErrorLog.tsx`

### Step 2.1 — backend route

Add the import. Replace:

```python
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
```

with:

```python
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
```

Add the route, directly after `get_health_report` (before the blank-line
section that follows it):

```python
@router.post("/api/health/open-logs")
async def open_logs_folder(request: Request) -> dict:
    """Reveals the folder holding `errors.json` in Explorer - the App
    Health screen's Logs button. Same `os.startfile` + pytest-skip +
    swallow-failures shape as `avg.export.capcut.export_take`'s existing
    Explorer reveal, not a new one - opens the containing folder, not the
    file itself, for the same reason: `os.startfile` on a `.json` file
    launches whatever program Windows associates with that extension, not
    Explorer.

    Creates the folder first if it doesn't exist yet - a fresh install
    with no recorded errors has never had a reason to create it, and a
    button that silently does nothing on a clean install is exactly the
    failure shape Task 1 of this slice fixes for Recent Errors.
    """
    folder = request.app.state.error_store.path.parent
    folder.mkdir(parents=True, exist_ok=True)
    if "PYTEST_CURRENT_TEST" not in os.environ:
        with suppress(Exception):
            os.startfile(folder)  # type: ignore[attr-defined]  # Windows-only, by design
    return {"opened": str(folder)}
```

### Step 2.2 — backend tests

In `tests/api/test_routes_settings.py`, append after
`test_health_report_checks_the_isolated_paths_not_the_real_profile`:

```python
def test_open_logs_folder_creates_it_and_needs_a_token(client):
    from avg.config.user_assets import default_errors_path

    assert client.post("/api/health/open-logs").status_code == 401

    r = client.post("/api/health/open-logs", headers=AUTH)
    assert r.status_code == 200
    opened = Path(r.json()["opened"])
    assert opened.is_dir()
    assert opened == default_errors_path().parent


def test_open_logs_folder_does_not_open_explorer_under_pytest(client, monkeypatch):
    """Same guard, same test technique as `capcut.py`'s existing
    `test_export_does_not_open_explorer_under_pytest` - a spy on
    `os.startfile` proves the route never calls it during the suite,
    which just checking for a 200 response cannot: `suppress(Exception)`
    would swallow a broken guard's `AttributeError` on this non-Windows
    sandbox exactly as quietly as a working guard's absence of a call,
    making the two indistinguishable without a spy.
    """
    import os

    calls = []
    # os.startfile doesn't exist on non-Windows Pythons; add it if absent
    # so the spy can be installed uniformly regardless of platform -
    # matching test_capcut.py's own handling of the same situation.
    if not hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(os, "startfile", lambda *a, **k: calls.append(a), raising=False)
    assert "PYTEST_CURRENT_TEST" in os.environ

    r = client.post("/api/health/open-logs", headers=AUTH)

    assert r.status_code == 200
    assert calls == []
```

### Step 2.3 — frontend client function

In `web/src/api.ts`, replace:

```typescript
export function clearErrors(): Promise<{ cleared: boolean }> {
  return apiDelete<{ cleared: boolean }>("/api/errors");
}
```

with:

```typescript
export function clearErrors(): Promise<{ cleared: boolean }> {
  return apiDelete<{ cleared: boolean }>("/api/errors");
}

export function openLogsFolder(): Promise<{ opened: string }> {
  return apiPost<{ opened: string }>("/api/health/open-logs");
}
```

### Step 2.4 — frontend button

In `web/src/ErrorLog.tsx`, add the import. Replace:

```typescript
import { clearErrors, getErrors, type AppError } from "./api";
```

with:

```typescript
import { clearErrors, getErrors, openLogsFolder, type AppError } from "./api";
```

Add the button. Replace:

```tsx
        <button
          type="button"
          className="btn"
          disabled={!errors || errors.length === 0}
          onClick={() => void clearErrors().then(load)}
        >
          Clear
        </button>
      </header>
```

with:

```tsx
        <button type="button" className="btn btn--sm" onClick={() => void openLogsFolder()}>
          Logs
        </button>
        <button
          type="button"
          className="btn"
          disabled={!errors || errors.length === 0}
          onClick={() => void clearErrors().then(load)}
        >
          Clear
        </button>
      </header>
```

### Step 2.5 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_settings.py -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

**Manual check:** click Logs. Explorer opens showing the folder
containing `errors.json` (Windows only - `os.startfile` has no effect to
observe on the Linux sandbox this was implemented in, only the folder
creation and the 200 response are checkable there).

### Step 2.6 — commit

`feat: Logs button reveals errors.json's folder — Slice 23 Task 2`

---

## Task 3 — Collapsible App Health sections

**Files:** `web/src/AppHealth.tsx`, `web/src/tokens.css`

### Step 3.1 — implement

In `web/src/AppHealth.tsx`, add the import. Replace:

```typescript
import { useCallback, useEffect, useState } from "react";
import { getHealthReport, type HealthReport } from "./api";
import ErrorLog from "./ErrorLog";
import ModelCacheCard from "./ModelCacheCard";
```

with:

```typescript
import { useCallback, useEffect, useState } from "react";
import { getHealthReport, type HealthReport } from "./api";
import ErrorLog from "./ErrorLog";
import { IconChevron } from "./Icons";
import ModelCacheCard from "./ModelCacheCard";
```

Add the open/collapsed state and its sync effect. Replace:

```typescript
  const failing = report?.stages.filter((s) => !s.ok) ?? [];
```

with:

```typescript
  const failing = report?.stages.filter((s) => !s.ok) ?? [];

  // Passing stages start collapsed - "Passed" in the header chip is
  // usually the whole answer, which is the entire reason this exists
  // (the sections "currently consume too much space"). Failing stages
  // start open - a failed check is this screen's content, not an error
  // state to click past. A user's own toggle, once made, is never
  // overridden by a later report unless it's a stage key seen for the
  // first time.
  const [openStages, setOpenStages] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!report) return;
    setOpenStages((prev) => {
      const next = { ...prev };
      for (const stage of report.stages) {
        if (!(stage.key in next)) next[stage.key] = !stage.ok;
      }
      return next;
    });
  }, [report]);
  const toggleStage = (key: string) => setOpenStages((prev) => ({ ...prev, [key]: !prev[key] }));
```

Replace the stage rendering:

```tsx
        {report?.stages.map((stage, index) => (
          <section key={stage.key} className={`health-stage${stage.ok ? "" : " health-stage--bad"}`}>
            <header className="health-stage__head">
              {/* The step number carries the ordering the stages actually
                  have - this is a pipeline, not three independent panels. */}
              <span className="health-stage__num" aria-hidden="true">
                {index + 1}
              </span>
              <div style={{ minWidth: 0 }}>
                <h2 className="health-stage__title">{stage.name}</h2>
                <p className="health-stage__detail">{stage.detail}</p>
              </div>
              <span className="home__spacer" />
              <span className={stage.ok ? "chip chip--ok" : "chip chip--bad"}>
                {stage.ok ? "Passed" : "Failed"}
              </span>
            </header>

            <ul className="health-checks">
              {stage.checks.map((check) => (
                <li key={check.name} className="health-check">
                  <span className={`health-check__dot${check.ok ? " health-check__dot--ok" : " health-check__dot--bad"}`} aria-hidden="true" />
                  <span className="health-check__name">{check.name}</span>
                  {check.required === false && (
                    <span className="health-check__optional">Optional</span>
                  )}
                  {check.version && <span className="health-check__version mono">{check.version}</span>}
                  <span className="health-check__detail" title={check.detail}>
                    {check.detail}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
```

with:

```tsx
        {report?.stages.map((stage, index) => {
          const isOpen = openStages[stage.key] ?? !stage.ok;
          return (
            <section key={stage.key} className={`health-stage${stage.ok ? "" : " health-stage--bad"}`}>
              <header className="health-stage__head">
                {/* The step number carries the ordering the stages actually
                    have - this is a pipeline, not three independent panels. */}
                <span className="health-stage__num" aria-hidden="true">
                  {index + 1}
                </span>
                <div style={{ minWidth: 0 }}>
                  <h2 className="health-stage__title">{stage.name}</h2>
                  <p className="health-stage__detail">{stage.detail}</p>
                </div>
                <span className="home__spacer" />
                <span className={stage.ok ? "chip chip--ok" : "chip chip--bad"}>
                  {stage.ok ? "Passed" : "Failed"}
                </span>
                <button
                  type="button"
                  className={`health-stage__chevron${isOpen ? " is-open" : ""}`}
                  aria-expanded={isOpen}
                  aria-label={isOpen ? `Collapse ${stage.name}` : `Expand ${stage.name}`}
                  onClick={() => toggleStage(stage.key)}
                >
                  <IconChevron />
                </button>
              </header>

              <ul className={`health-checks${isOpen ? "" : " health-checks--collapsed"}`}>
                {stage.checks.map((check) => (
                  <li key={check.name} className="health-check">
                    <span className={`health-check__dot${check.ok ? " health-check__dot--ok" : " health-check__dot--bad"}`} aria-hidden="true" />
                    <span className="health-check__name">{check.name}</span>
                    {check.required === false && (
                      <span className="health-check__optional">Optional</span>
                    )}
                    {check.version && <span className="health-check__version mono">{check.version}</span>}
                    <span className="health-check__detail" title={check.detail}>
                      {check.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
```

### Step 3.2 — CSS

In `web/src/tokens.css`, replace:

```css
.health-checks {
  list-style: none;
  margin: 0;
  padding: 0;
}
```

with:

```css
.health-checks {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow: hidden;
  max-height: 1200px;
  opacity: 1;
  transition:
    max-height calc(0.24s * var(--motion-scale)) ease,
    opacity calc(0.18s * var(--motion-scale)) ease;
}

/* 1200px is comfortably above any real stage's check count - this only
   needs to be "large enough", the transition times against the actual
   rendered height either way. */
.health-checks--collapsed {
  max-height: 0;
  opacity: 0;
}
```

Add the chevron, modeled on `.statuspill__chevron` (Slice 17) - same
button shape, same rotate-on-`.is-open`, not a new pattern. Insert
directly after `.health-stage__detail`'s rule:

```css
.health-stage__chevron {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-left: 8px;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: transform calc(0.18s * var(--motion-scale)) ease-out;
}

.health-stage__chevron:hover {
  background: var(--surface-2);
}

.health-stage__chevron.is-open {
  transform: rotate(180deg);
}
```

### Step 3.3 — verify

```
cd web && npm run build && npm run lint
```

**Manual check:** open App Health. A stage with everything passing starts
collapsed (its "Passed" chip visible, its check list not); a stage with a
failure starts open. Clicking the chevron smoothly toggles either one; a
manual toggle survives clicking Re-run everything (as long as the same
stage keys come back, which they always do - three fixed stages).

### Step 3.4 — commit

`feat: collapsible App Health stage sections — Slice 23 Task 3`

---

## Task 4 — Full regression, update progress ledger

### Step 4.1

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

### Step 4.2

Update `.superpowers/sdd/progress.md`: Slice 23 complete, all three items
noted, results recorded. This closes out the 20-23 batch
(`docs/reference/slice 20+ prompt.txt`'s items #1, #21+#7, #18, #3+#15+#20)
- a natural point to note that explicitly in the ledger too.

### Step 4.3 — commit

`docs: Slice 23 complete — App Health (Logs, collapsible sections, Recent Errors bug)`
