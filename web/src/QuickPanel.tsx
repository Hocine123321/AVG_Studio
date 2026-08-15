import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconChevron } from "./Icons";
import LiveList from "./LiveList";
import PreviewTab from "./PreviewTab";
import QuickSettingsTab from "./QuickSettingsTab";
import RecentErrorsTab from "./RecentErrorsTab";
import { useSettingsData } from "./useSettingsData";

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

type Tab = "livelist" | "quicksettings" | "preview" | "errors" | "inwork";

const TABS: { id: Tab; label: string }[] = [
  { id: "livelist", label: "Live List" },
  { id: "quicksettings", label: "Quick Settings" },
  { id: "preview", label: "Preview" },
  { id: "errors", label: "Recent Errors" },
  { id: "inwork", label: "In work" },
];

/**
 * The floating Quick panel (Slice 18 spec §1-2): a portalled, non-modal
 * overlay available on every screen, draggable and clamped to the window,
 * holding five tabs. Mounted once in `App.tsx`, beside `StatusBar` and
 * `Transport`, outside `.app-shell__content` - a screen change must never
 * unmount it or lose its tab/position (spec §1).
 *
 * `open` is a controlled prop, not local state: the `<` toggle lives in
 * `Rail.tsx` (spec's own recorded safe default, plan decision #1), and a
 * sibling component cannot flip state this component owns internally
 * without either a portal-rendered button (rejected - see Task 9 Step 4 in
 * the plan) or lifting `open` to `App.tsx`, which is what this does.
 *
 * Tab and drag position stay local + `localStorage`-backed: unlike
 * `open`, nothing outside this component ever needs to read or set them.
 *
 * One `useSettingsData()` instance lives here and is shared by the Quick
 * Settings and Preview tabs (plan decision #3) - both tabs edit the exact
 * same draft/save cycle Settings.tsx uses, not two independent copies that
 * could show different pending edits for the same setting.
 */
export default function QuickPanel({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>(() => {
    const stored = localStorage.getItem(TAB_KEY);
    return stored === "livelist" ||
      stored === "quicksettings" ||
      stored === "preview" ||
      stored === "errors"
      ? stored
      : "livelist";
  });
  const [position, setPosition] = useState<Position>(readPosition);
  const [size, setSize] = useState<Size>(readSize);

  const settingsData = useSettingsData();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dragOffset = useRef<{ dx: number; dy: number } | null>(null);
  const resizeStart = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);

  // The portal's DOM node stays mounted forever once `open` has been true
  // at least once - `visible` (not `mounted`) is what actually drives the
  // open/closed CSS class. This is what makes the slide-OUT possible at
  // all: an instant conditional unmount (`{open && ...}`) gives a CSS
  // transition no time to run, since there is no frame between "open flips
  // false" and the node leaving the DOM. Staying mounted and transitioning
  // a class instead is the standard fix, and it is symmetric with the
  // slide-IN below by construction - both directions are the same
  // transition, just reversed, matching spec §1's "slides back out the
  // same way."
  const [mounted, setMounted] = useState(open);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (open) setMounted(true);
  }, [open]);

  // One frame's delay before applying the "open" class, so a first-ever
  // mount actually paints the closed transform before transitioning away
  // from it - flipping straight to the open class on the same render the
  // node is created would give the browser nothing to transition FROM.
  useEffect(() => {
    if (!mounted) return;
    if (open) {
      const id = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(id);
    }
    setVisible(false);
  }, [open, mounted]);

  useEffect(() => {
    localStorage.setItem(TAB_KEY, tab);
  }, [tab]);

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

  /** The panel's own current box, or the fallback before it has ever
   * rendered - see `FALLBACK_WIDTH`/`FALLBACK_HEIGHT`'s own comment. */
  function panelSize(): { w: number; h: number } {
    const rect = panelRef.current?.getBoundingClientRect();
    return rect ? { w: rect.width, h: rect.height } : { w: FALLBACK_WIDTH, h: FALLBACK_HEIGHT };
  }

  // Clamp once on mount (and whenever the panel opens): a window resized
  // smaller since the position was stored must not restore the panel
  // off-screen.
  useEffect(() => {
    if (!open) return;
    const { w, h } = panelSize();
    setPosition((p) => clamp(p, w, h));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Escape closes it - spec §1: "a floating thing that cannot be
  // dismissed from the keyboard is a trap." Global, not focus-scoped: the
  // panel is non-modal, so focus may well be on the page behind it.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChange(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // Re-clamp on resize while open, per spec §2. Size first, then position
  // with the (possibly now-smaller) size - a window that got shorter must
  // shrink the panel's own height back under the new `MAX_HEIGHT_VH`
  // ceiling, not just nudge its position; clampSize's ceiling is itself a
  // fraction of `window.innerHeight`, so it already reflects the new size
  // by the time this runs.
  useEffect(() => {
    if (!open) return;
    function onResize() {
      setSize((s) => clampSize(s));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);

  // Drag, without a new dependency (same reasoning `Select.tsx` gives for
  // portalling rather than pulling in a library): pointerdown on the
  // header captures the pointer-to-panel offset, pointermove re-derives
  // position from that offset through `clamp`, pointerup releases. On the
  // header element only - the spec doesn't ask the whole panel body to be
  // a drag handle, and making it one would fight clicking the tabs and
  // fields inside.
  function onHeaderPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    const rect = panelRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragOffset.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onHeaderPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragOffset.current) return;
    const { w, h } = panelSize();
    const next = { x: e.clientX - dragOffset.current.dx, y: e.clientY - dragOffset.current.dy };
    setPosition(clamp(next, w, h));
  }

  function onHeaderPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragOffset.current) return;
    dragOffset.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }

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

  return (
    mounted &&
    createPortal(
          <div
            ref={panelRef}
            className={`quickpanel${visible ? "" : " quickpanel--closed"}`}
            style={{ left: position.x, top: position.y, width: size.w, height: size.h }}
            role="dialog"
            aria-label="Quick panel"
            aria-hidden={!open}
            inert={!open}
          >
            <div
              className="quickpanel__header"
              onPointerDown={onHeaderPointerDown}
              onPointerMove={onHeaderPointerMove}
              onPointerUp={onHeaderPointerUp}
              onPointerCancel={onHeaderPointerUp}
            >
              <span className="quickpanel__title">Quick panel</span>
              <button
                type="button"
                className="quickpanel__close"
                aria-label="Close quick panel"
                onClick={() => onOpenChange(false)}
              >
                <IconChevron />
              </button>
            </div>
            <div className="quickpanel__tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  disabled={t.id === "inwork"}
                  className={`quickpanel__tab${tab === t.id ? " quickpanel__tab--on" : ""}`}
                  title={t.id === "inwork" ? "More tools, coming soon" : undefined}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
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
          document.body,
        )
  );
}
