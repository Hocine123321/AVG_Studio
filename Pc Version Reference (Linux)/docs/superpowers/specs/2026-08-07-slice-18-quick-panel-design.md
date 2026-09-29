# Slice 18 — The Quick panel: Design

**Goal:** put the tools the user reaches for mid-task — the Live List, a preview,
a handful of settings, recent failures — one keystroke away on every screen,
instead of behind a navigation away from what they were doing.

---

## 0. Where this sits

Second of the three slices agreed on 2026-08-07 (see the Slice 17 spec for the
full split). Slice 17 owns the status pill and the activity feed. This slice
owns the floating panel. Slice 19 owns Settings.

**This slice takes two things out of Settings** — the Live List opener and the
Preview panels — which is what makes Slice 19's cleanup tractable. Slice 19
must not put either back.

## 1. What the panel is, and is not

An **in-app floating panel**: a portalled overlay inside the existing WebView2
window. **Not** a second OS window, and not a `pywebview` child window.

- **Non-modal.** No backdrop, no focus trap. The page behind stays live and
  usable — this is a tool palette, not a dialog. A modal would defeat the point,
  which is doing something *while* looking at something else.
- **Above everything**, including the status pill's detail panel. The pill is
  status; the Quick panel is work.
- **Draggable anywhere inside the app window**, and clamped to it: a panel
  dragged off-screen in a window that cannot be scrolled is unrecoverable.
- **Available on every screen.** It lives in `App.tsx` beside the shell, not
  inside any one screen's tree, so switching screens never unmounts it or loses
  its tab and position.

**Opening and closing.** A `<` button in the upper-left opens it; it slides in
from the upper-left and slides back out the same way on close. `Escape` closes
it, because a floating thing that cannot be dismissed from the keyboard is a
trap.

### A conflict to settle before implementation

**Slice 19 also puts something in the upper-left**: the sidebar "A" logo becomes
the app's main menu, replacing the "My Videos" button. Two controls in one
corner, both introduced in the same batch of work.

They can coexist — the logo is *in* the sidebar, the `<` sits at its edge — but
this is a layout decision that wants eyes, not a guess. **Recorded as open.**
The safe default if it is not settled by implementation time: the `<` goes at
the sidebar's outer edge, vertically below the logo, so the logo keeps the
corner it already occupies.

## 2. State, and where it lives

| State | Home | Why |
|---|---|---|
| Open/closed, active tab, drag position | `localStorage` | UI state, not user configuration. It has no business in the settings catalogue, which is the single source of truth for *configurable* settings and renders itself from `GET /api/settings/schema`. A `SettingSpec` for "where did I leave a panel" would be noise in that catalogue forever. |
| Which settings appear in Quick Settings | A new `quick_settings.json` | User-owned configuration that is a *list of setting ids*, which no `SettingSpec` type can express. Follows `avg/config/channel_defaults.py`'s shape exactly: one module owning one document, replaced wholesale, atomic write. |

Position is clamped on read as well as on drag: a window resized smaller
between sessions must not restore a panel outside it.

## 3. The tabs

### 3.1 Live List — moved, not rebuilt

The existing `LiveList` component moves in whole. Its Settings opener
(currently rendered under the `models` group) goes away.

**Plus the tag filter** (the batch's Addition 4). The filter offers five tags,
and they are **not one axis** — this is the design point that matters:

- **Provenance**, one of four, mutually exclusive per entry, already computed
  by `_entry_rows`: `verified` (`probed`), `listed` (`catalogue`), `added by me`
  (`manual`), `suggested`.
- **Ticked**, an orthogonal boolean — `ModelPrefs.selected`, the user's own
  choice about what pickers may offer.

So selecting several provenance tags is an **OR within that axis**, while
adding `ticked` is an **AND across axes**: "verified or suggested, and ticked".
No tag selected means the filter is off and everything shows.

This is stated because the obvious implementation — one flat list of five
checkboxes ANDed together — makes `verified` + `listed` return nothing at all,
since no entry is both.

The filter is client-side over rows already fetched. It is a view, not a query:
the Live List's rows are already in memory, and a round trip per keystroke
would be slower and would put a filter concept into an endpoint that does not
need one.

### 3.2 Quick Settings — new

A user-chosen subset of the settings catalogue, rendered with the **same field
components Settings uses** (`Select.tsx`, `BoolField`, the rest). No second
renderer: two implementations of "draw a setting" would drift, and the
catalogue exists precisely so a setting is drawn from its spec.

Ships with a starter preset. The user adds or removes entries; the list is
stored in `quick_settings.json` as ids. An id that no longer exists in the
catalogue is dropped on read rather than rendered as a broken row — settings
have been deleted before, and three were deleted in one day on 2026-07-28.

**Editing here writes exactly where Settings would write it**, through the same
route. Quick Settings is a shortcut to the same values, never a parallel store.

### 3.3 Preview — moved, and given its own layout

The Live Preview panels move out of Settings. Today there are four mounts, each
inside the group whose settings it previews (`captions`, `overlay`, `images`,
`voice`), which is exactly why it is cramped: it renders between settings rows
in a column sized for form fields.

Freed from that, it gets a layout designed for it: the preview frame is the
subject, at the size the thing being previewed actually is, with its controls
beside rather than above it.

**One consequence to build deliberately.** Inside Settings, *which* preview to
show was implied by the surrounding group. In the Quick panel there is no
surrounding group, so the Preview tab needs its own kind picker — caption,
overlay, image, voice — using `Select.tsx`.

**The money rule is unchanged and must stay unchanged.** The voice sample and
the image sample cost real money, still render only on an explicit press, and
the backend still refuses both without `confirm_paid`. Nothing about rehoming
the panel may introduce a render-on-mount, a render-on-tab-switch, or a
render-on-settings-change for those two. The free previews keep re-rendering
live.

### 3.4 Recent Errors

A read of `GET /api/errors`, which already exists and returns recent failures
newest-first, with `DELETE` to clear. Slice 17 spec assigned this tab here
because a tab needs the panel it lives in.

Newest first, each row showing severity, source, summary, and the time. `detail`
is behind a disclosure rather than inline — a stack trace in a floating panel
would push everything else off it.

### 3.5 "In work" — a disabled placeholder

Present, visibly disabled, not clickable, with a word saying what it is for.
It exists so the tab bar's final shape is visible now rather than shifting when
the next feature lands.

## 4. Design language

- **No native form controls.** `Select.tsx` for every choice, `.switch` for
  every boolean, the hidden-input `.select-dot` pattern where a checkbox must
  exist for state or keyboard focus.
- **One filled `.btn--primary` per screen.** The Quick panel is an overlay on
  whatever screen is beneath it, and that screen already has its one. **So the
  panel has none** — its actions are plain `.btn`. This is the single easiest
  rule in this slice to break, since a panel feels like its own screen and is
  not one.
- Every value from `web/src/tokens.css`. The panel is a surface the app already
  has a vocabulary for: `--surface`, `--line-2`, `--radius-lg`, `--shadow-md`.
- The slide-in and slide-out multiply by `var(--motion-scale)`, are disabled
  under `prefers-reduced-motion`, and remain **visible** when frozen at
  `data-motion="none"` — a panel that animates in from off-screen and is frozen
  mid-flight would otherwise sit outside the viewport, which is precisely the
  bug Slices 13 and 14 each shipped once.

## 5. Testing

**Backend**, and mandatory: `quick_settings.json`'s module — load, wholesale
replace, atomic write, an unknown id dropped on read, and a missing file
reading as the starter preset rather than an error.

**Frontend** has no unit tests by convention: `npm run build`, `npm run lint` at
zero warnings, and DOM and computed-style measurement against the dev server —
that the panel mounts above the shell, that its position clamps, that no native
control appears, and that the screen beneath still has exactly one
`.btn--primary`.

**One test that is worth more than the rest:** that switching to the Preview tab
issues no paid request. The money rule survived Slice 14's review intact and
this slice moves the surface that spends.

**A visual pass is owed** and has not been possible recently — the Browser pane
does not composite on this machine. Recorded as pending rather than claimed.

## 6. Risks

**The panel can cover the thing it is previewing.** A floating window over a
single-window app has nowhere to go. Draggability is the mitigation, not a fix;
if it proves annoying, docking to one edge is the fallback, and that is a
bigger change than it sounds.

**Quick Settings can become a second Settings screen.** The starter preset
should stay small. The moment it holds most of the catalogue, the user has two
places to change everything and no reason to prefer either.
