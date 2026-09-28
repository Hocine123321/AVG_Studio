# AVG Studio (iOS) — Skin v2: full redesign from the owner's HTML mock

The owner hand-designed a complete, clickable HTML/CSS/JS prototype (not
looking at this codebase) and wants the real app to match it pixel-for-pixel
in look AND behavior. This supersedes `UI_POLISH_PLAN.md`'s remaining scope
(that plan's Phases 1-3 already landed; its one open item, a Dynamic Type
simulator check, still stands and isn't touched by this).

The mock is `incoming_files/31cde4b51_AVG20Studio20for20iOS.html` from the
2026-09-28 "rewrite the skin" conversation - re-read it directly if a detail
here is unclear rather than trusting this summary alone.

## What the mock actually specifies (read once, applies everywhere)

- **Palette** (light/dark pairs): bg1 `#f4eee5`/`#14110d`, bg2 (olive-tinted)
  `#e4eedb`/`#1b2418`, ink `#241a12`/`#f6efe6`, i2 (secondary text) is ink at
  60%/62% alpha, gl (glass fill) white at 50%/9%, ed (glass edge) white at
  75%/22%, sh (shadow) is a warm brown/black at 16%/45%, fl (subtle fill -
  segmented-inactive, switch track, chip bg) is ink/white at 7%/8%. Single
  accent `#e08a4e` (warm amber) - **no more three-hue accent/violet/teal
  system**, `ok` (success) `#6f8757`/`#aebf92`, `bad` (danger) `#c4544a`.
- **Display font**: Caprasimo (bundled, OFL-licensed, `Resources/`) for
  every screen title (`.lt`) and sheet heading (`.sh3`), regular weight, NOT
  bold. Body text stays system (SF Pro / San Francisco).
- **Glass**: NOT Apple's Liquid Glass API (`.glassEffect`) - the mock hand-
  rolls its own recipe (26px blur + 190% saturate, translucent fill, .5px
  edge, inset top highlight + outer shadow, a diagonal white sheen overlay)
  that doesn't match Liquid Glass's system rendering. Pixel-fidelity means
  rebuilding this recipe by hand (`.background(.ultraThinMaterial)` + custom
  fill/border/shadow/sheen), not tinting the system material.
- **Background**: solid diagonal gradient (bg1→bg2) plus exactly TWO large
  blurred "orb" blobs (amber top-right, olive bottom-left, 300px, 70px blur,
  opacity .45/.3) that drift via one CSS keyframe (`translate(-40px,60px)
  scale(1.15)`, 18s ease-in-out alternate) - simpler and more specific than
  the 4-blob version just built for the old palette; replace it, don't layer
  on top of it.
- **Navigation**: floating pill title bar that fades in on scroll (not a
  standard nav bar); a floating pill tab bar, **five** tabs in this order -
  Library, **Studio**, Create (+), Channels, Settings. Tapping a library
  card sets Studio's "current project" and switches to the Studio TAB - it
  does not push a detail screen. This is a real navigation model change
  (`AppTab` needs a `.studio` case), not just a skin - implementing as drawn
  since it's unambiguous in the mock, not asking first.
- **Sheets**: nearly every picker (channel, model, image model, voice, API
  key entry, phase file list, export options) is a bottom sheet with a grab
  handle over a scrim, not a pushed screen or a `Menu`.
- **Toasts**: a new transient-notice primitive (top-center pill, auto-
  dismiss) - doesn't exist in the app at all yet.
- **Selection mode**: an explicit toggle (top-right icon in the title bar)
  puts Library into multi-select with a dedicated floating "N selected /
  Archive" bar - more than the current per-card swipe/context-menu archive;
  add this without removing the existing per-card actions.
- **Settings**: single page again (mock shows ONE screen with a
  Channel/Global segmented mode switch), not the three-page split Phase 2
  of the old plan did. Re-merging is an intentional read of the mock, not a
  mistake - the old split solved a real clutter problem a different way;
  this replaces that structure per the new design.

## Known mock-vs-real mismatches (adapt to real data, don't fake it)

- Mock's Studio "Pipeline" list has 8 rows (Brief/Script/Director I/Director
  II/Visuals/Voice/Captions/Render) with individual done/live state. Real
  `PhaseID` only has 5 stored phases (writing/visuals/voice/captions/render)
  - Brief/Script/Director I/Director II are sub-steps INSIDE `.writing`,
  tracked only as progress-message text (`store.jobs[id].message`), not as
  separate persisted statuses. Decision: keep the real 5-row granularity,
  port the mock's exact row styling (icon circle, pulsing "live" state,
  inline progress bar, trailing done/percent text) to those 5 rows, and
  surface the live sub-step message as StudioView already does today. Not
  building fake finer-grained persisted phase tracking for this.
- Mock's Settings "Model cache" (Groq 5/10 proven, rebuild-cache button with
  a fake progress timer), "API keys" list (Groq/Gemini/HuggingFace/
  Pollinations/ElevenLabs with Connected/Add-key chips), and "App health"
  screen's provider list/error log are ALL demo placeholder data in the
  mock's JS (`MD`, `KEYS`, `cp`, fake error log strings) - port the UI
  pattern (rows, chips, sheet-based key entry), wire it to whatever real
  equivalents already exist in `ModelCatalog.swift`/`HealthView.swift`/
  `Keychain.swift`, and only invent new real state if a real feature is
  genuinely missing (flag here if so, don't silently fabricate fake counts).
- Mock's Library empty-state copy, project list, channel names, etc. are all
  placeholder demo content - use real `Store.projects`/`Store.channels`.

## Phases (each gets its own commit + green build before the next)

1. **Foundation** - `Theme.swift` full rewrite: new color tokens (dynamic
   light/dark via `UIColor` trait closures), Caprasimo font registration +
   `Theme.disp(size:)` helper, hand-rolled glass card/pill modifiers
   replacing `.glassEffect` call sites, rewritten `AppBackground` (2-orb
   version), toast primitive, bottom-sheet presentation helper, segmented-
   control/chip/button style helpers. Nothing else can look right until
   this lands.
2. **Navigation shell** ✅ done (`<pending>`) - `Store.swift`/`App.swift`/
   `HomeView.swift`/`CreateView.swift`/`StudioView.swift`: added
   `AppTab.studio` + `Store.currentProject`/`selectForStudio(_:)` (the
   mock's `S.cur`/"pick"), Library's card tap and the interrupted-project
   Resume button now switch to the Studio tab instead of pushing a screen,
   Studio itself dropped its `projectID` init param in favour of always
   showing `store.currentProject`, and `App.swift` gained a custom
   `FloatingTabBar` (the mock's `.tabbar.g`) replacing the native tab bar
   chrome (`.toolbar(.hidden, for: .tabBar)` + `.safeAreaInset`), 5 tabs in
   the mock's order. Icons are SF Symbols matching the mock's ICONOGRAPHY
   (grid/clapperboard/plus/people/gear), not literal ports of its hand-
   drawn SVG paths - no native equivalent to "trace" a web glyph's exact
   vector, and Apple's HIG favours system symbols over custom glyphs
   anyway. Removed `Store.libraryPath` entirely (only existed to drive the
   old push-to-Studio navigation). Did NOT build the floating
   scroll-reveal title bar yet - deferred to land per-screen alongside each
   screen's own restyle (Phases 3+), since it needs that screen's own
   scroll offset, not a standalone piece.
3. **Library** ✅ done (16ae255) - `HomeView.swift`/`Theme.swift`: search
   field, All/Running/Done/Archived SegmentedFilter (replaced the old
   Archive-toggle button entirely), restyled ProjectCard (status chip +
   selection dot + date/aspect overlay on the thumbnail, real progress
   bar), selection mode + floating "N selected/Archive" bar, honest
   subtitle (real counts, no fake disk-size/job-slot numbers - flagged
   above). Added reusable `SegmentedFilter<T>`, `.fieldStyle()`, generic
   `.glass(shape:)`, `Theme.onAccent` to Theme.swift for later phases to
   reuse. Still has the native `.navigationTitle` bar, not yet the mock's
   custom floating scroll-reveal title - still deferred (see Phase 2's
   note).
4. **Studio** ✅ done (1d6b660) - `StudioView.swift`: each of the 5
   stored `PhaseID` rows is now its own floating glass card with a
   pulsing/filled `PhaseIcon`, a real per-phase subtitle, and an honest
   trailing Done/percent/dash (kept the real per-take horizontal scroller
   rather than the mock's fixed 2-take segmented control - a real N-takes
   feature, not a downgrade). Added the mock's always-visible "Live list"
   card (`store.projectLogTail`, last 6 lines) alongside the existing
   full-history Log sheet. Consolidated Save-to-Photos/CapCut/Share into
   one Export sheet (first real use of Phase 1's `GlassSheet`). Kept the
   real Render/Render-again/Cancel trigger the mock's own demo doesn't
   need (its projects auto-progress through a queue this app doesn't
   have) - removing it would have broken starting or retrying a render.
   Tapping a non-running phase row now confirms via `confirmationDialog`
   before redoing it, preserving the old Menu's one-extra-step safety net
   instead of a bare single-tap redo.
5. **Create** ✅ done (43219cc) - `CreateView.swift`: topic/title fields
   get the FULL glass treatment (the mock's own `field g`, distinct from
   Library's flatter `.fieldStyle()` search box), `SegmentedFilter
   <AspectRatio>` for format (kept Square alongside the mock's 9:16/16:9 -
   a real option its demo just doesn't cover), Channel is a real
   interactive `GlassSheet` single-choice picker. Writer/Images/Voice
   became real but READ-ONLY rows that jump to Settings instead of the
   mock's inline per-creation model pickers - this app's provider choice
   is a GLOBAL `Store.settings` field, not a per-video draft config, so a
   fake inline picker here would silently do nothing. Dropped the mock's
   Scenes stepper (no fixed scene-count setting exists - the AI script
   decides) and its static per-phase cost table (no real number is known
   before a run) entirely rather than fabricating either.
6. **Channels** - `ChannelsView.swift`: active-channel list + edit chevron,
   "Global defaults" entry row, matching card/row style. Channel editor
   sheet itself (already reduced to 8 sections in the old plan) gets the new
   visual skin, not a re-split.
7. **Settings** - `SettingsView.swift`: re-merge the 3-page split back into
   one page with the Channel/Global segmented mode switch, matching the
   mock's section list (Writing/Visuals/Voice/Captions groups → API keys →
   Model cache → Library → Appearance incl. Night/Day/Auto → App health
   link).
8. **App health** - `HealthView.swift`: 3-up status tiles + provider list +
   error log card, restyled.
9. **Welcome / remaining polish** - `WelcomeView.swift` restyle; sweep for
   any leftover `Theme.violet`/`Theme.teal` call sites once every screen
   above is done and decide case-by-case (both constants stay defined for
   now so nothing breaks mid-migration).

## Notes

- Reduce Motion must keep working (orb background AND toast/sheet
  animations) - already a hard requirement from the old plan, still is.
- `git fetch` + rebase before starting each phase - other sessions may land
  work on `ios-native` in parallel (see `UI_POLISH_PLAN.md`'s own sync note).
