# Slice 22 — Progress bar phase-transition bug: Implementation Plan

Implements `docs/superpowers/specs/2026-08-11-slice-22-progress-bar-design.md`.
Read that first.

No `pytest`/`mypy` involved - this slice is entirely frontend. Verify with
`cd web && npm run build && npm run lint`. No frontend test runner exists
in this repo (confirmed - `web/package.json` has no `vitest`/`jest`), so
this plan has no red/green test cycle; Task 1's step 3 is a documented
manual check instead.

## Files touched

| File | Change |
|---|---|
| `web/src/useEvents.ts` | `foldByProject` gains a `phase_started` branch; import `PHASE_DEFS` |

One file, one function. One task.

---

## Task 1 — `foldByProject` resets on `phase_started`

**Files:** `web/src/useEvents.ts`

### Step 1.1 — implement

Add the import. Replace:

```typescript
import { useCallback, useEffect, useState } from "react";
import { wsUrl } from "./api";
import { clearRenderClock } from "./renderClock";
```

with:

```typescript
import { useCallback, useEffect, useState } from "react";
import { wsUrl } from "./api";
import { PHASE_DEFS } from "./phases";
import { clearRenderClock } from "./renderClock";
```

Replace `foldByProject` in full:

```typescript
/**
 * Fold one event into the accumulated per-project progress map.
 *
 * A `progress` event writes (or overwrites) that project's entry. A terminal
 * event - the job finished, failed or was cancelled - deletes it, because a
 * finished job has no "latest progress" a consumer should keep showing; the
 * finished/cancelled/failed single-event fields are where that outcome lives
 * instead. Anything else (a phase transition, the socket's own "hello")
 * leaves the map untouched, for the same reason `foldPhases` does: several
 * jobs may be ticking at once and only their own `progress` events should
 * cause an allocation.
 */
function foldByProject(
  previous: EventsState["byProject"],
  payload: { type: string } & Record<string, unknown>,
): EventsState["byProject"] {
  const id = typeof payload.project_id === "string" ? payload.project_id : null;
  if (id === null) return previous;

  if (payload.type === "progress") {
    return { ...previous, [id]: payload as unknown as ProgressEvent };
  }
  if (
    payload.type === "render_finished" ||
    payload.type === "render_cancelled" ||
    payload.type === "pipeline_cancelled" ||
    payload.type === "phase_failed" ||
    // The genuine end of a successful full-pipeline run. `render_finished`
    // above fires from INSIDE the render phase, before its own composite
    // pass - whose progress events then recreate this project's entry a
    // moment later, which is why the Transport bar used to stick at
    // "Rendering... 100%" forever after every successful run. This is the
    // one event that cannot be followed by more progress for the same job.
    payload.type === "pipeline_finished"
  ) {
    if (!(id in previous)) return previous;
    const next = { ...previous };
    delete next[id];
    return next;
  }
  return previous;
}
```

with:

```typescript
/**
 * Fold one event into the accumulated per-project progress map.
 *
 * A `progress` event writes (or overwrites) that project's entry. A
 * `phase_started` event replaces it with a fresh zero-percent entry for
 * the new phase - without this, a project's entry keeps whatever
 * `percent` the PREVIOUS phase's last `progress` event left behind until
 * the new phase happens to emit its own first one, which can be a while;
 * from a consumer's point of view (Transport.tsx, mounted once, on every
 * screen) that's indistinguishable from the bar being stuck at 100%
 * between phases (Slice 22's bug report). Replacing rather than deleting
 * the entry matters: `Home.tsx`'s hero/stack requires a truthy entry to
 * show a job at all, so a bare delete here would make an actively
 * running job flicker out of the UI for the gap instead of just showing
 * a stale number. A terminal event - the job finished, failed or was
 * cancelled - still deletes the entry entirely, because a finished job
 * has no "latest progress" a consumer should keep showing; the
 * finished/cancelled/failed single-event fields are where that outcome
 * lives instead. Anything else (the socket's own "hello") leaves the map
 * untouched, for the same reason `foldPhases` does: several jobs may be
 * ticking at once and only their own events should cause an allocation.
 */
function foldByProject(
  previous: EventsState["byProject"],
  payload: { type: string } & Record<string, unknown>,
): EventsState["byProject"] {
  const id = typeof payload.project_id === "string" ? payload.project_id : null;
  if (id === null) return previous;

  if (payload.type === "progress") {
    return { ...previous, [id]: payload as unknown as ProgressEvent };
  }
  if (payload.type === "phase_started") {
    const phase = typeof payload.phase === "string" ? payload.phase : "";
    const label = PHASE_DEFS.find((def) => def.key === phase)?.label ?? phase;
    const message = label ? `Starting ${label}…` : "Starting…";
    return { ...previous, [id]: { type: "progress", project_id: id, percent: 0, message } };
  }
  if (
    payload.type === "render_finished" ||
    payload.type === "render_cancelled" ||
    payload.type === "pipeline_cancelled" ||
    payload.type === "phase_failed" ||
    // The genuine end of a successful full-pipeline run. `render_finished`
    // above fires from INSIDE the render phase, before its own composite
    // pass - whose progress events then recreate this project's entry a
    // moment later, which is why the Transport bar used to stick at
    // "Rendering... 100%" forever after every successful run. This is the
    // one event that cannot be followed by more progress for the same job.
    payload.type === "pipeline_finished"
  ) {
    if (!(id in previous)) return previous;
    const next = { ...previous };
    delete next[id];
    return next;
  }
  return previous;
}
```

### Step 1.2 — verify

```
cd web && npm run build && npm run lint
```

Both clean - this is a pure addition (one new branch), nothing about the
existing `progress`/terminal-event branches changed, so nothing else in
the file should need adjustment.

**Manual verification** (no automated coverage exists for this file - see
spec §2): start a pipeline run on a project with more than one phase
worth watching (anything past the first `[pause]`-free short script).
Watch the transport bar (bottom of every screen) as the run crosses from
one phase into the next. Confirm: it drops to a low/zero percentage and
shows "Starting `<Phase>`…" at the transition, rather than continuing to
show the previous phase's percentage frozen at 100%.

### Step 1.3 — commit

`fix: progress bar resets on phase transitions — Slice 22`

---

## Task 2 — Update progress ledger

### Step 2.1

Update `.superpowers/sdd/progress.md`: Slice 22 complete, root cause
(`foldByProject` never handled `phase_started`), fix, and that automated
coverage was explicitly skipped (no frontend test runner exists) in favor
of a documented manual check.

### Step 2.2 — commit

`docs: Slice 22 complete — progress bar phase-transition bug`
