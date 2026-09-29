# Slice 28 — Status pill: Implementation Plan

Implements `docs/superpowers/specs/2026-08-16-slice-28-statuspill-design.md`.
Read that first.

Frontend only. Verify: `cd web && npm run build && npm run lint`. No
frontend test runner exists (confirmed Slice 22).

## Files touched

| File | Change |
|---|---|
| `web/src/StatusBar.tsx` | Drop inline label/clock/count rendering; carry that text as `aria-label` instead; module docstring's position note |
| `web/src/tokens.css` | `.statuspill` becomes circular by default, positioned top-left; `.statuspill--docked` becomes the pull-tab, not a flush-edge sliver; new pull animation; now-dead `.statuspill__expand`/`__label`/`__clock`/`__more` rules removed |

One task - a single component and its stylesheet section, both edited
together since neither makes sense reviewed alone.

---

## Task 1 — Circular active, top-left pull-tab idle

**Files:** `web/src/StatusBar.tsx`, `web/src/tokens.css`

### Step 1.1 — CSS: position, shape, the pull-tab, the pull animation

Replace the base rule. Replace:

```css
.statuspill {
  position: absolute;
  top: 14px;
  right: 22px;
  z-index: 20;
  display: flex;
  align-items: center;
  padding: 7px 16px 7px 12px;
  border-radius: 999px;
  border: 1.5px solid var(--pill);
  background: color-mix(in srgb, var(--pill) 12%, var(--panel));
  color: var(--pill);
  font-size: 12.5px;
  font-weight: 600;
  box-shadow: var(--shadow-sm);
  /* Never intercept a click meant for the content behind it - it reports,
     it is not a control. */
  pointer-events: none;
  /* Backstop for a label wide enough to make `.statuspill__expand`'s own
     max-width (below) not the tightest constraint in play. The pill's
     actual growing and shrinking is `.statuspill__expand`'s job, not this
     rule's - `width: auto` cannot be transitioned directly, which is why
     that element uses `max-width` as an animatable stand-in for it, with
     the contents crossfading via `.statuspill__icon` and
     `.statuspill__expand`'s own opacity below. `--motion-scale`
     (Slice 12) and the reduced-motion block further down both govern every
     animation this file adds, so the whole pill's motion is decided in two
     places, not a dozen. */
  max-width: 320px;
  transition:
    border-color calc(0.2s * var(--motion-scale)) ease-out,
    background-color calc(0.2s * var(--motion-scale)) ease-out,
    color calc(0.2s * var(--motion-scale)) ease-out,
    width calc(0.24s * var(--motion-scale)) ease-out,
    padding calc(0.24s * var(--motion-scale)) ease-out,
    right calc(0.24s * var(--motion-scale)) ease-out;
}
```

with:

```css
/* Slice 28: circular and top-left by default (this IS the "active"
   shape - working, a sticky notification, or the panel manually open,
   the three cases that together make up "not docked") - `.statuspill--
   docked` below overrides width/radius for the idle pull-tab instead of
   this rule growing to fit a label anymore; there is no label rendered
   inline in either state now; see the design spec §2 for why. */
.statuspill {
  position: absolute;
  top: 14px;
  left: 22px;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border-radius: 50%;
  border: 1.5px solid var(--pill);
  background: color-mix(in srgb, var(--pill) 12%, var(--panel));
  color: var(--pill);
  box-shadow: var(--shadow-sm);
  /* Never intercept a click meant for the content behind it - it reports,
     it is not a control, EXCEPT while docked, which overrides this back
     to `auto` below (spec's own "obvious click target" requirement,
     unchanged from before this slice). */
  pointer-events: none;
  transition:
    border-color calc(0.2s * var(--motion-scale)) ease-out,
    background-color calc(0.2s * var(--motion-scale)) ease-out,
    color calc(0.2s * var(--motion-scale)) ease-out,
    width calc(0.24s * var(--motion-scale)) ease-out,
    border-radius calc(0.24s * var(--motion-scale)) ease-out;
}
```

Replace the docked rule and its two immediate followers. Replace:

```css
/* Slice 17 - the docked/active/finishing states from the design spec's §4.
   Docked is a thin, glyph-free sliver flush with the right edge: `right`
   moves the whole pill out to the viewport edge, and shrinking width/
   padding while leaving the icon/expand/chevron content mounted (just
   clipped by `overflow: hidden`) is the same "animate what's already
   there, never mount/unmount" discipline the rest of this file already
   uses for the check/dot/ring slot. `pointer-events` flips to `auto` here
   specifically - the base `.statuspill` reports and is not a control, but
   spec §4 asks the docked sliver to be "wide enough to be an obvious
   click target", so this is the one state where the pill itself becomes
   interactive rather than only the buttons inside it. */
.statuspill--docked {
  width: 14px;
  min-width: 14px;
  padding: 0;
  right: 0;
  border-radius: 999px 0 0 999px;
  overflow: hidden;
  pointer-events: auto;
  cursor: pointer;
}

.statuspill--docked .statuspill__icon,
.statuspill--docked .statuspill__expand {
  opacity: 0;
  width: 0;
}
```

with:

```css
/* Slice 28: idle's own shape - a narrow pull-tab, near the corner but
   deliberately not flush against it (spec §1 - the previous build's
   `right: 0` put it flush with the edge; this removes that entirely
   rather than mirroring it to `left: 0`). The gentle periodic tug
   (`statuspill-pull`) is the "grab me" affordance that used to come from
   sitting right at the edge - now that it doesn't, the pill needs to ask
   for the click a different way. `pointer-events: auto` is unchanged
   from before this slice: idle can still have something to open (a
   queued-but-not-yet-running activity keeps `detailAvailable` true even
   while `docked`), so this stays clickable rather than assuming idle
   never has anything to show. */
.statuspill--docked {
  width: 14px;
  border-radius: 999px;
  overflow: hidden;
  pointer-events: auto;
  cursor: pointer;
  animation: statuspill-pull calc(3.2s * var(--motion-scale)) ease-in-out infinite;
}

.statuspill--docked .statuspill__icon {
  opacity: 0;
}

@keyframes statuspill-pull {
  0%,
  100% {
    transform: translateX(0);
  }
  50% {
    transform: translateX(3px);
  }
}
```

Remove the now-dead `.statuspill__expand`/`.statuspill__label`/
`.statuspill__clock`/`.statuspill__more` rules - nothing renders inside
`.statuspill__expand` anymore after Step 1.2. Replace:

```css
.statuspill__expand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  max-width: 0;
  margin-left: 0;
  overflow: hidden;
  white-space: nowrap;
  opacity: 0;
  transition:
    max-width calc(0.22s * var(--motion-scale)) ease-out,
    margin-left calc(0.22s * var(--motion-scale)) ease-out,
    opacity calc(0.16s * var(--motion-scale)) ease-out;
}

.statuspill__expand.is-expanded {
  max-width: 270px;
  margin-left: 9px;
  opacity: 1;
}
```

with nothing (delete the block). Its two siblings, immediately after it,
go the same way. Replace:

```css
.statuspill__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: pill-pop calc(0.28s * var(--motion-scale)) ease-out;
}

.statuspill__clock,
.statuspill__more {
  opacity: 0.8;
}
```

with nothing. `@keyframes pill-pop` (defined earlier in this file,
directly above `.statuspill--docked`'s original location) has no other
caller once `.statuspill__label` is gone - confirmed via search, not
assumed - remove that definition too, in the same edit:

```css
@keyframes pill-pop {
  from {
    opacity: 0;
    transform: translateX(-4px) scale(0.96);
  }
}
```

(Confirmed against the file directly - this is the real body, not a
guess.)

Do NOT remove `.statuspill__icon`,
`.statuspill__check`/`__dot`/`__ring`, or `.statuspill__chevron*` - all
still render and still matter.

Add the new animation to the existing reduced-motion block, directly
after this component's CSS section (search for `@media
(prefers-reduced-motion: reduce)` immediately following
`.statuspill-detail__ack:hover` - confirmed present at this location
during investigation). Replace:

```css
@media (prefers-reduced-motion: reduce) {
  .statuspill {
    transition: none;
  }

  .statuspill__check,
  .statuspill__dot,
  .statuspill__ring,
  .statuspill__expand,
  .statuspill__label,
  .statuspill__icon,
  .statuspill__chevron {
    transition: none;
    animation: none;
  }

  .statuspill-detail {
    animation: none;
  }
```

with:

```css
@media (prefers-reduced-motion: reduce) {
  .statuspill,
  .statuspill--docked {
    transition: none;
    animation: none;
  }

  .statuspill__check,
  .statuspill__dot,
  .statuspill__ring,
  .statuspill__icon,
  .statuspill__chevron {
    transition: none;
    animation: none;
  }

  .statuspill-detail {
    animation: none;
  }
```

(`.statuspill__expand`/`__label` dropped from this list along with the
rules themselves - nothing left to disable motion on.)

### Step 1.2 — StatusBar.tsx: drop inline text, carry it as `aria-label`

Update the module docstring's position note. Replace:

```typescript
/**
 * The floating status pill: what the backend is doing, top-right of the
 * content area.
```

with:

```typescript
/**
 * The floating status pill: what the backend is doing, top-left of the
 * content area (Slice 28 - was top-right).
```

Replace the `expanded` computation and the return's `aria-label` /
`.statuspill__expand` block together - the label text that used to
render visibly now becomes the ONLY place that text exists, so it has to
move into `aria-label` or a screen reader loses information sighted
users can still get from the click-to-expand panel, not just lose a
visual nicety. Replace:

```tsx
  // The part that grows and shrinks: the label, the clock, and the "+N"
  // overflow count. Computed once so the JSX below and the width-animating
  // wrapper agree about when there is anything in it to show.
  const expanded = label !== "" || activeLead !== null;

  return (
    <div
      className={`statuspill statuspill--${kind}${docked ? " statuspill--docked" : ""}`}
      role="status"
      aria-live="polite"
      // Idle drops the visible word, and the mark itself is `aria-hidden`,
      // so without this the pill would announce nothing at all to a screen
      // reader at rest. The visible text carries it in every other state.
      // Docked has neither glyph nor label (spec §4), so it gets its own
      // wording rather than the bare "Ready".
      aria-label={docked ? "Ready" : kind === "ready" && !working ? "Ready" : undefined}
    >
```

with:

```tsx
  // Slice 28: the circle carries no visible text in any state anymore -
  // this is now the ONLY place the label/clock ever reach the user who
  // hasn't clicked the chevron, so it has to say what used to be visible,
  // not just "Ready"/undefined for the states that previously showed
  // nothing. Docked still gets its own fixed wording (nothing meaningful
  // to report while idle); every other state uses the same `label` text
  // that used to render inline, with the same "…" for an in-progress
  // clock the visible version used to add.
  const pillAriaLabel = docked
    ? "Ready"
    : label !== ""
      ? `${label}${activeLead !== null ? "…" : ""}`
      : kind === "ready"
        ? "Ready"
        : undefined;

  return (
    <div
      className={`statuspill statuspill--${kind}${docked ? " statuspill--docked" : ""}`}
      role="status"
      aria-live="polite"
      aria-label={pillAriaLabel}
    >
```

Remove the now-inert `.statuspill__expand` block entirely. Replace:

```tsx
      <span className={`statuspill__expand${expanded ? " is-expanded" : ""}`}>
        {label !== "" && (
          // Keyed so the label replays its entry each time the pill changes
          // between resting and working, rather than swapping text in place.
          <span className="statuspill__label" key={working ? "working" : "resting"}>
            {label}
            {activeLead !== null && "…"}
          </span>
        )}
        {activeLead !== null && (
          <span className="statuspill__clock mono">{elapsedClock(activeLead.startedAt, now)}</span>
        )}
        {activeLead !== null && activities.length > 1 && (
          <span className="statuspill__more">+{activities.length - 1}</span>
        )}
      </span>
```

with nothing (delete it). This also makes the ticking clock state that
fed it entirely dead - `now` has no other reader anywhere in this file
once this is gone (confirmed by search, not assumed), which means the
`setInterval` that updates it every second is now doing work nothing
observes. Replace:

```typescript
  const activities = useActivities();
  const [now, setNow] = useState(() => Date.now());
  const [detailOpen, setDetailOpen] = useState(false);

  useEffect(() => {
    if (activities.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [activities.length]);
```

with:

```typescript
  const activities = useActivities();
  const [detailOpen, setDetailOpen] = useState(false);
```

(`activities` itself stays - still feeds `leadActivity(activities)`
right below and `activities.length` inside `detailAvailable` further
down, both unrelated to the clock.) `elapsedClock` becomes unused too -
remove it from the `./activity` import. Replace:

```typescript
import { elapsedClock, leadActivity, useActivities } from "./activity";
```

with:

```typescript
import { leadActivity, useActivities } from "./activity";
```

Check whether `useEffect` itself is still imported/used elsewhere in this
file after removing the block above (it very likely is, for other
effects already in this component) before touching that import line.

### Step 1.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** idle sits
top-left, narrow, with a visible slow tug; hovering/reading its
accessible name (e.g., via a screen reader or the browser's accessibility
inspector) still announces "Ready". Start a render - the pill morphs into
a circle in place (same top-left anchor, no jump across the screen); its
accessible name announces the label text that used to be visible, "…"
included while still running. Click the chevron (still present whenever
`detailAvailable`) - the same detail panel opens, showing the label/
clock/count that used to be inline. `prefers-reduced-motion: reduce`
stops the pull tug the same way it already stops every other statuspill
animation.

### Step 1.4 — commit

`feat: circular active status pill, top-left pull-tab when idle — Slice 28`

---

## Task 2 — Update progress ledger

### Step 2.1

Update `.superpowers/sdd/progress.md`: Slice 28 complete - note the
accessibility carry-over (inline text → `aria-label`) explicitly, since
it's easy for a future reader to miss that the removed visual text still
exists somewhere.

### Step 2.2 — commit

`docs: Slice 28 complete — status pill circular/pull-tab`
