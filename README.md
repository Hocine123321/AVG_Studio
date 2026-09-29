# AVG Studio for iPhone: coding guide

A map, not a manual. `CLAUDE.md` says what the rules are and what must never
be done; this says **where everything is and how a change flows through it**,
so a change can be made without reading the tree first.

Anything explained here in detail is explained here *only*. Every file's own
header comment carries the reasoning, and this file points at it.

This is the native iOS port of the desktop app kept in
[`Pc Version Reference (Linux)/`](<Pc Version Reference (Linux)/>). That
folder is **read-only reference**: the Swift code cites the Python file and
function each piece replaces, and the bug it exists to prevent. Read the
comment before changing a file; it usually explains why the obvious approach
was rejected.

---

## 1. The system, in one paragraph

One process, one target, no server. `App.swift` builds a single `Store`
(`@MainActor @Observable`) and injects it into the environment; every screen in
`UI/` reads and writes through it. There is no HTTP layer and no WebSocket:
the `Store` **is** the boundary, and it plays the role that `avg/api/` plays on
PC. Below it, `Core/Pipeline.swift` runs the five phases, `Providers/` talks to
AI backends, and `Render/VideoRenderer.swift` draws frames with Core Graphics
and encodes them with the iPhone's hardware HEVC encoder, replacing the PC's
ffmpeg child process. Nothing below `Store` touches UI state: a phase reports
progress through a closure, failures surface as typed `AVGError`s, and the
`Store` decides what reaches the user, either a log line, a banner or a
`RecoverySheet`. Everything runs on-device. The only network traffic is the
provider calls, sent straight from the phone with keys held in the Keychain.

Layer rule: a file may use its own layer and any layer below it in this list,
never above.

```
AVGStudio/UI/         SwiftUI screens; read and write only through Store
AVGStudio/Core/       Store, Pipeline, Recovery, models, export, audio
AVGStudio/Render/     Core Graphics frames + AVAssetWriter HEVC + AVFoundation mux
AVGStudio/Providers/  text / image / voice / transcription backends,
                      HTTP transport, error taxonomy, model catalogues
```

Nothing in `Providers/` imports from `UI/`. `UI/` never calls a provider
directly, except `ModelPickerRow` and the catalogue refresh, which go through
`ModelCatalog`.

---

## 2. Every file, one line each

### `AVGStudio/Core/`: state, sequencing, vocabulary.

| File | Responsibility | Must not know about |
|---|---|---|
| `Models.swift` | Every `Codable` document: `Project`, `Take`, `PhaseRecord`, `Channel`, `AppSettings`, plus the enums (`PhaseID`, `MotionType`, `GradeLook`, provider kinds). Decoding is **tolerant**: a missing key takes its default | SwiftUI, providers |
| `Store.swift` | The one state container: projects, channels, settings, jobs, log, the recovery plan. Owns persistence and starts/cancels runs | Views |
| `Pipeline.swift` | `Pipeline.execute(_ phase:)` and the five phase bodies. Provider resolution, routing, cost recording, prompt-marker parsing | HTTP status codes |
| `Recovery.swift` | Maps a failure to a `RecoveryPlan` (nine safeguards), the fallback-model logic, `Connectivity` (`NWPathMonitor`), and `RecoverySheet` | Which view shows it |
| `BackgroundRender.swift` | `BGProcessingTask` registration and the "interrupted render" flag | Phase internals |
| `Keychain.swift` | **The only file that reads or writes API keys.** `APIKey`, `Keychain.get/set/has/require` | Providers' request shapes |
| `SilenceTrim.swift` | Dead-air removal on PCM audio (20 ms windows), the port of `remove_silences_core` | Where the audio came from |
| `CapCutExport.swift` | Builds the CapCut import bundle in `temporaryDirectory` | Share sheets |

### `AVGStudio/Providers/`: talking to the outside.

| File | Responsibility |
|---|---|
| `HTTP.swift` | One request (`HTTP.send`), and `HTTP.retrying`, the policy-driven backoff loop. `RenderLog` is a `@TaskLocal` logger so two projects never share a log |
| `ErrorPolicy.swift` | `classify()` says what a status/body means. `POLICY` says what to do about it. Nothing else greps an error string |
| `TextProviders.swift` | `AppleText` (on-device), `GeminiText`, `ChatText.groq`, `ChatText.pollinations` |
| `ImageProviders.swift` | `PollinationsImages`, `StableHordeImages`, `GeminiImages`, `OpenAIImages`, and `validImage(_:)` |
| `VoiceProviders.swift` | `AppleVoice` (on-device), `ElevenLabsVoice`, `GeminiVoice` (adds its own WAV header) |
| `TranscriptionProvider.swift` | `AssemblyAI.words(fromAudio:key:)` for word-level caption timing |
| `ModelCatalog.swift` | `ModelCatalog`: a live-fetched, disk-cached list backing a picker. One class, nine instances |
| `ModelCatalogSources.swift` | One fetch closure per catalogue (Gemini, Groq, ElevenLabs, Stable Horde, Pollinations) |

### `AVGStudio/Render/`.

| File | Responsibility |
|---|---|
| `VideoRenderer.swift` | `VideoRenderer.render(_:progress:)`. Per-frame Core Graphics drawing, Ken Burns, transitions, one-time Core Image grade bake, takeover cards, six caption animations, film-grain/light-leak filters, HEVC encode, passthrough mux |

### `AVGStudio/UI/`: the screens.

| File | Responsibility |
|---|---|
| `App.swift` (root) | `AVGStudioApp` and `RootView`: the five-tab `TabView`, onboarding gate, recovery sheet, scene-phase hook |
| `Theme.swift` | The design system (Skin v2): palette, glass, typography, background, toasts, sheets, haptics. **The authority on every colour and radius** |
| `HomeView.swift` | Library: search, filter strip, project grid, multi-select archive, interrupted-render banner |
| `StudioView.swift` | The current project: player, five phase rows, live list, takes, scenes, brief, export sheet |
| `CreateView.swift` | New-video form: topic, title, shape, channel |
| `ChannelsView.swift` | Channel list and `ChannelEditor` (identity, videos, brand colours and font, recycling, cards, image backup, voice cleanup, per-phase models) |
| `SettingsView.swift` | One page: Writing, Visuals, Voice, Captions, keys, model cache, appearance, health links |
| `HealthView.swift` | App Health: required and optional checks, summary tiles, recent activity |
| `WelcomeView.swift` | First-run five-step onboarding |
| `ModelPickerRow.swift` | A picker over a `ModelCatalog`, falling back to a plain text field |
| `ProjectLogView.swift` | The live log sheet |
| `ReusableImagesView.swift` | The recycle-pool manager (photo import, descriptions) |
| `ActivityShareSheet.swift` | `UIActivityViewController` wrapper |

### Build and resources.

| File | Responsibility |
|---|---|
| `project.yml` | XcodeGen spec. **`Info.plist` and `AVGStudio.xcodeproj` are generated from it and never committed** |
| `.github/workflows/ios.yml` | Builds an unsigned IPA on every push that touches `AVGStudio/**` or `project.yml` |
| `AVGStudio/Resources/` | `Caprasimo-Regular.ttf` and its OFL licence |
| `AVGStudio/Assets.xcassets/` | The app icon (light, dark and tinted variants) |
| `SKIN_V2_PLAN.md`, `UI_POLISH_PLAN.md` | The design plans. Skin v2 is complete; see §7 |

---

## 3. PC to iOS: where each piece went

The PC pipeline has eight phases; the iOS one has five. Nothing was lost by
accident. Each difference below is a decision recorded in a comment.

| PC | iOS | Difference |
|---|---|---|
| `brief` + `script` | `writing` | One phase: optional research, brief, narration, scene split (5 to 12 scenes) |
| `director1` | *not ported* | iOS always makes one image per scene (plus the recycle pool), so there is no structure to plan |
| `visuals` | `visuals` | Batches of 3 concurrent images via `withThrowingTaskGroup` |
| `voice` | `voice` | `[pause]` splits the narration into segments joined with real silent gaps; each is trimmed by `SilenceTrim` |
| `director2` | inside `render` (`planDirection()`) | Motion, transition and grade plan. On failure it logs and falls back to deterministic Ken Burns |
| `screen_text` | `captions` | AssemblyAI timing if a key exists, otherwise character-weighted estimate. Also builds the takeover-card plan |
| `render` | `render` | ffmpeg child process becomes `VideoRenderer` in-process |
| `avg/api/` | `Store` | No HTTP, no token, no WebSocket |
| `takes/<id>/` folders | `render/take_<id>.mp4` | A take is a rendered file, not a snapshot of every artifact. See §6 |
| `take.log` per take | one `take.log` per **project** | Each run starts with a `▶ Started…` separator line |
| DPAPI `secrets.dat` | Keychain | Service `com.avgstudio.keys` |
| Edge TTS | `AVSpeechSynthesizer` | On-device, free, always available |
| Groq, Hugging Face, Pollinations text | Apple Intelligence, Gemini, Groq, Pollinations | Hugging Face has no iOS backend, and its error patterns were left out on purpose |
| `SETTINGS` catalogue (75 specs) | hand-built `AppSettings` and `Channel` | See §5 |
| `invalidates` + clone-a-take | *not ported* | Tapping a phase row asks "Redo from X?" and re-runs X and everything after it. Phase files are overwritten; each render adds a new `take_<id>.mp4`, so earlier videos are kept |
| Pillow takeover-card renderer | Core Graphics, `drawCard` | Same rule: a card owns its scene's whole window |
| `film_grain.mov`, `light_leak.mov` | procedural 128×128 grain tile, light-leak gradient | No bundled video overlays |

---

## 4. The pipeline

Order is `PhaseID.allCases` in `Core/Models.swift`. It is the single source of
truth; `StudioView` and `WelcomeView` list the same five names in their own
copy, so change them together.

| # | `PhaseID` | What it does | Writes |
|---|---|---|---|
| 1 | `writing` | Optional topic research, ~120-word brief, narration (~150 wpm) with `[pause]` and `((question))` markers, then a JSON scene split. Falls back to `sentenceScenes` if the JSON fails | `project.json` fields |
| 2 | `visuals` | One image per scene. A `[RECYCLE #k]` pick reuses a pool image instead of generating | `images/scene_NN.jpg` |
| 3 | `voice` | Splits at `[pause]`, synthesises each segment, trims dead air per segment, joins with silent gaps in an `AVMutableComposition` | `audio/segment_N.caf`, `clean_N.caf`, `narration.m4a` |
| 4 | `captions` | Word timings (AssemblyAI or estimated), caption cues, takeover-card plan (~1 card per 3 scenes at most) | `project.json` fields |
| 5 | `render` | Direction plan, then `VideoRenderer`, then a new `Take` | `render/take_<id>.mp4` |

### Providers and fallbacks

| Kind | Providers | Keychain key |
|---|---|---|
| Text | Apple Intelligence (default), Gemini, Groq, Pollinations | `gemini`, `groq`, `pollinations` (optional) |
| Image | Pollinations (default), Stable Horde, Gemini, OpenAI | `pollinations`, `stablehorde` (optional), `gemini`, `openai` |
| Voice | Apple (default), ElevenLabs, Gemini | `elevenlabs`, `gemini` |
| Transcription | AssemblyAI | `assemblyai` |

The defaults need no keys at all. A phase can name a **backup provider** and an
`if both fail` choice (`stop` or `fallBack`); `stop` must never silently fall
back. `Pipeline.routedComplete` tries every configured step on *any* failure.
That is simpler than the PC router on purpose, because `AVGError` does not
carry the PC's finer taxonomy.

### Error handling, two layers

1. **`HTTP.retrying`** (per request). `classify()` turns a status and body into
   an `ErrorKind`; `POLICY` says whether to retry:

   | Kind | Retry | Attempts | Backoff base |
   |---|---|---|---|
   | `rateLimit` | yes | 5 | 2 s, doubling |
   | `network`, `timeout` | yes | 4 | 4 s, doubling |
   | `auth`, `quota`, `billing`, `modelMissing`, `contentFilter`, `bug`, `dependency` | no | 1 | none |

2. **`Recovery.plan`** (per phase). When retries are exhausted the run
   **pauses and asks** via `RecoverySheet`, using `withCheckedContinuation`. The
   nine safeguards: busy/network/timeout, rate limit, quota/billing, bad key,
   model not found or too long, content filter, device offline (auto-resumes
   when `Connectivity` reports online, waits up to 600 s), low storage (under
   300 MB), and repeated failures from one provider (three or more suggests a
   permanent switch).

---

## 5. Settings and channels

There is no schema catalogue. Settings are two `Codable` structs in
`Core/Models.swift`, each field added by hand:

- **`AppSettings`** is global: provider choice per kind (`text` = Apple,
  `image` = Pollinations, `voice` = Apple by default), model ids per provider,
  `fps` (30), `shortEdge` (1080), `kenBurns` (true), `kbIntensity` (1.0),
  `imageStyle`, captions (`captionsEnabled`, `captionMaxWords` 4,
  `captionPosition` center, `captionFontSize` 72, `captionAnimationStyle`
  fade), `dynamicPausesEnabled` and `pauseMs` (700), `filterStyle` and
  `filterIntensity`, the writer/image/voice fallback models and
  `autoUseFallback`, plus `glassOpacity`, `themePreference`, `appFont` and
  `onboarded`.
- **`Channel`** is per channel: identity (`name`, `niche`, `tone`,
  `language`), `targetSeconds` (60), `aspectRatio`, `topicResearchEnabled`,
  brand colours (`accentColorHex`, `accent1ColorHex`, `accent2ColorHex`) and
  `fontFamily`, recycling (`recycleEnabled`, `recycleRatio` 70), cards
  (`takeoverCardsEnabled`, `explainerCardsEnabled`), image backup
  (`imageProviderBackup`, `imageOnFailure`), voice cleanup
  (`voiceTrimAggressiveness` medium, `voiceMaxRemovableSilencePct` 40), and a
  provider / backup / on-failure triple for each text step (`brief`,
  `scriptDraft`, `scriptSegmentation`, `director2`).

There is **no inheritance layer** and no `invalidates`. Create, Studio and
Settings read the global values directly; the Channel editor holds the
per-channel ones. The Settings screen deliberately has no Global/Channel
toggle, because not every field has both a global default and a channel
override.

Adding a setting is: a field with a default in the struct, a line in its
tolerant `init(from:)` (`c.value("key", key)`), a control in `SettingsView` or
`ChannelEditor`, and the code that reads it.

---

## 6. The five recipes

**Add a setting.** Add a field with a default to `AppSettings` (or `Channel`)
and a `c.value(...)` line in its `init(from:)`. If you skip the decode line, an
existing user's saved settings will reset to the default. Add the control in
`SettingsView` or `ChannelEditor`. Read it where it is used, through `store`.

**Add a phase.** Add a case to `PhaseID` in `Models.swift`, **in order**. Add
its body to `Pipeline` and a `case` in `Pipeline.execute`. Add its label in
`StudioView`'s phase rows and `WelcomeView`'s walkthrough. Decide what
`Recovery.aiKind(for:)` returns for it. Add a `cost…` recorder if it spends
money.

**Add a provider.** A struct satisfying the protocol in its kind's file
(`TextProvider`, `ImageProvider`, `VoiceProvider`). Add its case to the kind
enum in `Models.swift` (`TextProviderKind` etc.) and to that phase's resolver
in `Pipeline` (`textProvider(for:)`, `imageProvider(for:)`, `voiceProvider()`).
Add its `APIKey` case in `Keychain.swift` if it needs one. Wrap every network
call in `HTTP.retrying(provider:)`. The `provider:` argument is **required**,
because the provider-specific patterns in `ErrorPolicy.swift` key off it. Add
its patterns there, add a catalogue source in `ModelCatalogSources.swift` if
it advertises models, and add a cost rate in `Pipeline`.

**Add a screen.** A view in `UI/`, a case in `AppTab` (`Store.swift`), and a
tab in `RootView` (`App.swift`). Its data comes from `store`; a view does not
call a provider. Use `Theme` for every colour and radius. One filled
(`.glassProminent`) action per screen.

**Add a failure safeguard.** Extend `Recovery.plan(for:…)` with the new
`RecoveryAction` set, handle it in `Recovery.apply`, and add the row to the
list in the header comment of `Recovery.swift`. Never add a retry outside
`HTTP.retrying`.

---

## 7. Design system

`Theme.swift` is the authority. Skin v2 is a port of an HTML mock, and each
value is a direct copy of the mock's custom property.

| Token | Value |
|---|---|
| Accent (terracotta) | `#e08a4e` |
| Settled / finished (sage) | `#aebf92` in dark, `#6f8757` in light |
| Canvas | `(20,17,13)` dark, `(244,238,229)` light |
| Display font | Caprasimo, bundled, with a system-serif fallback baked into `Theme.disp` |
| Radii | card 24, inner 18, sheet 34, player 26, selection bar 26, sheet handle 38×5 |
| Glass | hand-rolled `GlassSurface`: fill + blur + top-left sheen + 0.5 pt stroke + top highlight + drop shadow. Deliberately **not** `.glassEffect`, which exposes no fill or blur knobs. Buttons still use the system `.glassProminent` style |
| Background | two animated orbs, one still frame under Reduce Motion |

The tab bar is the **native iOS 26 Liquid Glass tab bar**. A hand-rolled
`FloatingTabBar` was tried and removed (`680410e`); `SKIN_V2_PLAN.md` still
describes it. Skin v2 is closed out (all nine phases). The only open item is a
Dynamic Type check in the simulator.

---

## 8. Data on disk

| Item | Location | Owner |
|---|---|---|
| `project.json` | `Documents/Projects/<projectID>/` | `Store.save(_:)` is the only writer |
| Images, audio, renders | `…/<projectID>/images/`, `audio/`, `render/` | `Pipeline` |
| `take.log` | `…/<projectID>/` | `Store.appendToProjectLog`; a failed write never breaks a run |
| `.avg-meta.json` | `Documents/` | settings, channels, active channel, reusable-image descriptors. An unreadable file is kept as `meta.unreadable-<timestamp>.json` |
| Reusable images | `Documents/ReusableImages/<UUID>.jpg` | `Store.addReusableImage` |
| Model catalogues | `Application Support/ModelCatalogs/<key>.json` | `ModelCatalog`. Replaced only by a **non-empty** successful fetch |
| API keys | Keychain, service `com.avgstudio.keys`, `AfterFirstUnlock` | `Keychain.swift` only |
| Pending render id | `UserDefaults` `AVGPendingRenderProjectID` | `BackgroundRender` |
| CapCut bundle | `temporaryDirectory/CapCut Export - <title> - <take>/` | `CapCutExporter`, handed to the share sheet |

The Documents folder is exposed to the Files app (`UIFileSharingEnabled`), so
`Projects/` is visible to the user.

A project's costs are kept in `Project.costs`, capped at 200 entries. The
rates are flat guesses, not metering: Gemini text about $0.25 per million
characters, Groq about $0.15, Gemini image $0.02, OpenAI image $0.04,
ElevenLabs $0.00003 per character, AssemblyAI $0.01 per minute.

---

## 9. Where state lives

The `Store`, one instance for the process lifetime:

| Property | Meaning |
|---|---|
| `projects`, `channels`, `activeChannelID` | The loaded library and channels |
| `settings`, `reusableImages` | Global settings and the recycle pool |
| `jobs` | `[projectID: JobState]`: phase, progress, message for each running run |
| `tasks` (private) | The running `Task` per project. Several projects can run at once |
| `log` | Recent `LogEntry`s (the Live List and Health's Recent Activity) |
| `tab`, `currentProjectID` | Navigation. Studio always shows `currentProject` |
| `recoveryPlan`, `pendingReword` | A paused run waiting on the user |
| `interruptedProjectID` | A run cut off by the app closing; shown as a "tap to resume" banner |

Views keep almost none. Anything a screen shows comes from `store`; local
`@State` is limited to form fields and sheet flags.

---

## 10. Build and run

- **Requires:** Xcode 26, iOS 26 deployment target, Swift language mode 5.0,
  bundle id `com.avgstudio.app`, iPhone only, portrait only.
- **CI:** push to `ios-native`. `.github/workflows/ios.yml` runs on
  `macos-26`, generates the project with XcodeGen, builds Release unsigned,
  and uploads `AVGStudio-unsigned.ipa` plus `build.log` as artifacts.
- **Sideload:** Sideloadly or AltStore. There is no signing in CI
  (`CODE_SIGNING_ALLOWED=NO`).
- **Local:** `brew install xcodegen && xcodegen generate && open AVGStudio.xcodeproj`
  from the repo root.
- **Tests:** there is **no iOS test target**. Verification is the CI build
  plus running the app. The PC test suite in the reference folder tests
  Python and does not cover this code.
- **Needs on device:** iOS 26 and Apple Intelligence for the default on-device
  writer. Without it, choose Gemini, Groq or Pollinations in Settings.

---

## 11. Repository layout

```
/                                  the iOS app
  AVGStudio/                       Swift sources, assets, fonts
  project.yml                      XcodeGen spec
  SKIN_V2_PLAN.md, UI_POLISH_PLAN.md
  .github/workflows/ios.yml        unsigned-IPA build
Pc Version Reference (Linux)/      the desktop app, read-only reference
  README.md, CLAUDE.md             its own guides (they describe the old layout)
```
