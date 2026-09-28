# AVG Studio (iOS) — UI polish & QoL plan

Not a feature-parity task like the Director II port — this is about making the
app already ported feel *finished*: calmer, less cluttered, and alive. Written
before touching code, per the owner's request, since this touches several
screens over several sessions.

Current state (as of `dea3b97`): the app already uses iOS 26's Liquid Glass
throughout (`glassCard`/`glassEffect`/`GlassEffectContainer`), has a
first-run Welcome flow with staggered reveal animations, and a single
animated `AppBackground` mesh gradient behind every screen. It's in decent
shape already — this plan is about specific, scoped improvements, not a
rewrite.

## Phase 1 — background animation + quick wins ✅ done (`5b20f65`, bugfix `1ee9daa`)

1. **Gemini-style moving background.** The current `AppBackground` (Theme.swift)
   is a `MeshGradient` whose control points drift slowly via `sin`/`cos` — a
   good bones but the motion is subtle and the palette is static. Rework it to
   read more like Google Gemini's flowing backdrop:
   - Slowly rotating/shifting *hue*, not just anchor position, so the color
     mix itself evolves over time, not just where it sits.
   - Bigger, more organic drift amplitude on the control points (soft blob
     motion) with different periods per point so it never looks like it's
     visibly looping.
   - Respect **Reduce Motion**: freeze to a single still gradient frame
     instead of animating — this was already done for Welcome's entrance
     animations but the background itself currently ignores the setting,
     which is a real accessibility gap.
   - Keep it cheap: still one `TimelineView(.animation(minimumInterval:))`
     tied to a low frame budget (15fps is already fine), no extra GPU work.
2. **Discoverability: swipe actions on library cards.** `HomeView`'s
   `ProjectCard` only exposes Archive/Delete via long-press context menu,
   which many users never find. Add `.swipeActions` (or an equivalent glass
   affordance) alongside the existing context menu — don't remove the menu,
   just make the same actions reachable the obvious way too.
3. **Haptic feedback on key moments** — create → render start, render
   finish/failure, save-to-Photos. Small, cheap, makes the app feel more
   responsive. Use `UINotificationFeedbackGenerator`/`UIImpactFeedbackGenerator`,
   nothing fancier.

## Phase 2 — declutter Settings ✅ done (`d7dcc01`)

`SettingsView` is one long `Form` with 9 sections stacked top to bottom (AI
services, per-provider model pickers, narration, API keys, video quality,
filters, captions, reusable images link, health/about links). It works, but
it's the single most "everything at once" screen in the app. Plan:
- Split into a short top-level list of grouped destinations (mirrors how
  iOS's own Settings app organizes itself): **AI Providers** (writer/image/
  voice + their model pickers + API keys), **Video & Render** (quality, fps,
  Ken Burns, filters), **Captions**, and keep **Reusable images** / **App
  health** / **About** as they are today.
- No behavior changes — same toggles/pickers, just regrouped, so this is a
  pure navigation/structure change, low risk.
- Re-check each subpage still reads its state from the same `@State private
  var ...Catalog` instances (they're declared once in the parent view today
  for a reason — see SettingsView's own doc comment on why 5 small closures
  rather than 5 classes — moving them to child views needs the same
  survive-re-evaluation care).

## Phase 3 — remaining polish pass (in progress)

- [x] Empty/loading states checked: `ChannelsView` always has ≥1 channel from
  onboarding so an empty list isn't reachable in normal use; `ReusableImagesView`'s
  empty pool still shows its "Add from Photos" action + explanation. Both
  degrade reasonably as-is — no change made.
- [x] Section header casing audited across every `Form` — already consistent
  sentence case ("AI services", "Video", "Captions", "Apple voice", ...).
  Nothing to fix.
- [x] Accessibility: audited every `Image(systemName:)` in `UI/` for icon-only
  *tappable* controls (as opposed to decorative icons sitting next to text,
  which VoiceOver already reads via the label). Found and fixed exactly one
  gap — `ProjectCard`'s overflow `Menu` (`c1046b0`).
- [x] `ChannelEditor` sheet: had 11 Form sections, 4 of which (Brief/Script-
  Draft/Script-Segmentation/Direction) were the identical "3 pickers + header
  + footer" shape repeated 4 times. Merged into one "Per-phase models"
  section — 11 → 8 sections (`d95ed5e`→`28f044b` after a rebase). Did NOT do
  a full page-split like Settings: 8 sections in one sheet is a reasonable
  size, unlike Settings' 9 fully-independent sections; splitting further felt
  like manufactured work rather than a real win.
- [ ] Dynamic Type check on the card grid and Studio screen text — needs an
  actual Accessibility-size simulator run, can't be verified by reading code
  alone. Flagging for whoever has simulator access rather than guess-fixing.

## Sync note

Another agent/session is also pushing to `ios-native` in parallel (saw
`f8fabfb` "keep animated background mesh inside bounds" and `a6aa73b`
"avoid duplicate video encode and per-frame image work" land between two of
my sessions, both authored as `Hocine123321`, both green). `f8fabfb` is a
real fix on top of Phase 1's `AppBackground` rework — worth reading before
touching `Theme.swift` again. No conflicts so far since work has stayed on
different files, but always `git fetch` + rebase before starting, not just
before pushing.

## Notes

- Each phase gets its own commit + a GitHub Actions build check before
  moving to the next, same discipline as the Director II port.
- This plan lives at `ios/UI_POLISH_PLAN.md` — update the checked-off state
  here as phases land, instead of just letting it go stale.
