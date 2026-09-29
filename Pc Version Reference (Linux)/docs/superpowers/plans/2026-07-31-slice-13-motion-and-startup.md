# Slice 13 — Motion and startup: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the status pill read as one continuously moving thing rather than a label that snaps between words, tell the user something is happening during the seconds before the library appears, and take the one piece of startup cost that is genuinely ours off the critical path.

**Architecture:** Four tasks. Two backend (one deferred import, one setting wired to the shell), two frontend (the pill's states and transitions, and a startup indicator). Nothing changes the phase registry, the settings catalogue's role, or what a phase writes.

**Tech Stack:** Python 3.12 + FastAPI + pywebview (backend), React 19 + TypeScript + Vite (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-13-motion-and-startup-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** Tests: `.venv-build/Scripts/python.exe -m pytest tests -q`. Types: `.venv-build/Scripts/python.exe -m mypy`.
- **Never go below the suite's count when you start.** Slices 11 and 12 raise it; whatever it reports at Task 1, that is your floor.
- **mypy baseline is `Found 688 errors in 22 files`.** mypy does NOT pass clean here; add nothing to the count.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1`.
- **Tests must never write to the real `%APPDATA%/AVG Studio`.** Pass `create_app`'s path overrides.
- **Nothing below `avg/api/` may print or prompt.** `avg/shell.py` is the one exception — it is the outermost layer and the window is its only surface.
- **`avg/media/vendor/` is protected.**
- **Never use a native form control**; never add a Google Fonts `@import`.
- **One filled terracotta `.btn--primary` primary action per screen.** The pill and the startup bar are status, not actions — neither is ever `.btn--primary`.
- **Reuse `web/src/tokens.css` values** — never a fresh guess.
- **`prefers-reduced-motion` always wins.** `motion_scale` (Slice 12) can only reduce motion below the OS preference, never restore it. The existing reduced-motion block in `tokens.css` stays authoritative.
- **The frontend has no unit tests by convention.** Frontend tasks are verified by build, lint, computed-style measurement, and reasoning stated in the report.
- **Match code by content, not by line number.**
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

**Dependency:** Task 4 wires `startup_check`, which **Slice 12 Task 2 declares**. If Slice 12 has not been executed, skip Task 4 and say so in your report — do not invent the setting here.

---

## Task 1: Take `legacy_import` off the startup path

**Files:**
- Modify: `avg/api/app.py` (remove the module-scope import; import inside each of its three call sites)
- Test: `tests/core/test_startup_cost.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `avg.api.app` no longer has a module-level `import_all` attribute.

**Why:** Measured 2026-07-31 with `python -X importtime` on the boot path (`import uvicorn; from avg.api.app import create_app`):

| Module | Cumulative |
|---|---|
| `avg.api.app` | **3.05 s** |
| ↳ `fastapi` | 2.06 s (`fastapi.exceptions` 0.73 s self, `fastapi.openapi.models` 0.41 s self) |
| ↳ `uvicorn` | 1.21 s |
| ↳ **`avg.config.legacy_import`** | **0.50 s** |

`avg/api/app.py:46` does `from avg.config.legacy_import import import_all` at module scope. That module imports `avg.pipeline.registry`, which imports every phase module, which imports the provider and media layers — the whole pipeline pulled into startup for a library scan `create_app` then runs **on a background thread anyway**.

`import_all` has exactly three call sites: `app.py:482` (the scan), `:946` and `:960` (the `/api/settings/scan` route). All three can import it locally.

**The remaining ~2.5 s is `fastapi` and `uvicorn` and is deliberately not addressed** — those imports are what the server is, and disabling the docs routes does not prevent `fastapi.openapi.models` loading.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_startup_cost.py`:

```python
def test_the_api_module_does_not_pull_the_pipeline_at_import_time():
    """`avg.config.legacy_import` costs ~0.50s because it transitively
    imports `avg.pipeline.registry` and therefore every phase, provider and
    media module. The library scan it serves runs on a background thread, so
    that cost has no business being on the startup path.

    Same seam Slice 10 asserted for `avg.shell` and uvicorn.
    """
    import avg.api.app as app_module

    assert not hasattr(app_module, "import_all")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_startup_cost.py -q
```

Expected: FAIL — `assert not hasattr(app_module, 'import_all')`.

- [ ] **Step 3: Defer the import**

In `avg/api/app.py`, delete the module-scope line:

```python
from avg.config.legacy_import import import_all
```

At each of the three call sites, add the import immediately above the use, with the reason:

```python
        # Imported here, not at module scope: `avg.config.legacy_import`
        # transitively pulls `avg.pipeline.registry` and so every phase,
        # provider and media module - ~0.50s measured with `-X importtime`.
        # This scan already runs on a background thread, so that cost belongs
        # on the thread, not on the path to the first window.
        from avg.config.legacy_import import import_all  # noqa: PLC0415
```

Find them by searching for `import_all(` — there are three. The comment above belongs on the first (the background scan); the other two can carry a one-line version:

```python
        from avg.config.legacy_import import import_all  # noqa: PLC0415 - see the scan worker
```

- [ ] **Step 4: Run the test**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_startup_cost.py -q
```

Expected: PASS.

- [ ] **Step 5: Measure the improvement**

```bash
.venv-build/Scripts/python.exe -X importtime -c "import uvicorn; from avg.api.app import create_app" 2>&1 | sort -t'|' -k2 -rn | head -5
```

Record `avg.api.app`'s cumulative figure before and after in your report. Expect it to fall by roughly the 0.50 s `legacy_import` costs. Do **not** assert a timing in a test — a wall-clock assertion in a suite is a flake waiting to happen.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: one more than your starting count, zero failures. The library scan and `/api/settings/scan` must still work — existing tests cover both; if either breaks, the import moved to the wrong scope.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/api/app.py tests/core/test_startup_cost.py
git commit -m "perf: keep the pipeline off the startup path"
```

---

## Task 2: The pill's three states, and the transitions between them

**Files:**
- Modify: `web/src/StatusBar.tsx`
- Modify: `web/src/tokens.css` (the `.statuspill*` block)

**Interfaces:**
- Consumes: `IconCheck` from `web/src/Icons.tsx` — signature `IconCheck({ size = 13 })`.
- Consumes from Slice 12 (optional): `--motion-scale`. If Slice 12 has not landed, the property is simply undefined and `var(--motion-scale, 1)` falls back to `1`.
- Produces: nothing exported.

**Why:** Three defects in one component. The ring appears only for `high`-priority activity, so changing screens — the most common activity in the app — looks identical to idle. The pill's width changes with no transition, so it jumps. And idle spends a full pill saying `Ready!`.

**The idle mark is a drawn `IconCheck`, not the `✅` character.** An emoji glyph is drawn by the operating system in its own font and colours — the same class of thing as the native `<select>` that `CLAUDE.md` bans outright, and for the same reason: it cannot obey the theme.

**Ring on any activity, gated by time not priority.** Slice 10 gated on `priority === "high"` to stop a 40 ms GET flashing a spinner. That reasoning was about flicker, and the fix for flicker is a delay: the ring appears for any activity in flight longer than 150 ms.

- [ ] **Step 1: Rewrite the component body**

In `web/src/StatusBar.tsx`, add to the imports:

```tsx
import { IconCheck } from "./Icons";
```

Replace everything from `const lead = leadActivity(activities);` to the end of the component:

```tsx
  const lead = leadActivity(activities);

  // The ring is gated on TIME, not on priority. Slice 10 gated it on
  // `priority === "high"` so a 40ms GET would not flash a spinner - but that
  // reasoning was about flicker, and the fix for flicker is a delay. A slow
  // screen change deserves the ring exactly as much as a render does; a fast
  // one still shows nothing.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    if (lead === null) {
      setSettled(false);
      return;
    }
    const id = window.setTimeout(() => setSettled(true), RING_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [lead?.id]);

  const kind = !connected ? "error" : lead !== null ? "busy" : "ready";
  const working = lead !== null && settled;
  const label = kind === "error" ? "Reconnecting" : lead !== null ? lead.label : "";

  return (
    <div
      className={`statuspill statuspill--${kind}`}
      role="status"
      aria-live="polite"
      // Idle drops the visible word, and the mark itself is `aria-hidden`,
      // so without this the pill would announce nothing at all to a screen
      // reader at rest. The visible text carries it in every other state.
      aria-label={kind === "ready" && !working ? "Ready" : undefined}
    >
      {working ? (
        <span className="statuspill__ring" aria-hidden="true" />
      ) : kind === "ready" ? (
        // A drawn mark, never the ✅ character: an emoji is rendered by the
        // OS in its own font and colours and is incapable of obeying the
        // theme - the same objection that bans the native <select>.
        <span className="statuspill__check" aria-hidden="true">
          <IconCheck size={12} />
        </span>
      ) : (
        <span className="statuspill__dot" aria-hidden="true" />
      )}
      {label !== "" && (
        // Keyed so the label replays its entry each time the pill changes
        // between resting and working, rather than swapping text in place.
        <span className="statuspill__label" key={working ? "working" : "resting"}>
          {label}
          {lead !== null && "…"}
        </span>
      )}
      {lead !== null && <span className="statuspill__clock mono">{elapsedClock(lead.startedAt, now)}</span>}
      {activities.length > 1 && <span className="statuspill__more">+{activities.length - 1}</span>}
    </div>
  );
}
```

Add the constant above the component:

```tsx
/** How long an activity must be in flight before the pill shows a ring.
 * Below this the work is over before a spinner would mean anything, and
 * showing one makes the pill cry wolf. */
const RING_DELAY_MS = 150;
```

Idle now renders **only** the check — no word. That is what makes the pill small when nothing is happening, which is the point.

- [ ] **Step 2: Animate the pill's shape and contents**

In `web/src/tokens.css`, in the `.statuspill` rule add a width/colour transition:

```css
  /* The pill changes size as its contents change; without this it jumps.
     `--motion-scale` (Slice 12) and the reduced-motion block below both
     govern it, so the whole pill's motion is decided in two places, not
     twelve. */
  transition:
    border-color calc(0.2s * var(--motion-scale, 1)) ease-out,
    background-color calc(0.2s * var(--motion-scale, 1)) ease-out,
    color calc(0.2s * var(--motion-scale, 1)) ease-out;
```

Add the check's own rule beside `.statuspill__dot`:

```css
/* The resting mark. Same optical weight as the dot it replaces, sage
   because sage means settled everywhere else in this app. */
.statuspill__check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  flex-shrink: 0;
  color: var(--sage);
}
```

Scale the existing ring and label animations by the property:

```css
.statuspill__ring {
  animation: avg-spin calc(0.9s / max(var(--motion-scale, 1), 0.01)) linear infinite;
}

.statuspill__label {
  animation: pill-pop calc(0.28s * var(--motion-scale, 1)) ease-out;
}
```

`max(..., 0.01)` guards the infinite animation: a scale of `0` would otherwise ask for a zero-duration infinite spin.

Confirm the existing reduced-motion block still lists `.statuspill__dot`, `.statuspill__ring` and `.statuspill__label`, and add the pill's own transition to it:

```css
@media (prefers-reduced-motion: reduce) {
  .statuspill {
    transition: none;
  }

  .statuspill__dot,
  .statuspill__ring,
  .statuspill__label {
    animation: none;
  }
}
```

- [ ] **Step 3: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 4: Verify the states by measurement**

Serve the app (`.venv-build/Scripts/python.exe tools/_dev_serve.py`, kill anything on 8756 first) and open `http://127.0.0.1:8756/?token=devtoken`. Using `javascript_tool`, report:

- at rest: that `.statuspill__check` exists and `.statuspill__label` does not;
- during a screen change: whether `.statuspill__ring` appears;
- the computed `animation-duration` of `.statuspill__ring` while it is present.

State in your report whether a request that resolves in under 150 ms produced a ring — drive one by re-fetching a small endpoint if you need to.

- [ ] **Step 5: Commit**

```bash
git add web/src/StatusBar.tsx web/src/tokens.css
git commit -m "feat: the status pill rests on a mark and rings for any real work"
```

---

## Task 3: A loading bar until the library is ready

**Files:**
- Modify: `web/src/App.tsx` (pass a flag to `StatusBar`)
- Modify: `web/src/StatusBar.tsx` (render the bar instead of the pill)
- Modify: `web/src/tokens.css` (the bar's rules)

**Interfaces:**
- Consumes from Task 2: the pill's markup.
- Produces: `StatusBar` gains a `loading?: boolean` prop.

**Why:** `avg/shell.py` points the window at the real URL once `/api/settings/schema` answers — deliberately, because the schema is a constant while `GET /api/projects` waits on the startup library scan. So React mounts and asks for a project list that is not ready. During that window the app is up, the pill says everything is fine, and the library is empty.

The signal already exists: `App.tsx` holds `projects` as `null` until that first response. No new state, no new endpoint, nothing polls.

**This is not the `avg/shell.py` splash** — that covers "the server does not exist yet". This covers "the server is up and the library is still being counted".

- [ ] **Step 1: Pass the flag**

In `web/src/App.tsx`, find the `<StatusBar connected={events.connected} />` usage and change it to:

```tsx
        <StatusBar connected={events.connected} loading={projects === null} />
```

`projects` is already `null` until the first `GET /api/projects` resolves — confirm that by reading its declaration before you change anything.

- [ ] **Step 2: Render the bar**

In `web/src/StatusBar.tsx`, change the signature:

```tsx
export default function StatusBar({ connected, loading = false }: { connected: boolean; loading?: boolean }) {
```

Immediately before the existing `return`, add:

```tsx
  // Until the library has ever answered, the pill has nothing true to say -
  // the server is up, so it would read "ready" while the screen is still
  // empty. A bar in the pill's own slot says "still counting" instead.
  // `avg/shell.py`'s splash covers the earlier moment, before the server
  // exists at all; this is the one after it.
  if (loading) {
    return (
      <div
        className="statuspill statuspill--loading"
        role="status"
        aria-live="polite"
        aria-label="Loading your library"
      >
        <span className="statusbar-load" aria-hidden="true">
          <span className="statusbar-load__fill" />
        </span>
      </div>
    );
  }
```

- [ ] **Step 3: Style it**

In `web/src/tokens.css`, beside the other `.statuspill` rules:

```css
.statuspill--loading {
  --pill: var(--accent);
  padding: 10px 16px;
}

/* An indeterminate bar, in the pill's own slot, while the library is still
   being counted. Deliberately slim and slow - it reports, it does not
   demand attention. */
.statusbar-load {
  display: block;
  width: 76px;
  height: 3px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--pill) 24%, transparent);
  overflow: hidden;
}

.statusbar-load__fill {
  display: block;
  width: 40%;
  height: 100%;
  border-radius: inherit;
  background: var(--pill);
  animation: statusbar-sweep calc(1.3s / max(var(--motion-scale, 1), 0.01)) ease-in-out infinite;
}

@keyframes statusbar-sweep {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(250%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .statusbar-load__fill {
    animation: none;
    width: 100%;
  }
}
```

Under reduced motion the bar becomes a full static line rather than disappearing — the state still needs to be visible, it simply stops moving.

- [ ] **Step 4: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 5: Verify it appears and resolves**

Serve the app and reload with the network throttled if you can, or add a temporary delay to the library scan. Using `javascript_tool`, report whether `.statusbar-load` is present immediately after load and absent once the project grid renders. If you added a temporary delay, **remove it before committing** and say so in your report.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/StatusBar.tsx web/src/tokens.css
git commit -m "feat: a loading bar while the library is still being counted"
```

---

## Task 4: Wire `startup_check`

**Files:**
- Modify: `avg/shell.py` (`_boot`)
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes from **Slice 12 Task 2**: the `startup_check` setting (`scope="app"`, bool, default `true`).
- Produces: `avg.shell.should_check_at_startup(values: dict[str, Any]) -> bool`.

**Why:** Slice 12 declares `startup_check` and names `avg/shell.py` as its reader. `settings_schema.py:79-93` records three settings deleted on 2026-07-28 because the UI saved them and no Python read them — a declared-but-unread setting is that mistake repeating. This task is what stops it.

**If Slice 12 has not been executed, skip this task entirely and say so in your report.** Do not add the setting here; it belongs to that slice's catalogue work.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell.py`:

```python
def test_startup_check_defaults_to_on_and_can_be_turned_off():
    """A setting the UI saves and nothing reads is the mistake
    `settings_schema.py` records three of. This is the reader."""
    from avg.shell import should_check_at_startup

    assert should_check_at_startup({}) is True
    assert should_check_at_startup({"startup_check": True}) is True
    assert should_check_at_startup({"startup_check": False}) is False
    # A junk value is not a reason to skip a health check.
    assert should_check_at_startup({"startup_check": "yes please"}) is True


def test_reading_the_setting_needs_no_server_and_no_app():
    """Same property the splash has: this runs on the boot path before the
    server exists, so it must not need one."""
    from avg.shell import should_check_at_startup

    assert should_check_at_startup({"startup_check": False}) is False
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/test_shell.py -q
```

Expected: FAIL — `cannot import name 'should_check_at_startup'`.

- [ ] **Step 3: Add the reader**

In `avg/shell.py`, add beside `splash_html`:

```python
def should_check_at_startup(values: dict[str, Any]) -> bool:
    """Whether to warm the component check while the library loads.

    Reads the `startup_check` app setting (`avg.core.settings_schema`).
    Anything that is not exactly `False` means yes: a malformed stored value
    is not a reason to silently skip a diagnostic, and the setting exists to
    save a moment, not to hide a problem.

    A pure function of the values dict so it can be called on the boot path
    without a server, an app instance, or a settings file - the same property
    `splash_html` has, and for the same reason.
    """
    return values.get("startup_check", True) is not False
```

Add `from typing import Any` to the imports if it is not already there.

- [ ] **Step 4: Use it**

In `avg/shell.py`'s `_boot`, after `create_app` has been built and the server is answering, warm the check only when asked:

```python
            from avg.config.settings import load_settings  # noqa: PLC0415

            if should_check_at_startup(load_settings().values):
                # Warms the five-minute component cache on the boot thread so
                # App health answers instantly the first time it is opened.
                # Never blocks the window: this runs after `load_url`.
                from avg.core.health import check_all  # noqa: PLC0415

                check_all()
```

Place this **after** `window.load_url(...)`, inside the same `try`, so a failure here can never stop the app appearing — and so the check cost is behind the UI rather than in front of it.

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/test_shell.py -q
```

Expected: PASS.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: two more than after Task 3.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/shell.py tests/test_shell.py
git commit -m "feat: startup_check is read, not just stored"
```

---

## Final verification

Run after Task 4. Not a task — no commit unless something needs fixing.

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — never below your starting count; expect 4 new tests (3 if Task 4 was skipped).
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Boot-path import time re-measured and recorded in `.superpowers/sdd/progress.md`, before and after — `avg.api.app` cumulative should fall by roughly 0.50 s from the 3.05 s measured on 2026-07-31.
- [ ] Idle: the pill is small and shows a sage check, with **no word** beside it.
- [ ] Change screens: the ring appears and the label names the work. A request under 150 ms shows no ring at all.
- [ ] Start a render: the ring keeps spinning and the label reads `Working`.
- [ ] Watch idle → busy → idle: the pill's width animates and the contents change without jumping.
- [ ] Set `motion_scale` to `reduced`, then set the OS to prefer reduced motion: in both cases the pill still changes state correctly, just without the motion, and **the OS preference wins over the setting**.
- [ ] Launch cold and watch the pill's slot: a loading bar until the library arrives, then the pill.
- [ ] Turn `startup_check` off, relaunch, and confirm App health still reports correctly when opened — it simply pays for the check then rather than at startup.
- [ ] `"Build Installer.bat"` — the same behaviour in `AVG APP/`.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 Pill states: check, ring, dot | 2 |
| §3.1 Ring on any activity, 150 ms delay | 2 |
| §3.1 Width and content transitions | 2 |
| §3.2 Loading bar until the library is ready | 3 |
| §3.3 One deferred import | 1 |
| §3.3 Framework cost documented, not fought | 1 (stated in the task's Why) |
| §4 Testing | 1, 4 |
| §5 Verification | Final verification |

**`startup_check`** is declared by Slice 12 and wired by Task 4 here. If the two slices are executed out of order, the Global Constraints note at the top of this plan governs: a declared setting that nothing reads must not ship.
