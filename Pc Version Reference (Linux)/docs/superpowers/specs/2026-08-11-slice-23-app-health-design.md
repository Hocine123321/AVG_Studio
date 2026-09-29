# Slice 23 — App Health: Logs button, collapsible sections, Recent Errors bug: Design

**Goal:** three App Health items, all on the same screen
(`web/src/AppHealth.tsx` and its `ErrorLog.tsx` child): a working
disclosure on Recent Errors, collapsible diagnostic-stage sections, and a
Logs button that reveals `errors.json` in Explorer.

Covers backlog items #3, #15, #20.

---

## 1. Recent Errors expansion bug (#20) — root cause traced, confirmed

**Not a toggle bug, not a CSS bug — an empty-data bug.** Read
`ErrorLog.tsx`'s click handler, the `flex-basis: 100%` CSS that makes the
detail block appear on its own line beneath the row, and the `/api/errors`
contract closely; all three are correct. Confirmed against the actual
symptom (asked: "nothing happens at all when I click" - not "wrong row
opens", not "opens then closes").

The render condition is `{expanded === error.id && error.detail && (<pre>...)}`.
`error.detail` is empty for **every** `low`-severity error:
`avg.core.error_store.ErrorStore.degraded()` - the method every
`report_degradation()` call goes through, across every phase, and per
that method's own docstring "a fallback that rescued a run" is exactly
the common case this screen is designed to surface without it crowding
out real failures - hardcodes `detail=""`. `report_degradation`'s own
signature doesn't even accept a detail argument. So: click toggles
`expanded` correctly every time; the `<pre>` only ever renders for the
`critical`/`needs_review` errors that happen to carry a populated
`detail` (real exceptions, tracebacks). For every degradation - a
meaningful fraction of what this screen shows, and more after Slice 21
adds new `report_degradation` call sites - the click is, correctly by the
code's own logic, and yet indistinguishable from broken.

**Fix:** the expanded block stops gating on `error.detail` alone. It
shows, in order: `error.detail` if present; otherwise `error.context`
formatted as a list (already computed - it's the same object
`promptFor()` already puts in the clipboard for Copy, currently never
shown on screen at all); otherwise a plain "No further detail recorded
for this error." A click always produces something visible.

## 2. Logs button (#3)

New `.btn.btn--sm` "Logs" button in `ErrorLog`'s header, beside Clear -
secondary, per the requirement, next to the screen's one primary action
("Re-run everything", unchanged). Backend: `POST /api/health/open-logs`,
`os.startfile()` on the *folder* containing `errors.json`
(`avg.config.user_assets.default_errors_path().parent` - Slice 20's
location), not the file itself - matches `avg.export.capcut.export_take`'s
existing precedent exactly (same `os.startfile`, same
`PYTEST_CURRENT_TEST` skip-under-test guard, same `suppress(Exception)`),
which opens a folder in Explorer rather than a single file in whatever
program Windows associates with `.json`. Creates the folder first if it
doesn't exist yet (a fresh install with zero recorded errors has never
had a reason to create `Logs/` before this) - otherwise the button would
silently do nothing on a clean install, the same failure shape as §1.

No new logging viewer: this reveals the existing file in the existing OS
file manager, nothing renders inside the app.

## 3. Collapsible App Health sections (#15)

Each of the three stage `<section>`s (Dependency check, App warmup,
Safeguards) gets its own expand/collapse state, keyed by `stage.key`
(matches how `RecentErrorsTab`/`ErrorLog` key their own per-row disclosure
state - `Set<string>` of collapsed keys, not three separate booleans, so
a fourth stage added later needs no new state variable).

**Default state, not arbitrary:** a passing stage collapses by default -
"Passed" in the header chip is the whole answer most of the time, which
is the entire reason this item exists ("currently consume too much
space"). A **failing** stage stays expanded by default - this screen's
own stated philosophy ("a failed check is never an error state for the
screen - it is the content") means the failure detail shouldn't cost an
extra click to see. Toggling is still always available either way.

Chevron button reuses `IconChevron` and the rotate-on-`is-open` pattern
`StatusBar.tsx`'s own disclosure already established
(`.statuspill__chevron`/`is-open`), not a new one. Collapsed state hides
`.health-checks` (the per-check list); the header - number, name, detail
line, Passed/Failed chip - stays visible either way, since that's the
"concise summary" the collapsed state is supposed to be.

## 4. Testing

Backend: `POST /api/health/open-logs` - folder gets created if missing;
`os.startfile` call skipped under `PYTEST_CURRENT_TEST` (matching
`capcut.py`'s existing test-safety pattern exactly, not a new one).

Frontend: no test runner exists in this repo (confirmed in Slice 22) -
`tsc -b` + `oxlint` clean, plus a documented manual check for each of the
three items.

## 5. Out of scope

- Any change to how `report_degradation`/`degraded()` decide severity or
  whether to start attaching a `detail` value - the fix is entirely in
  what the UI does with an empty one, not in generating richer data
  server-side.
- Extracting `capcut.py`'s inline `os.startfile` call into a shared
  helper - the same operation now exists in two places (there un-touched,
  here new), which is worth revisiting when a third place needs it
  (My Videos' "Open Folder", a later slice), not speculatively now.
