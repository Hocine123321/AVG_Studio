# Slice 24 — Quick Panel: Implementation Plan

Implements `docs/superpowers/specs/2026-08-14-slice-24-quickpanel-design.md`.
Read that first.

Frontend only. Verify: `cd web && npm run build && npm run lint`. No
frontend test runner exists (confirmed Slice 22) - manual checks are
called out per task.

## Files touched

| File | Change |
|---|---|
| `web/src/tokens.css` | `.quickpanel-toggle` position fix; `.quickpanel` sizing becomes inline-style-driven; new `.quickpanel__resize-handle` |
| `web/src/QuickPanel.tsx` | Resizable width/height, persisted, clamped |
| `web/src/PreviewTab.tsx` | Refresh button, `refreshKey` state |
| `web/src/PreviewPanel.tsx` | `refreshKey` threaded to `CaptionPreview`/`OverlayPreview` only |

Three independent tasks. Doing the puller CSS fix first - smallest,
zero-risk, good warm-up before the resize state work.

---

## Task 1 — Puller alignment (#14)

**Files:** `web/src/tokens.css`

### Step 1.1 — implement

Replace:

```css
.quickpanel-toggle {
  position: absolute;
  top: 78px;
  right: -13px;
```

with:

```css
.quickpanel-toggle {
  position: absolute;
  top: 22px;
  right: -13px;
```

Also update the rule's own comment, which still says "vertically below
the logo" - no longer accurate once centered on it. Replace:

```css
/* The Quick panel's own open/close toggle (Slice 18 spec §1) - rendered at
   the rail's own outer edge, vertically below the logo, per the spec's
   recorded safe default: the logo keeps the corner it already occupies,
   this sits at the column's edge rather than in it, and it is deliberately
   NOT one of the 48px `.rail__item` icons since it belongs to a different,
   cross-cutting concern (a global panel toggle, not a section of the app).
   Half-overlapping the rail's own right border, the same "floating tab"
   affordance a collapse control reads as everywhere else it appears. */
```

with:

```css
/* The Quick panel's own open/close toggle (Slice 18 spec §1) - rendered at
   the rail's own outer edge, vertically CENTERED on the "A" logo (Slice 24 -
   `top: 78px` put it inside the Create Video button's own row instead,
   which is the bug the item reported; `22px` is the logo's own vertical
   center: `16px` rail padding + half of the logo's `38px` height, minus
   half this toggle's `26px` height). Deliberately NOT one of the 48px
   `.rail__item` icons since it belongs to a different, cross-cutting
   concern (a global panel toggle, not a section of the app).
   Half-overlapping the rail's own right border, the same "floating tab"
   affordance a collapse control reads as everywhere else it appears. */
```

### Step 1.2 — verify

`cd web && npm run build && npm run lint`. **Manual check:** the `<`
puller sits vertically centered against the "A" logo, not beside/inside
the Create Video button's row.

### Step 1.3 — commit

`fix: quick panel puller aligns with the A logo — Slice 24 Task 1`

---

## Task 2 — Resizable, wider minimum (#4)

**Files:** `web/src/QuickPanel.tsx`, `web/src/tokens.css`

### Step 2.1 — CSS

Replace:

```css
.quickpanel {
  position: fixed;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: 380px;
  max-height: 70vh;
  border-radius: var(--radius-lg);
```

with:

```css
.quickpanel {
  position: fixed;
  z-index: 30;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-lg);
```

(`width`/`height` move to the component's inline `style`, alongside
`left`/`top`, which already work this way - see Step 2.2. `max-height`
is dropped rather than kept as a second cap: real height is now an
explicit, resizable value, not content-driven.)

Add the resize handle, directly after `.quickpanel--closed`'s rule:

```css
.quickpanel__resize-handle {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  border-radius: 0 0 var(--radius-lg) 0;
}

.quickpanel__resize-handle::after {
  content: "";
  position: absolute;
  right: 4px;
  bottom: 4px;
  width: 8px;
  height: 8px;
  border-right: 2px solid var(--line-2);
  border-bottom: 2px solid var(--line-2);
  border-radius: 0 0 2px 0;
}
```

### Step 2.2 — QuickPanel.tsx

Replace the constants and position helpers. Replace:

```typescript
const POSITION_KEY = "avg.quickpanel.position";
const TAB_KEY = "avg.quickpanel.tab";

// Used to clamp before the panel has ever been measured (first paint of a
// fresh install, no `POSITION_KEY` yet) - kept in sync with the panel's
// actual CSS size in tokens.css (Task 10). Once the panel has rendered,
// `panelRef`'s own measured box is used instead, so a real CSS change
// never has to be mirrored into a second set of numbers here.
const FALLBACK_WIDTH = 380;
const FALLBACK_HEIGHT = 520;

interface Position {
  x: number;
  y: number;
}

/** A window resized smaller between sessions must not restore the panel
 * off-screen (spec §2) - applied on read AND on every drag move/end AND on
 * a live `resize` listener while open. */
function clamp(pos: Position, panelW: number, panelH: number): Position {
  const maxX = Math.max(0, window.innerWidth - panelW);
  const maxY = Math.max(0, window.innerHeight - panelH);
  return { x: Math.min(Math.max(pos.x, 0), maxX), y: Math.min(Math.max(pos.y, 0), maxY) };
}

function readPosition(): Position {
  try {
    const raw = localStorage.getItem(POSITION_KEY);
    if (!raw) return { x: 90, y: 60 };
    const parsed = JSON.parse(raw) as Position;
    if (typeof parsed.x !== "number" || typeof parsed.y !== "number") return { x: 90, y: 60 };
    return parsed;
  } catch {
    return { x: 90, y: 60 };
  }
}
```

with:

```typescript
const POSITION_KEY = "avg.quickpanel.position";
const SIZE_KEY = "avg.quickpanel.size";
const TAB_KEY = "avg.quickpanel.tab";

// The panel's default AND fallback size - used both to seed `readSize()`
// on a fresh install (no `SIZE_KEY` yet) and to clamp before the panel has
// ever been measured. Once rendered, `panelRef`'s own measured box is used
// instead for clamping position, so a resize never has to be mirrored into
// a second set of numbers - only these two defaults are hand-maintained.
const FALLBACK_WIDTH = 380;
const FALLBACK_HEIGHT = 520;

// 340 leaves real margin below the fixed 380 every install already used
// without inviting the five-tab-label wrap the item warns about (spec
// §2) - confirmed against the rendered tab strip, not just arithmetic, is
// still worth doing once this is actually running.
const MIN_WIDTH = 340;
const MAX_WIDTH = 640;
const MIN_HEIGHT = 320;
// A fraction of the window, matching the pre-resize `max-height: 70vh`
// cap's own idiom, not a fixed pixel count - a resizable panel's ceiling
// should still scale with the screen it's on.
const MAX_HEIGHT_VH = 0.8;

interface Position {
  x: number;
  y: number;
}

interface Size {
  w: number;
  h: number;
}

/** A window resized smaller between sessions must not restore the panel
 * off-screen (spec §2) - applied on read AND on every drag move/end AND on
 * a live `resize` listener while open. */
function clamp(pos: Position, panelW: number, panelH: number): Position {
  const maxX = Math.max(0, window.innerWidth - panelW);
  const maxY = Math.max(0, window.innerHeight - panelH);
  return { x: Math.min(Math.max(pos.x, 0), maxX), y: Math.min(Math.max(pos.y, 0), maxY) };
}

/** Keeps a resize within both the fixed floor/ceiling above AND the
 * window's own current height - the same "don't let a smaller window
 * strand it" reasoning `clamp` above already applies to position. */
function clampSize(size: Size): Size {
  const maxH = window.innerHeight * MAX_HEIGHT_VH;
  return {
    w: Math.min(Math.max(size.w, MIN_WIDTH), MAX_WIDTH),
    h: Math.min(Math.max(size.h, MIN_HEIGHT), maxH),
  };
}

function readPosition(): Position {
  try {
    const raw = localStorage.getItem(POSITION_KEY);
    if (!raw) return { x: 90, y: 60 };
    const parsed = JSON.parse(raw) as Position;
    if (typeof parsed.x !== "number" || typeof parsed.y !== "number") return { x: 90, y: 60 };
    return parsed;
  } catch {
    return { x: 90, y: 60 };
  }
}

function readSize(): Size {
  try {
    const raw = localStorage.getItem(SIZE_KEY);
    if (!raw) return { w: FALLBACK_WIDTH, h: FALLBACK_HEIGHT };
    const parsed = JSON.parse(raw) as Size;
    if (typeof parsed.w !== "number" || typeof parsed.h !== "number") return { w: FALLBACK_WIDTH, h: FALLBACK_HEIGHT };
    return clampSize(parsed);
  } catch {
    return { w: FALLBACK_WIDTH, h: FALLBACK_HEIGHT };
  }
}
```

Add size state and its persistence/re-clamp effects. Replace:

```typescript
  const [position, setPosition] = useState<Position>(readPosition);

  const settingsData = useSettingsData();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dragOffset = useRef<{ dx: number; dy: number } | null>(null);
```

with:

```typescript
  const [position, setPosition] = useState<Position>(readPosition);
  const [size, setSize] = useState<Size>(readSize);

  const settingsData = useSettingsData();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dragOffset = useRef<{ dx: number; dy: number } | null>(null);
  const resizeStart = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);
```

Replace:

```typescript
  useEffect(() => {
    localStorage.setItem(POSITION_KEY, JSON.stringify(position));
  }, [position]);
```

with:

```typescript
  useEffect(() => {
    localStorage.setItem(POSITION_KEY, JSON.stringify(position));
  }, [position]);

  useEffect(() => {
    localStorage.setItem(SIZE_KEY, JSON.stringify(size));
  }, [size]);

  // A manual resize can push the panel's own edge past the window's -
  // the existing window-resize listener below only re-clamps position when
  // the WINDOW changes, not when the PANEL does, so this covers the other
  // half of the same concern.
  useEffect(() => {
    const { w, h } = panelSize();
    setPosition((p) => clamp(p, w, h));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size]);
```

(This second effect references `panelSize`, defined below it in the
component - fine in a function component, since `panelSize` isn't called
until this effect actually runs, well after the whole function body has
finished defining every function in it.)

Add the resize pointer handlers, directly after `onHeaderPointerUp`:

```typescript
  function onResizePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    resizeStart.current = { startX: e.clientX, startY: e.clientY, startW: size.w, startH: size.h };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onResizePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!resizeStart.current) return;
    const { startX, startY, startW, startH } = resizeStart.current;
    setSize(clampSize({ w: startW + (e.clientX - startX), h: startH + (e.clientY - startY) }));
  }

  function onResizePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (!resizeStart.current) return;
    resizeStart.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }
```

Apply the size to the panel's inline style and add the handle to the
JSX. Replace:

```tsx
          <div
            ref={panelRef}
            className={`quickpanel${visible ? "" : " quickpanel--closed"}`}
            style={{ left: position.x, top: position.y }}
```

with:

```tsx
          <div
            ref={panelRef}
            className={`quickpanel${visible ? "" : " quickpanel--closed"}`}
            style={{ left: position.x, top: position.y, width: size.w, height: size.h }}
```

Replace the closing of the body div (add the handle as the panel's last
child, a sibling of `.quickpanel__body`, not inside it - it needs to sit
at the panel's own corner regardless of which tab is scrolled where):

```tsx
            <div className="quickpanel__body">
              {tab === "livelist" && <LiveList />}
              {tab === "quicksettings" && (
                <QuickSettingsTab
                  schema={settingsData.schema}
                  ctx={settingsData.ctx}
                  dirtyCount={settingsData.dirtyCount}
                  saving={settingsData.saving}
                  saveError={settingsData.saveError}
                  handleSave={settingsData.handleSave}
                />
              )}
              {tab === "preview" && <PreviewTab ctx={settingsData.ctx} />}
              {tab === "errors" && <RecentErrorsTab />}
              {tab === "inwork" && <p className="home__empty">More tools are coming here.</p>}
            </div>
          </div>,
```

with:

```tsx
            <div className="quickpanel__body">
              {tab === "livelist" && <LiveList />}
              {tab === "quicksettings" && (
                <QuickSettingsTab
                  schema={settingsData.schema}
                  ctx={settingsData.ctx}
                  dirtyCount={settingsData.dirtyCount}
                  saving={settingsData.saving}
                  saveError={settingsData.saveError}
                  handleSave={settingsData.handleSave}
                />
              )}
              {tab === "preview" && <PreviewTab ctx={settingsData.ctx} />}
              {tab === "errors" && <RecentErrorsTab />}
              {tab === "inwork" && <p className="home__empty">More tools are coming here.</p>}
            </div>
            <div
              className="quickpanel__resize-handle"
              onPointerDown={onResizePointerDown}
              onPointerMove={onResizePointerMove}
              onPointerUp={onResizePointerUp}
              onPointerCancel={onResizePointerUp}
              aria-hidden="true"
            />
          </div>,
```

### Step 2.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** drag the
corner handle - panel resizes both dimensions, stops at each bound
(340-640 wide, 320px-80vh tall), reload the page and confirm the size
persisted. Open the Preview tab specifically and confirm its frame
doesn't overflow or clip at both the minimum and maximum size (spec §2's
explicit flag - check this for real here, not just in review).

### Step 2.4 — commit

`feat: resizable Quick Panel with a wider floor — Slice 24 Task 2`

---

## Task 3 — Preview refresh (#19)

**Files:** `web/src/PreviewTab.tsx`, `web/src/PreviewPanel.tsx`

### Step 3.1 — PreviewPanel.tsx: accept and thread `refreshKey`

Replace:

```typescript
interface Props {
  kind: PreviewKind;
  /** Settings.tsx's own `valueById` - draft-aware, so the preview reflects
   * an edit the instant it is made rather than waiting for Save. */
  valueOf: (id: string) => string;
}
```

with:

```typescript
interface Props {
  kind: PreviewKind;
  /** Settings.tsx's own `valueById` - draft-aware, so the preview reflects
   * an edit the instant it is made rather than waiting for Save. */
  valueOf: (id: string) => string;
  /** Bumped by `PreviewTab`'s Refresh button. Only `CaptionPreview` and
   * `OverlayPreview` read it - `VoicePreview`/`ImagePreview` are paid,
   * gated behind their own explicit "Generate a sample" button, and stay
   * that way; adding a second path to `confirm_paid: true` here would be
   * exactly the regression this module's own docstring already warns
   * against (see "the money rule" below). */
  refreshKey?: number;
}
```

Replace the dispatcher:

```tsx
export default function PreviewPanel({ kind, valueOf }: Props) {
  if (kind === "caption") return <CaptionPreview valueOf={valueOf} />;
  if (kind === "overlay") return <OverlayPreview valueOf={valueOf} />;
  if (kind === "voice") return <VoicePreview valueOf={valueOf} />;
  return <ImagePreview valueOf={valueOf} />;
}
```

with:

```tsx
export default function PreviewPanel({ kind, valueOf, refreshKey }: Props) {
  if (kind === "caption") return <CaptionPreview valueOf={valueOf} refreshKey={refreshKey} />;
  if (kind === "overlay") return <OverlayPreview valueOf={valueOf} refreshKey={refreshKey} />;
  if (kind === "voice") return <VoicePreview valueOf={valueOf} />;
  return <ImagePreview valueOf={valueOf} />;
}
```

Replace `CaptionPreview`'s signature and dependency array:

```typescript
function CaptionPreview({ valueOf }: { valueOf: (id: string) => string }) {
```

with:

```typescript
function CaptionPreview({
  valueOf,
  refreshKey,
}: {
  valueOf: (id: string) => string;
  refreshKey?: number;
}) {
```

and:

```typescript
  }, [width, height, fontName, fontSize, position]);
```

with:

```typescript
  }, [width, height, fontName, fontSize, position, refreshKey]);
```

Replace `OverlayPreview`'s signature and dependency array the same way.
Replace:

```typescript
function OverlayPreview({ valueOf }: { valueOf: (id: string) => string }) {
```

with:

```typescript
function OverlayPreview({
  valueOf,
  refreshKey,
}: {
  valueOf: (id: string) => string;
  refreshKey?: number;
}) {
```

and:

```typescript
  }, [width, height, font, accent1, accent2, highlight]);
```

with:

```typescript
  }, [width, height, font, accent1, accent2, highlight, refreshKey]);
```

`VoicePreview`/`ImagePreview` are untouched - no `refreshKey` parameter
added, matching the spec's resolution that Refresh is a no-op for both
rather than a second path to their paid call.

### Step 3.2 — PreviewTab.tsx: the button

Replace:

```typescript
export default function PreviewTab({ ctx }: { ctx: SettingsCtx }) {
  const [kind, setKind] = useState<PreviewKind>("caption");
  return (
    <div className="quickpanel-preview">
      <div className="quickpanel-preview__picker">
        <span className="field-row__label">Preview</span>
        <Select
          value={kind}
          options={KIND_OPTIONS}
          onChange={(v) => setKind(v as PreviewKind)}
          ariaLabel="Choose what to preview"
        />
      </div>
      <div className="quickpanel-preview__frame">
        <PreviewPanel kind={kind} valueOf={ctx.valueById} />
      </div>
    </div>
  );
}
```

with:

```typescript
export default function PreviewTab({ ctx }: { ctx: SettingsCtx }) {
  const [kind, setKind] = useState<PreviewKind>("caption");
  // Bumped to force an immediate re-fetch rather than waiting for the
  // debounce - see PreviewPanel.tsx's `refreshKey` doc for why this only
  // does anything for Caption/Overlay.
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="quickpanel-preview">
      <div className="quickpanel-preview__picker">
        <span className="field-row__label">Preview</span>
        <Select
          value={kind}
          options={KIND_OPTIONS}
          onChange={(v) => setKind(v as PreviewKind)}
          ariaLabel="Choose what to preview"
        />
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => setRefreshKey((k) => k + 1)}
        >
          Refresh
        </button>
      </div>
      <div className="quickpanel-preview__frame">
        <PreviewPanel kind={kind} valueOf={ctx.valueById} refreshKey={refreshKey} />
      </div>
    </div>
  );
}
```

Check `.quickpanel-preview__picker`'s CSS (`display: flex` or similar)
fits a third inline element without wrapping oddly - adjust only if the
manual check below shows a problem; no CSS change is anticipated but
this is a fit worth actually looking at once it renders, not assuming.

### Step 3.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** on Caption or
Overlay kind, click Refresh - the frame visibly re-fetches (a brief
`preview-frame--busy` state). Switch to Voice or Image kind, click
Refresh - nothing happens, no network request, no charge; "Generate a
sample" is still the only thing that does.

### Step 3.4 — commit

`feat: Preview tab Refresh button — Slice 24 Task 3`

---

## Task 4 — Update progress ledger

### Step 4.1

Update `.superpowers/sdd/progress.md`: Slice 24 complete, all three items
noted (including the deliberate no-op for Voice/Image Refresh, so a
future reader doesn't "fix" it into a money-rule regression).

### Step 4.2 — commit

`docs: Slice 24 complete — Quick Panel resize, puller, Preview refresh`
