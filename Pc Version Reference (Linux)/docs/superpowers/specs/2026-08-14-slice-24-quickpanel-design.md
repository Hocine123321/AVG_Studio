# Slice 24 — Quick Panel: resize, puller position, Preview refresh: Design

**Goal:** the Quick panel (`web/src/QuickPanel.tsx`) becomes resizable
with a sane minimum, its `<` puller (`Rail.tsx`) visually aligns with the
"A" logo instead of the Create Video button, and its Preview tab gets an
explicit Refresh control.

Covers backlog items #4, #14, #19 - one slice because all three are the
same component family (`QuickPanel.tsx` / `Rail.tsx` / `PreviewTab.tsx`,
all styled from the same block of `tokens.css`), continuing directly from
Slices 18-19's own Quick Panel work.

---

## 1. Puller position (#14) — a CSS number is wrong, not the architecture

Traced, not guessed. `Rail.tsx`'s JSX and its own comments already say
the puller renders "right after the logo" - the DOM order and intent are
correct. The bug is purely in `tokens.css`: `.quickpanel-toggle` is
`position: absolute; top: 78px`, a value that predates whatever last
changed the logo's own layout and simply doesn't correspond to it
anymore. The logo (`.rail__logo`) sits at the rail's `16px` top padding,
is `38px` tall - vertical center at `35px`. The toggle is `26px` tall;
centered against the logo that's `top: 22px`, not `78px`. At `78px` it
falls squarely inside the Create Video button's own 48px row instead -
exactly the reported symptom.

**Fix:** `top: 78px` → `top: 22px`. `right: -13px` (the horizontal
half-overlap of the rail's edge) is unrelated and unchanged. One-line
CSS fix; `Rail.tsx` itself needs no change.

## 2. Resizable, wider minimum (#4)

Currently `.quickpanel` is a hardcoded `width: 380px` (mirrored in
`QuickPanel.tsx`'s own `FALLBACK_WIDTH` constant) with a content-driven
height capped at `max-height: 70vh` - there is no resize affordance at
all today.

Adds a corner drag handle (bottom-right, the universal `<textarea>`
resize convention), reusing the exact pointer-capture technique the
header drag already uses (`setPointerCapture`/`onPointerMove`/
`releasePointerCapture`) rather than a new drag implementation. Both
width and height become resizable, each independently bounded:

- Width: `MIN_WIDTH = 340px`, `MAX_WIDTH = 640px`. 340 is a judgment
  call, not measured against a running build - reasoning: the five tab
  labels (`Live List`, `Quick Settings`, `Preview`, `Recent Errors`,
  `In work`) are the widest thing the header ever has to fit, and 340
  leaves real margin below the current fixed 380 without inviting the
  wrap the item explicitly warns about. Confirm against the actual
  rendered tab strip during implementation, not just this reasoning, and
  adjust the constant if it wraps in practice.
- Height: `MIN_HEIGHT = 320px`, `MAX_HEIGHT = 80vh` (a hair past today's
  70vh cap, since a deliberately-resized-taller panel asking for more
  room than the previous fixed cap allowed is the entire point).

Persisted the same way position already is - a new `SIZE_KEY` in
`localStorage`, following `POSITION_KEY`'s exact pattern (the "does the
architecture have an appropriate place for UI preferences" the item asks
about - yes, this is it, already established, not a new one). Resizing
re-clamps position afterward through the existing `clamp()` function
(same call already made on window resize), so growing the panel near a
screen edge can't push it off-screen.

**Preview tab must not break:** `PreviewTab`'s frame
(`.quickpanel-preview__frame`) and each preview kind's own frame sizing
need checking against the new resize range during implementation -
flagging this as a thing to verify, not a code change decided here,
since it depends on how those frames are actually sized (percentage vs.
fixed) once looked at directly.

## 3. Preview refresh (#19) — respecting the money rule

`PreviewPanel.tsx` already auto-refreshes two of its four kinds -
`CaptionPreview` and `OverlayPreview` both re-fetch on a debounced
`useEffect` keyed to the relevant setting values. The other two,
`VoicePreview` and `ImagePreview`, deliberately do NOT auto-refresh -
they're paid generations, gated behind their own explicit "Generate a
sample" button, and the module's own docstring states the invariant
plainly: "neither VoicePreview nor ImagePreview has any effect that
reaches `confirm_paid: true` - only their own 'Generate a sample' button
does... switching kind does not add a new path to either paid call."

So "changing settings does not automatically refresh" is only actually
true - and only should be true - for Caption/Overlay. A single top-level
Refresh button that's wired the same way for all four kinds would either
do nothing for two of them (confusing) or add a second path to a paid
action (a real regression against a stated invariant). Resolved design:

- One `Refresh` button in `PreviewTab`'s header, always visible (matches
  "the explicit Refresh button must remain available regardless").
- Bumps a `refreshKey` number, passed down through `PreviewPanel` to
  whichever kind is mounted.
- `CaptionPreview`/`OverlayPreview`'s existing debounced effects gain
  `refreshKey` in their dependency arrays - clicking Refresh re-runs the
  exact fetch they already do on a settings change, immediately rather
  than waiting for the debounce. No new fetch code.
- `VoicePreview`/`ImagePreview` ignore `refreshKey` entirely. Their
  already-generated sample keeps showing; nothing paid fires. Refresh
  being a visible no-op for these two kinds is the correct behavior given
  the money rule, not a gap to close - stated here so it isn't
  "discovered" as a bug during review.

## 4. Testing

No frontend test runner exists (confirmed Slice 22) - `tsc -b` + `oxlint`
clean, plus documented manual checks: puller visually centered on the A
logo; drag-resize respects both bounds and survives a reload (localStorage);
Preview tab's Refresh button visibly re-fetches for Caption/Overlay and is
inert (not broken, not paid) for Voice/Image.

## 5. Out of scope

- Changing `VoicePreview`/`ImagePreview` to auto-refresh - the money rule
  says they must not.
- Any change to the Quick panel's five tabs beyond Preview.
