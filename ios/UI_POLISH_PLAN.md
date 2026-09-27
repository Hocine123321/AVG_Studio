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

## Phase 3 — remaining polish pass (next session)

- Empty/loading states: `ProjectCard`'s thumbnail has a placeholder gradient
  already; check other screens (Channels, Reusable images) have an equally
  considered empty state, not just `ContentUnavailableView` defaults.
  Check for a real loading skeleton before AVPlayer content loads.
- Typography/spacing consistency pass across all `glassCard()` sections —
  look for inconsistent header casing, padding, or icon sizing between
  screens now that there are ~10 of them.
- Accessibility: VoiceOver labels for icon-only buttons, Dynamic Type check
  on the card grid and Studio screen text.
- Re-verify `ChannelsView`'s already-long `ChannelEditor` sheet (Identity /
  Videos / Brand / Recycling / Cards / Images / Voice cleanup / 4 model
  sections) — likely the same "one screen holds everything" pattern as
  Settings; same grouped-navigation treatment once Settings' pattern is
  proven out.

## Notes

- Each phase gets its own commit + a GitHub Actions build check before
  moving to the next, same discipline as the Director II port.
- This plan lives at `ios/UI_POLISH_PLAN.md` — update the checked-off state
  here as phases land, instead of just letting it go stale.
