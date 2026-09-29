# Slice 13 — Motion and startup

**Goal:** make the status pill read as one continuously moving thing rather
than a label that snaps between words, tell the user something is happening
during the seconds before the library appears, and take the one piece of
startup cost that is genuinely ours off the critical path.

**Date:** 2026-07-31. Every timing below was measured with
`python -X importtime` against `.venv-build` on that date.

Third slice of the 2026-07-31 backlog. It depends on Slice 12 only for
`motion_scale`; nothing else here needs another slice.

---

## 1. What the evidence shows

### 1.1 Startup is framework import, and one piece of it is ours

Measured on 2026-07-31, cumulative, for the boot path
(`import uvicorn; from avg.api.app import create_app`):

| Module | Cumulative | Notable self-cost |
|---|---|---|
| `avg.api.app` | **3.05 s** | — |
| ↳ `fastapi` | 2.06 s | `fastapi.exceptions` 0.73 s, `fastapi.openapi.models` 0.41 s |
| ↳ `uvicorn` | 1.21 s | `asyncio` 0.68 s |
| ↳ **`avg.config.legacy_import`** | **0.50 s** | ours |

Slice 10 already moved all of this off the *window*'s critical path — the
splash paints before any of it runs, and `import avg.shell` is now 0.76 s.
What remains is the wait between the splash appearing and the real UI
loading.

Most of it cannot be removed: `fastapi` and `uvicorn` are what the server
is. But `avg.config.legacy_import` is ours, and `avg/api/app.py:46` imports
`import_all` at module scope. That module pulls `avg.pipeline.registry`,
which imports every phase module, which imports the provider and media
layers — the entire pipeline, dragged into startup for a library scan that
`create_app` then runs **on a background thread anyway**.

### 1.2 The pill snaps; it does not move

`web/src/StatusBar.tsx` swaps its own content between three states. Slice 10
gave the busy state a rotating ring and the label a pop animation, but:

- The ring appears only for `high`-priority activity — a running render or
  pipeline. Changing screens, saving a setting or loading a list shows the
  static dot, so the most common activity in the app looks identical to
  idle.
- The pill's width changes as the label changes, with no transition, so it
  jumps.
- Idle reads the word `Ready!`, which spends a full pill's width saying
  nothing happened.

### 1.3 Nothing reports the gap before the library appears

`avg/shell.py` points the window at the real URL once
`/api/settings/schema` answers — deliberately, because the schema is a
constant while `GET /api/projects` waits on the startup library scan
(`app.state.library_loaded`). So the React app mounts and asks for a project
list that is not ready yet.

During that window the app is up, the pill says `Ready!`, and the library is
empty. The one moment the user most needs to be told "still working" is the
moment the pill claims everything is done.

---

## 2. Decisions

1. **Idle is a drawn mark, not an emoji glyph.** See §3.1.
2. **The ring means any activity**, not only a running job.
3. **The startup indicator lives in the pill's own slot**, replacing it
   until the library has loaded.
4. **One import is deferred; the framework cost is documented, not fought.**

---

## 3. Architecture

Three frontend pieces and one backend line. Nothing changes the phase
registry, the settings catalogue, or what a phase writes.

### 3.1 The pill's three states, and the transitions between them

| State | Mark | Label |
|---|---|---|
| Idle | a drawn check, sage | none — the mark is the whole message |
| Active | the rotating ring | the activity's own word (`Working`, `Saving`, `Loading`…) |
| Disconnected | the dot, red | `Reconnecting` |

**The idle mark is `IconCheck` from `web/src/Icons.tsx`, tinted with
`--sage`, not the `✅` character.** An emoji glyph is drawn by the operating
system in its own font and its own colours — the same class of thing as the
native `<select>` and `<input type=checkbox>` that `CLAUDE.md` bans outright,
and for exactly the same reason: it is incapable of obeying the theme. A
drawn icon carries the identical meaning and is ours to colour.

Dropping the idle label is what makes the pill small when nothing is
happening, which is the point of the request — the pill stops spending a
sentence to say nothing is wrong.

**Ring on any activity.** Slice 10 gated the ring on
`lead.priority === "high"` so that a 40 ms GET would not flash a spinner.
That reasoning was about *flicker*, not about priority, and the fix for
flicker is a delay, not a category: the ring appears for any activity that
has been in flight longer than **150 ms**, whatever its priority. A fast
request never shows one; a slow screen change does. The `high`/`medium`/`low`
ranking in `activity.ts` keeps its existing job of deciding *which* label to
show and is not otherwise touched.

**The transitions.** The pill animates its width and cross-fades its
contents between states, so the mark, the ring and the label exchange places
rather than snapping. Every duration is expressed as a multiple of one
custom property so `motion_scale` (Slice 12) and `prefers-reduced-motion`
both govern the whole pill from one place. Under reduced motion the states
still change — they simply change instantly, which is what the existing
reduced-motion block already does for the ring.

### 3.2 A loading bar until the library is ready

While the app has never yet received a project list, the pill's slot holds a
slim indeterminate bar instead of the pill. It resolves into the pill the
moment `GET /api/projects` first answers.

The signal already exists: `App.tsx` holds `projects` as `null` until that
first response. No new state, no new endpoint, and nothing polls.

This is deliberately **not** the `avg/shell.py` splash, which is a different
moment — the splash covers "the server does not exist yet", this covers "the
server is up and the library is still being counted". Slice 10 built the
first; this builds the second.

### 3.3 One deferred import

`avg/api/app.py` imports `import_all` inside the function that runs the
library scan, rather than at module scope. The scan already runs on a
background thread, so the ~0.50 s of `avg.config.legacy_import` — and the
whole pipeline-registry chain it drags in — moves off the startup path onto
a thread that was going to pay for it regardless.

**The remaining ~2.5 s is `fastapi` and `uvicorn` and is not addressed.**
`fastapi.exceptions` (0.73 s) and `fastapi.openapi.models` (0.41 s) are
imported by `fastapi.applications` itself; they cannot be avoided while the
app is a FastAPI app, and disabling the docs routes does not prevent the
import. This slice states that rather than attempting surgery on it, so the
next person to look does not re-measure it from scratch.

---

## 4. Testing

The frontend has no unit tests by convention; the pill and the bar are
verified by looking, enumerated in §5.

Python covers the one backend change:

- **The deferred import**: `avg.api.app` must not import
  `avg.config.legacy_import` at module scope — asserted the same way Slice
  10 asserts `avg.shell` does not import `uvicorn` at module scope, by
  checking the module has no such attribute after import. The library scan
  must still work, which the existing scan tests already cover.

Target: **769 tests still passing**, mypy no worse than its `Found 688
errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Boot-path import time re-measured with `-X importtime` and recorded in
  `.superpowers/sdd/progress.md`: `avg.api.app` cumulative should fall by
  roughly the 0.50 s `legacy_import` currently costs.
- Idle: the pill is small and shows a sage check, with no word beside it.
- Change screens: the ring appears and the label names the work. Do it on a
  fast local response and confirm a request under 150 ms shows no ring at
  all.
- Start a render: the ring keeps spinning and the label reads `Working`.
- Watch the pill go idle → busy → idle: the width animates and the contents
  cross-fade; nothing jumps.
- Set `motion_scale` to `reduced`, then set the OS to prefer reduced motion:
  in both cases the pill still changes state correctly, just without the
  motion. The OS preference wins over the setting.
- Launch the app cold and watch the pill's slot: a loading bar until the
  library arrives, then the pill.
- `"Build Installer.bat"` — the same behaviour in `AVG APP/`.

---

## 6. Out of scope

- The `avg/shell.py` splash, which Slice 10 built and this does not change.
- Reducing `fastapi`/`uvicorn` import cost — see §3.3 for why, measured.
- Toasts and the device-frame player, still open from Slice 6C.
- Live Preview (Slice 14), the Smart model picker (Slice 15) and parallel
  pipelines (Slice 16).
