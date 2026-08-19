# Slice 30 — My Videos: performance, selection mode, CapCut export: Design

**Goal:** the grid stays responsive with many videos, selection becomes
an explicit mode instead of a hover-revealed checkbox, and a selected
video can be exported to CapCut or revealed in its folder.

## 1. Performance — traced to an actual cause, not guessed

`reloadProjects` (`GET /api/projects`, feeding `projects` state) runs
once on mount, not on a timer. `reloadJobs` (`GET /api/jobs`), by
contrast, polls continuously and unconditionally once onboarded (its own
comment: a queued job is invisible over the socket for as long as it
stays queued, so this can't gate on "is anything running" the way
`ModelCacheCard`'s own poll does). Every poll tick updates `jobs` state
in `App.tsx`, which re-renders `App.tsx`, which re-renders `Home` with a
fresh `jobs`/`byProject` - `liveIds`/`gridProjects` are recomputed (new
array references) on every tick, and neither `ProjectCard` nor `Home`'s
own callback props (`onOpen={openProject}`, `onRender={openAndRender}` -
plain functions in `App.tsx`'s body, not `useCallback`-wrapped) survive
a shallow-equality check across that. The result: every project card in
the grid - not just the live ones the poll is actually about - is a
candidate for re-render on every jobs-poll tick, without anything
gating it. More projects in the grid means more wasted work per tick,
which is exactly "more videos, more lag."

**Fix, not virtualization:** `React.memo(ProjectCard)`, plus wrapping
`openProject`/`openAndRender`/the other callbacks `Home` receives in
`useCallback` so memoization has stable props to actually compare
against - without the second half, the first does nothing. This
directly addresses the item's own "unnecessary rendering" and "event
handlers" investigation prompts; DOM size and thumbnail loading were
checked and are already reasonable (`loading="lazy"` is already present
on every card's `<img>`, and there's no obviously heavy per-card
computation - `resolveState`/`formatCreated` are both cheap, synchronous,
and small). Virtualization is not warranted by what was actually found -
the cost is redundant re-renders of unaffected cards, not raw DOM
volume - matching the item's own instruction not to reach for it
pre-emptively.

## 2. Selection mode, not hover-to-select

Current mechanism: `.select-dot`, a small checkbox badge on every card,
opacity-hidden until `.project-cell:hover` reveals it - the exact
behavior the item asks to remove. New: a `Select` button in the grid's
own header toggles a `selectMode` state. Not selecting: cards behave
exactly as they already do (`ProjectCard`'s own `onClick={open}` covers
the whole card already). Selecting: the same `<label>`/`<input
type="checkbox">` pair is still what's rendered - conditionally, on
`selectMode`, not hover - but resized to cover the entire cell rather
than a small centered dot, so clicking anywhere on a card toggles its
selection (the item's own "clicking cards selects them"); the small
visual badge (checkmark, sage when on) moves to its own inner element
positioned where the dot already sits today, decoupled from what's
actually catching the click. Nothing here is hover-gated any more, in
either mode - the item's explicit "hover should not secretly toggle
selection."

## 3. Selected-video actions

**Archive/Restore stay bulk** (unchanged, already correct - "one call
for a whole selection," per that code's own existing comment).
**Export and Open Folder are single-target**, not bulk - the item's own
wording ("when a video is selected") reads as one at a time, and
firing five simultaneous CapCut exports or five Explorer windows from
one click is a worse experience than the one problem this fixes.
Visible in the select bar only when exactly one project is selected.

**Export** - `POST /api/projects/{id}/takes/{take_id}/export` already
exists (`avg.export.capcut.export_take`, wired to a real route in
`routes_takes.py`) and has zero callers anywhere in the frontend today -
confirmed by search, not assumed. This slice's job is entirely wiring a
button to a call that already works, exactly matching "reuse the
existing API, do not duplicate export logic." Exports the selected
project's `current_take_id`.

**Open Folder** - no equivalent route exists yet for "reveal this
project's folder" (distinct from a take's export folder). New `POST
/api/projects/{id}/open-folder`, the same `os.startfile` +
`PYTEST_CURRENT_TEST`-skip + `suppress(Exception)` shape Slice 23's Logs
button and `capcut.py`'s own export already both use - a third instance
of an established pattern, not a new one.

## 4. Testing

Backend: the new open-folder route - folder path resolves correctly,
guarded the same way under pytest (spy-based, matching Slice 23's own
test technique - a bare 200 response can't distinguish a working guard
from a broken one silently swallowed by `suppress(Exception)`).

Frontend: no test runner (confirmed Slice 22) - `tsc -b` + `oxlint`
clean, plus manual checks: grid stays responsive with a jobs poll firing
while many projects are on screen (a slow machine or throttled CPU in
devtools makes this easier to feel); Select button toggles selection
mode cleanly with no residual hover-reveal; clicking anywhere on a card
in selection mode toggles it; Export and Open Folder appear only at
exactly one selection and both work.

## 5. Out of scope

- Virtualizing the grid - not warranted by what was actually found (§1).
- A bulk variant of Export/Open Folder.
- Any change to `avg.export.capcut.export_take` itself - reused as-is.
