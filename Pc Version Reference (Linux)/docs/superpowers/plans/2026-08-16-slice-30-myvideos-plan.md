# Slice 30 — My Videos: Implementation Plan

Implements `docs/superpowers/specs/2026-08-16-slice-30-myvideos-design.md`.
Read that first.

Verify: `.venv-build\Scripts\python.exe -m pytest tests -q`,
`.venv-build\Scripts\python.exe -m mypy avg`, `cd web && npm run build
&& npm run lint`. No frontend test runner exists (confirmed Slice 22).

## Files touched

| File | Change |
|---|---|
| `web/src/App.tsx` | `openProject`/`openAndRender`/`cancelRender` wrapped in `useCallback` |
| `web/src/ProjectCard.tsx` | Wrapped in `React.memo` |
| `web/src/Home.tsx` | Explicit `selectMode`; full-cell selection click target; Export/Open Folder in the select bar |
| `web/src/tokens.css` | `.select-dot` restructured (whole-cell click target + a separate visual badge); new `.select-dot__badge` |
| `web/src/api.ts` | `exportTakeToCapcut`, `openProjectFolder` |
| `avg/api/routes_takes.py` | New `POST /api/projects/{id}/open-folder` |
| `tests/api/test_routes_takes.py` | +2 tests |

Three independent tasks - performance, selection mode, and the new
actions touch different parts of the same two files but don't depend on
each other. Doing performance first since it's backend-free and
self-contained.

---

## Task 1 — Performance: memoize the grid

**Files:** `web/src/App.tsx`, `web/src/ProjectCard.tsx`

### Step 1.1 — stabilize the callbacks `Home` receives

In `web/src/App.tsx`, wrap the three functions passed to `Home` as props
in `useCallback` - without this, `React.memo` on `ProjectCard` (Step 1.2)
has nothing stable to compare against and does nothing. View the file
directly for `openProject`'s, `openAndRender`'s, and `cancelRender`'s
exact current bodies before editing - each keeps its existing body
unchanged, only gaining a `useCallback` wrapper and a dependency array
covering whatever state/functions it actually closes over (`projects`
for the existence check inside `openStudio` - Slice 27 - `setScreen`,
`setStartError`, and so on; read each function's real body to get its
own dependency list right rather than guessing one list for all three).

Pattern for each:

```typescript
const openProject = useCallback((id: string) => {
  // ... existing body, unchanged ...
}, [/* whatever state/setters the real body actually references */]);
```

`useCallback` needs to already be imported from `"react"` in this file -
confirm (it's a large file with many hooks already in use; very likely
already imported, add it to the existing React import line if not).

### Step 1.2 — memoize `ProjectCard`

In `web/src/ProjectCard.tsx`, replace:

```typescript
export default function ProjectCard({ project, onOpen, onRender }: ProjectCardProps) {
```

with:

```typescript
function ProjectCardImpl({ project, onOpen, onRender }: ProjectCardProps) {
```

Add, after the function's closing brace (check the file's actual end -
investigation saw this as a single-function default export, no other
exports below it to disturb):

```typescript
/**
 * Wrapped in `React.memo` (Slice 30) - `Home`'s own re-renders (the
 * continuous `GET /api/jobs` poll in `App.tsx` updates `jobs` state
 * unconditionally, which cascades down regardless of whether anything
 * about THIS card's own project changed) no longer force every card in
 * the grid to re-render along with it, as long as its own props
 * (`project`, `onOpen`, `onRender`) stay referentially stable - which
 * `App.tsx`'s new `useCallback` wrapping (Step 1.1) is what actually
 * makes true, not this alone.
 */
export default React.memo(ProjectCardImpl);
```

Add the import. Replace:

```typescript
import { useState } from "react";
```

with:

```typescript
import React, { useState } from "react";
```

(Match whatever this file's real top import line actually is - it was
seen using `useState` at minimum during investigation; add `React`
alongside whatever else is already there rather than assuming this is
the only hook imported.)

### Step 1.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** with
devtools' React Profiler (or a simple visual check - throttle CPU, watch
for grid-wide flicker), confirm that a jobs-poll tick with nothing
running no longer causes every card in a large library to re-render;
only cards whose own data actually changed should light up.

### Step 1.4 — commit

`fix: memoize project cards, stabilize their callbacks — Slice 30 Task 1`

---

## Task 2 — Selection mode

**Files:** `web/src/Home.tsx`, `web/src/tokens.css`

### Step 2.1 — CSS: whole-cell click target, separate visual badge

Replace the existing `.select-dot` rule and its neighbors. View
`web/src/tokens.css` directly at this location first for the exact
current properties (position/size/colors) before editing - investigation
captured the block's shape, not necessarily its precise final byte
sequence after any earlier slice's own edits to nearby rules. Replace:

```css
.select-dot {
  position: absolute;
  top: 48px;
  left: 50%;
  transform: translateX(-50%);
  width: 26px;
  height: 26px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--canvas) 72%, transparent);
  border: 1px solid var(--line-2);
  cursor: pointer;
  opacity: 0;
  transition: opacity calc(0.16s * var(--motion-scale)) ease-out;
}

.project-cell:hover .select-dot,
.select-dot--on,
.select-dot:focus-within {
  opacity: 1;
}

.select-dot--on {
  border-color: var(--sage);
  background: var(--sage-deep);
}

.select-dot input {
  position: absolute;
  inset: 0;
  opacity: 0;
  margin: 0;
  cursor: pointer;
}

.select-dot::after {
  content: "";
  width: 11px;
  height: 6px;
  border-left: 2px solid var(--canvas);
  border-bottom: 2px solid var(--canvas);
  transform: rotate(-45deg) translate(1px, -1px);
  opacity: 0;
  transition: opacity calc(0.12s * var(--motion-scale)) ease-out;
}

.select-dot--on::after {
  opacity: 1;
}
```

(Confirm this is the real current text - property order/exact values -
against the file directly; the block above is investigation's best
capture of it, and this task's whole point is replacing it precisely,
not approximately.)

with:

```css
/* Slice 30: rendered only while `selectMode` is on (React-conditional -
   Home.tsx - not hover-revealed), and now spans the WHOLE cell rather
   than a small centered dot, so clicking anywhere on a card selects it
   (spec §2's own "clicking cards selects them"). The visual badge moves
   to its own child element, `.select-dot__badge`, positioned where the
   old dot used to sit - decoupled from what's actually catching the
   click. */
.select-dot {
  position: absolute;
  inset: 0;
  cursor: pointer;
  z-index: 1;
}

.select-dot input {
  position: absolute;
  inset: 0;
  opacity: 0;
  margin: 0;
  cursor: pointer;
}

.select-dot__badge {
  position: absolute;
  top: 48px;
  left: 50%;
  transform: translateX(-50%);
  width: 26px;
  height: 26px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--canvas) 72%, transparent);
  border: 1px solid var(--line-2);
  pointer-events: none;
}

.select-dot--on .select-dot__badge {
  border-color: var(--sage);
  background: var(--sage-deep);
}

.select-dot__badge::after {
  content: "";
  width: 11px;
  height: 6px;
  border-left: 2px solid var(--canvas);
  border-bottom: 2px solid var(--canvas);
  transform: rotate(-45deg) translate(1px, -1px);
  opacity: 0;
  transition: opacity calc(0.12s * var(--motion-scale)) ease-out;
}

.select-dot--on .select-dot__badge::after {
  opacity: 1;
}
```

### Step 2.2 — Home.tsx: the `Select` toggle and full-cell click target

Add the state. Replace:

```typescript
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
```

with:

```typescript
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [selectMode, setSelectMode] = useState(false);
```

Add the header button, next to the existing Archived toggle. Replace:

```tsx
        {(archivedCount > 0 || showArchived) && (
          <button
            type="button"
            className={`btn${showArchived ? " btn--on" : ""}`}
            onClick={() => {
              setShowArchived((on) => !on);
              setSelected(new Set());
            }}
          >
            {showArchived ? "Back to library" : `Archived (${archivedCount})`}
          </button>
        )}
```

with:

```tsx
        {(archivedCount > 0 || showArchived) && (
          <button
            type="button"
            className={`btn${showArchived ? " btn--on" : ""}`}
            onClick={() => {
              setShowArchived((on) => !on);
              setSelected(new Set());
            }}
          >
            {showArchived ? "Back to library" : `Archived (${archivedCount})`}
          </button>
        )}
        <button
          type="button"
          className={`btn${selectMode ? " btn--on" : ""}`}
          onClick={() => {
            setSelectMode((on) => !on);
            setSelected(new Set());
          }}
        >
          {selectMode ? "Done" : "Select"}
        </button>
```

Make the checkbox conditional and add the badge span. Replace:

```tsx
          {gridProjects.map((project) => (
            <div key={project.id} className="project-cell">
              <ProjectCard project={project} onOpen={onOpen} onRender={onRender} />
              <label
                className={`select-dot${selected.has(project.id) ? " select-dot--on" : ""}`}
                title={showArchived ? "Select to restore" : "Select to archive"}
              >
                <input
                  type="checkbox"
                  checked={selected.has(project.id)}
                  onChange={() => toggleSelected(project.id)}
                  aria-label={`Select ${project.title}`}
                />
              </label>
            </div>
          ))}
```

with:

```tsx
          {gridProjects.map((project) => (
            <div key={project.id} className="project-cell">
              <ProjectCard project={project} onOpen={onOpen} onRender={onRender} />
              {selectMode && (
                <label
                  className={`select-dot${selected.has(project.id) ? " select-dot--on" : ""}`}
                  title={showArchived ? "Select to restore" : "Select to archive"}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(project.id)}
                    onChange={() => toggleSelected(project.id)}
                    aria-label={`Select ${project.title}`}
                  />
                  <span className="select-dot__badge" aria-hidden="true" />
                </label>
              )}
            </div>
          ))}
```

(`ProjectCard`'s own `onClick={open}` still fires underneath in
non-select mode - unreachable while `selectMode` is on, since the
now-full-cell `.select-dot` label sits above it and catches the click
first via `z-index: 1`, without any change to `ProjectCard` itself.)

### Step 2.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** outside
selection mode, cards behave exactly as before (click opens, hover
reveals nothing selection-related - there is no select-dot in the DOM
at all in this mode now). Clicking Select reveals a full-cell click
target on every card; clicking anywhere on a card toggles it, not only a
small corner dot; the visual badge still shows/hides correctly. Clicking
Select again (or navigating away and back) clears the selection.

### Step 2.4 — commit

`feat: explicit Select mode replaces hover-to-select — Slice 30 Task 2`

---

## Task 3 — Export to CapCut, Open Folder

**Files:** `avg/api/routes_takes.py`, `tests/api/test_routes_takes.py`,
`web/src/api.ts`, `web/src/Home.tsx`

### Step 3.1 — failing test

`tests/api/test_routes_takes.py` already has `_project(paths, project_id)`
and `client` fixtures (used throughout this file, confirmed during Slice
27's own work in this same file). Append:

```python
def test_open_project_folder_reveals_it_without_opening_explorer_under_pytest(client, paths, monkeypatch):
    """Same spy technique as Slice 23's Logs-button test - a bare 200
    can't distinguish a working PYTEST_CURRENT_TEST guard from a broken
    one silently swallowed by suppress(Exception).
    """
    import os

    project = _project(paths)
    client.app.state.projects[project.id] = project

    calls = []
    if not hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(os, "startfile", lambda *a, **k: calls.append(a), raising=False)

    r = client.post(f"/api/projects/{project.id}/open-folder", headers=AUTH)

    assert r.status_code == 200
    assert r.json()["opened"] == str(Path(project.folder))
    assert calls == []


def test_open_project_folder_requires_a_token(client, paths):
    project = _project(paths)
    client.app.state.projects[project.id] = project
    assert client.post(f"/api/projects/{project.id}/open-folder").status_code == 401
```

`export_take_route` itself needs no new test here - search
`tests/api/test_routes_takes.py` for its existing coverage first (it
almost certainly already has some, being a pre-existing route); this
task only adds frontend wiring to a route that already works, per the
spec's own point.

Run: `.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_takes.py -q`
— the two new tests fail, route doesn't exist yet.

### Step 3.2 — backend: Open Folder route

In `avg/api/routes_takes.py`, add near `export_take_route`:

```python
@router.post("/api/projects/{project_id}/open-folder")
async def open_project_folder(project_id: str, request: Request) -> dict:
    """Reveals a project's own folder in Explorer - My Videos' single-
    selection Open Folder action (Slice 30). Third instance of the same
    `os.startfile` + `PYTEST_CURRENT_TEST`-skip + `suppress(Exception)`
    shape as Slice 23's Logs button and `avg.export.capcut.export_take`'s
    own existing reveal - not a new pattern.
    """
    project = _get_project(request.app.state, project_id)
    folder = Path(project.folder)
    if "PYTEST_CURRENT_TEST" not in os.environ:
        with suppress(Exception):
            os.startfile(folder)  # type: ignore[attr-defined]  # Windows-only, by design
    return {"opened": str(folder)}
```

(`os`/`suppress`/`Path` need to already be imported in this file - very
likely yes, given the file's existing extensive path handling; confirm
before adding a duplicate.)

### Step 3.3 — frontend: wire both actions

In `web/src/api.ts`, add near `archiveProjects`:

```typescript
export function exportTakeToCapcut(projectId: string, takeId: number): Promise<{ folder: string }> {
  return apiPost<{ folder: string }>(`/api/projects/${projectId}/takes/${takeId}/export`);
}

export function openProjectFolder(projectId: string): Promise<{ opened: string }> {
  return apiPost<{ opened: string }>(`/api/projects/${projectId}/open-folder`);
}
```

In `web/src/Home.tsx`, add the import and the two handlers, plus
single-selection-only buttons in the select bar. Replace:

```typescript
import { archiveProjects, thumbnailUrl } from "./api";
```

with:

```typescript
import { archiveProjects, exportTakeToCapcut, openProjectFolder, thumbnailUrl } from "./api";
```

Add state and handlers near `applyArchive`. Replace:

```typescript
  async function applyArchive(archived: boolean) {
```

with:

```typescript
  const [exporting, setExporting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleExport(projectId: string) {
    const project = projects.find((p) => p.id === projectId);
    if (!project || project.current_take_id == null) {
      setActionError("This video has no completed take to export yet.");
      return;
    }
    setExporting(true);
    setActionError(null);
    try {
      await exportTakeToCapcut(projectId, project.current_take_id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  }

  async function handleOpenFolder(projectId: string) {
    try {
      await openProjectFolder(projectId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  }

  async function applyArchive(archived: boolean) {
```

Add the buttons to the select bar - single-selection only, per spec §3.
Replace:

```tsx
      {visibleSelected.length > 0 && (
        <div className="select-bar" role="region" aria-label="Bulk actions">
          <span>
            {visibleSelected.length} selected
          </span>
          <button type="button" className="btn" onClick={() => setSelected(new Set())} disabled={busy}>
            Clear
          </button>
          {/* Archiving hides; it never deletes or moves a folder, so it needs
              no confirmation and stays a plain action. */}
          <button type="button" className="btn" onClick={() => applyArchive(!showArchived)} disabled={busy}>
            {busy ? "Working…" : showArchived ? "Restore" : "Archive"}
          </button>
        </div>
      )}
```

with:

```tsx
      {visibleSelected.length > 0 && (
        <div className="select-bar" role="region" aria-label="Bulk actions">
          <span>
            {visibleSelected.length} selected
          </span>
          <button type="button" className="btn" onClick={() => setSelected(new Set())} disabled={busy}>
            Clear
          </button>
          {/* Export/Open Folder are single-target, not bulk (spec §3) -
              firing several CapCut exports or several Explorer windows
              from one click is worse than the problem this fixes. */}
          {visibleSelected.length === 1 && (
            <>
              <button
                type="button"
                className="btn"
                onClick={() => void handleExport(visibleSelected[0])}
                disabled={exporting}
              >
                {exporting ? "Exporting…" : "Export to CapCut"}
              </button>
              <button type="button" className="btn" onClick={() => void handleOpenFolder(visibleSelected[0])}>
                Open Folder
              </button>
            </>
          )}
          {/* Archiving hides; it never deletes or moves a folder, so it needs
              no confirmation and stays a plain action. */}
          <button type="button" className="btn" onClick={() => applyArchive(!showArchived)} disabled={busy}>
            {busy ? "Working…" : showArchived ? "Restore" : "Archive"}
          </button>
          {actionError && <span className="field-row__blocked">{actionError}</span>}
        </div>
      )}
```

### Step 3.4 — verify

```
.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_takes.py -q
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

**Manual check:** select exactly one finished video - Export to CapCut
and Open Folder both appear; Export succeeds and reveals the export
folder (the existing `export_take` behavior, unchanged); Open Folder
reveals the project's own folder. Select two or more - both buttons
disappear, only Clear/Archive remain.

### Step 3.5 — commit

`feat: Export to CapCut and Open Folder from My Videos — Slice 30 Task 3`

---

## Task 4 — Full regression, update progress ledger

### Step 4.1

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

### Step 4.2

Update `.superpowers/sdd/progress.md`: Slice 30 complete - the
performance root cause (unmemoized cards + unstable callbacks reacting
to an unrelated poll), the selection-mode redesign, and that
virtualization was deliberately not used.

This also closes out the entire `docs/reference/slice 20+ prompt.txt`
backlog (Slices 20-30) - worth noting in the ledger as the batch's own
completion point, not just this one slice's.

### Step 4.3 — commit

`docs: Slice 30 complete — My Videos (performance, selection, export)`
