# Slice 28 — Status pill: circular active, pull-tab idle: Design

**Goal:** the status pill (`web/src/StatusBar.tsx`) becomes a circle while
something is running, and a top-left pull-tab while idle, instead of the
current always-top-right elongated pill.

## 0. What's already there

The component already has almost the exact state split this item asks
for - it's just shaped and positioned differently than asked. `docked`
(`kind === "ready" && !working && !hasSticky && !panelOpen`) is already
computed, already means exactly "idle, nothing to show, nothing open,"
and already renders as a content-free sliver
(`.statuspill--docked`/`.statuspill__chevron--fill`). The ring/check/dot
composite that indicates working/ready/error is already circular and
already animates between its three states via opacity/transform on
already-mounted elements, never a remount. The click-to-expand detail
panel (`.statuspill-detail`) already exists and already lists every
running/queued activity with its own label, detail, and percent.

So this slice is a shape-and-position change built on existing state,
not a new state machine: `docked` → the idle pull-tab (Task 1); `!docked`
→ the circular active badge (Task 2). Both anchored at the same top-left
point, so the transition between them is a shape/size morph in place, not
a cross-screen relocation - the existing "transform, not a mount"
philosophy extends naturally to "transform, not a jump across the
screen" too.

## 1. Idle - top-left pull-tab

Reposition: `.statuspill`'s `top: 14px; right: 22px` becomes `top: 14px;
left: 22px` - same inset, mirrored side, per direct confirmation rather
than guessed.

Pull motion: a slow (`~3.2s`), subtle horizontal tug -
`@keyframes statuspill-pull`, translating a few pixels and back, eased,
looping only while docked. Uses `--motion-scale` for its duration
(the existing convention every other timed animation in this file
already follows) and gets added to the statuspill-specific
`prefers-reduced-motion: reduce` block that already exists (right after
this component's own CSS section) rather than a new query.

## 2. Active - a circle, not a pill

`!docked` is "active" in the broadest sense the item means - working,
a sticky error/notification, or the panel manually opened - all three
already share the one thing that matters here: something worth a glance
even without reading. Resolved (confirmed, not assumed): the label,
elapsed clock, and "+N" overflow count that currently render inline stop
rendering inline entirely when active. They already exist, unchanged, as
rows inside the click-to-expand detail panel (`stickyHead`, the
per-activity list) - the circle doesn't need to duplicate that content
next to itself, it needs to be a clear enough "something's happening,
click to see what" indicator on its own, backed by the same ring/pulse
animation that already communicates "working" today.

`width === height`, `border-radius: 50%`, sized to comfortably hold the
existing 13px icon plus real click-target padding - `34px`, in line with
this file's other circular controls (`.quickpanel-toggle` at 26px,
`.health-stage__chevron` at 24px; a persistent, always-visible status
indicator reads as the more prominent of the three, not the smallest).
`.statuspill__expand` (the label/clock/count wrapper) stops rendering at
all while active rather than collapsing to zero width, since there's
nothing conditionally sized left in it to animate.

The chevron (click-to-expand) stays exactly as it already is - still the
one way to open the panel, still absent when there's genuinely nothing to
show, unaffected by this slice.

## 3. Testing

No frontend test runner (confirmed Slice 22) - `tsc -b` + `oxlint` clean,
plus manual checks: idle sits top-left with a visible, subtle periodic
tug; starting a render morphs it into a circle in place (no jump to the
other side of the screen); the circle's ring animates the same way the
existing "working" indicator already does; clicking the chevron still
opens the same detail panel, now showing the label/clock/count that used
to render inline; `prefers-reduced-motion: reduce` stops the pull tug the
same way it already stops every other statuspill animation.

## 4. Out of scope

- Any change to the detail panel's own content or the activity-row data
  it already renders - it already has everything the compact view is
  giving up.
- The sticky-notification acknowledgment flow (`statuspill-detail__ack`)
  - unaffected, still opens the same way.
