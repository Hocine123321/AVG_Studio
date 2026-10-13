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
AI backends, and `Render/VideoRenderer.swift` composes frames on the GPU (Core Image on Metal, with a Core Graphics fallback)
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
AVGStudio/Core/       Store, Pipeline, Recovery, models, export, audio, image cache
AVGStudio/Render/     Core Image (GPU) or Core Graphics (CPU) frames + AVAssetWriter HEVC + AVFoundation mux
AVGStudio/Providers/  text / image / voice / transcription backends,
                      HTTP transport, error taxonomy, model catalogues
```

Nothing in `Providers/` imports from `UI/`. `UI/` never calls a provider
directly, except `ModelRow` and the catalogue refresh, which go through
`ModelCatalog`. Tests live beside the app in `AVGStudioTests/` and reach in
with `@testable import AVGStudio`.

---

## 2. Every file, one line each

### `AVGStudio/Core/`: state, sequencing, vocabulary.

| File | Responsibility | Must not know about |
|---|---|---|
| `Models.swift` | Every `Codable` document: `Project`, `Take`, `PhaseRecord`, `Channel`, `AppSettings`, plus the enums (`PhaseID`, `MotionType`, `GradeLook`, provider kinds). Decoding is **tolerant**: a missing key takes its default | SwiftUI, providers |
| `Store.swift` | The one state container: projects, channels, settings, jobs, log, the recovery plan. Owns persistence and starts/cancels runs. Settings writes are **debounced** (0.4 s); `flushMeta()` forces one when the app leaves the foreground | Views |
| `Pipeline.swift` | `Pipeline.execute(_ phase:)` and the five phase bodies. Provider resolution, routing, cost recording, prompt-marker parsing, and the per-step markers for the Studio screen. The render phase takes a turn on `RenderLease` | HTTP status codes |
| `Steps.swift` | `StepStatus`, `StepRecord`, `PhaseID.steps` (the finer steps inside each phase: 4 for Writing, 1 Visuals, 3 Voice, 3 Captions, 3 Render), and `Project.displayStatus` / `stepProgress`. Bookkeeping only: never changes what a phase does. `Project.stepRecords` is **optional** so older projects still decode | Pipeline logic |
| `Costs.swift` | `CostTable`: the one table of provider price **estimates**, used both by `Pipeline` as calls happen and by the Create screen's `estimate(...)` before a video runs. Say "about", never "cost" | A metered bill |
| `RenderLease.swift` | `actor RenderLease`: one render at a time across all running videos, first come first served; a cancelled waiter leaves the line | Phases other than render |
| `Recovery.swift` | Maps a failure to a `RecoveryPlan` (nine safeguards), the fallback-model logic, `Connectivity` (`NWPathMonitor`), and `RecoverySheet` | Which view shows it |
| `BackgroundRender.swift` | `BGProcessingTask` registration and the "interrupted render" flag. Uses the app's **one** `Store`, attached at launch with `attach(_:)` | Phase internals |
| `Keychain.swift` | **The only file that reads or writes API keys.** `APIKey`, `Keychain.get/set/has/remove/require`. Each provider has a MAIN and a BACKUP slot (`KeySlot`): `get`/`require` return what to SEND (the backup while its window is open), `read`/`has` return one slot as stored, `allSecrets` is every secret for scrubbing logs. `set` returns `Bool` and updates in place | Providers' request shapes |
| `ImageCache.swift` | `ImageCache` (ImageIO downsample off the main actor into an `NSCache`) and `CachedThumb`, the view every thumbnail uses | Which screen shows the picture |
| `PhotoSaver.swift` | `PhotoSaver.saveVideo(at:)`: add-only Photos permission, waits for the save, throws a readable error | Share sheets |
| `SilenceTrim.swift` | Dead-air removal on PCM audio (20 ms windows), the port of `remove_silences_core` | Where the audio came from |
| `CapCutExport.swift` | Builds the CapCut import bundle in `temporaryDirectory` | Share sheets |
| `CostRates.swift` | The assistant's view of cost, built **on `CostTable`** (Costs.swift, the one rate table the pipeline and the Create screen share): `CostBreakdown` (priced per phase, so a redo from Voice is not quoted as a whole video) and `CostEstimate`. Adds no rates of its own | Which phase runs |
| `Agent.swift` | The in-app assistant's loop: bounded tool calling, approval continuation, spend limit, one auto-start per message, compaction, failover, self-test, "ready/stopped" announcements | SwiftUI, request shapes |
| `SegmentedGeometry.swift` | The glass segmented control's gesture decisions as plain tested maths: a tap picks nothing up (the thumb glides to the new segment), only a clear sideways move (> 6pt, more sideways than vertical) drags it, picking it up does not make it jump, a vertical swipe is a scroll and never a choice | Views |
| `KeyBackup.swift` | The backup API key's 30-minute window (a date per provider in UserDefaults, so a relaunch doesn't reset it). `activateIfPossible(after:)` opens it when a KEY-level failure (rejected, quota, billing, rate limit past all retries) hits a provider with a backup saved, after the fallback model and the other service were tried; a failing backup is never retried with itself | Providers |
| `RunOverrides.swift` | Per-VIDEO setting overrides. A fallback model or provider is a decision about one run: it lives here keyed by project, `Store.effectiveSettings(for:)` lays it on the person's settings, the pipeline reads ONLY that, and ending the run discards it. Nothing global changes: not the Settings screen, not another video, not the saved file | Views |
| `AgentCapability.swift` | The seven assistant capabilities (one switch each; to the model each is a set of tools; off = not offered) and which tools each owns | Views |
| `AgentSettingsCatalog.swift` | The hand-written list of 24 settings the assistant may read and, with approval, change. No `agent...` field and no key is reachable (a test throws values at every entry and checks) | Views |
| `AgentCapabilityTools.swift` | `estimate_cost`, `manage_project`, `change_setting`, `edit_channel`, `get_app_status`, `get_activity`, `get_settings`: terse specs, plain-text results, errors that name the valid values | The loop |
| `AgentMarkdown.swift` | Turns a model's block Markdown (`* item`, `# Title`) into text SwiftUI's inline parser can style | Views |
| `AgentErrorHelp.swift` | An assistant failure in plain words (`title`, `detail`) plus one-tap `AgentFix`es (fix my key, choose another model, switch service, try again). Pure; the raw provider message still goes to the Activity log | The view, navigation |
| `LibraryLayout.swift` | How the Library packs cards into two columns: a landscape project spans both, narrow ones pair up, a lone card is half width and a waiting card borrows the next narrow one past a landscape row. Pure and property-tested | Views |
| `AgentTools.swift` | `AgentHost`, the seven tools, the gated-action `AgentApproval`, project-ref resolution, preflight, permission filtering | The loop, providers |
| `AgentPolicy.swift` | `AgentPolicy` (needs a tap? over the spend limit?) and `AgentPrompt` (the system prompt). **Policy is decided here from `AppSettings`, never from model text** | Model output |
| `AgentSettings.swift` | The Assistant's enums and `AgentSettingsRules` (spend-limit and instruction clamps) | Views |
| `AgentChatStore.swift` | The optional saved chat: user and assistant text only, newest 40, key-shaped strings masked | Providers |

### `AVGStudio/Providers/`: talking to the outside.

| File | Responsibility |
|---|---|
| `HTTP.swift` | One request (`HTTP.send`), and `HTTP.retrying`, the policy-driven backoff loop. `RenderLog` is a `@TaskLocal` logger so two projects never share a log |
| `ErrorPolicy.swift` | `classify()` says what a status/body means. `POLICY` says what to do about it. Nothing else greps an error string |
| `TextProviders.swift` | `AppleText` (on-device), `GeminiText`, `ChatText.groq`, `ChatText.pollinations` |
| `ImageProviders.swift` | `PollinationsImages`, `StableHordeImages`, `GeminiImages`, `OpenAIImages`, and `validImage(_:)` |
| `VoiceProviders.swift` | `AppleVoice` (on-device), `ElevenLabsVoice`, `GeminiVoice` (adds its own WAV header) |
| `TranscriptionProvider.swift` | `AssemblyAI.words(fromAudio:key:)` for word-level caption timing |
| `AgentTypes.swift` | Provider-neutral vocabulary: `AgentToolSpec`, `AgentMessage`, `AgentProvider`, tolerant `AgentArgs`, and `AgentHistory` (trim and compact without orphaning a tool result) |
| `AgentGroq.swift` | Groq's OpenAI-style tool-calling codec, `GroqAgent`, and `AgentProviderFactory` |
| `AgentGemini.swift` | Gemini's `functionDeclarations` codec. Replays the model's turn **verbatim** (Gemini 3 thought signatures) |
| `ModelCatalog.swift` | `ModelCatalog`: a live-fetched, disk-cached list backing a picker. One class, nine instances |
| `ModelCatalogSources.swift` | One fetch closure per catalogue (Gemini, Groq, ElevenLabs, Stable Horde, Pollinations) |

### `AVGStudio/Render/`.

| File | Responsibility |
|---|---|
| `VideoRenderer.swift` | `VideoRenderer.render(_:progress:) -> RenderStats`. `plan(t:…)` decides **once** what each frame contains (`FramePlan`); `composeGPU` (Core Image on Metal) and `draw(into:…)` (Core Graphics) both draw from that plan. Images are graded and normalised once up front; captions and takeover cards are rasterised only when their look changes. The encoder pulls frames through `requestMediaDataWhenReady`. Ken Burns, transitions, six caption animations, film-grain/light-leak filters, HEVC encode, passthrough mux |
| `RenderSupport.swift` | `RenderStats` (frames, GPU vs CPU frames, prepare / frame / mux seconds), `AppActivity` (may the GPU be used right now?), `CancelFlag`, and `Duration.seconds` |

### `AVGStudio/UI/`: the screens.

| File | Responsibility |
|---|---|
| `App.swift` (root) | `AVGStudioApp` and `RootView`: the five-tab `TabView`, onboarding gate, recovery sheet, scene-phase hook |
| `Theme.swift` | The design system (Skin v2): palette, glass, typography, background, toasts, sheets, haptics. **The authority on every colour and radius.** Two glass recipes: `liquidGlass` is Apple's real Liquid Glass, for controls and floating bars (search, composer, chips, round buttons, the selection bar, the segmented filter's thumb); `glass`/`glassCard` is the hand-rolled, tunable one for large content panels |
| `HomeView.swift` | Library: native `.searchable` search, filter strip, project grid (rows from `LibraryLayout`; a card's thumbnail size comes only from a `Color.clear` aspect-ratio base, never from the image), multi-select archive, interrupted-render banner, "N running · N queued" summary |
| `StudioView.swift` | The current project: player, five phase rows (tap one to expand its steps), queue position, live list, takes, scenes, brief, export sheet |
| `CreateView.swift` | New-video form: topic, title, shape, channel, and an estimated-cost card from `CostTable` |
| `ChannelsView.swift` | Channel list and `ChannelEditor` (identity, videos, brand colours and font, recycling, cards, image backup, voice cleanup, per-phase models) |
| `SettingsView.swift` | The Settings **hub**: big rows that each show their current value, a search field and a Quick settings button docked at the bottom, and the `SettingsCatalogs` instance shared by every sub-screen |
| `SettingsScreens.swift` | One page per category: `WritingSettingsPage`, `ImagesSettingsPage`, `VoiceSettingsPage`, `CaptionsSettingsPage`, `VideoSettingsPage`, `AssistantSettingsPage`, `KeysSettingsPage`, `FallbacksSettingsPage`, `AppearanceSettingsPage`. The keys page holds only *which* keys exist, never a key's value |
| `SettingsKit.swift` | Routing (`SettingsCategory`, `SettingAnchor`, `SettingsRoute`), the **search index** (`SettingsIndex`), and the row components (`ToggleRow`, `ChoiceRow`, `SliderRow`, `StepperRow`, `SegmentRow`, `FieldRow`, `NavRow`, `SettingsGroup`, `SettingsPage`) |
| `SettingsCatalogs.swift` | The nine live `ModelCatalog`s, created once by the hub so every screen shows the same loaded lists |
| `APIKeySheet.swift` | `KeyStatusPill` and `APIKeySheet`: the one key-entry sheet, shared by Settings and Welcome. Never shows a saved key |
| `ErrorLogView.swift` | The Activity screen: All / Errors filter over `store.log` |
| `HealthView.swift` | App Health: required and optional checks, summary tiles, recent activity. Checks are computed once per appearance, not on every read |
| `WelcomeView.swift` | First-run five-step onboarding |
| `ModelRow.swift` | One 56 pt row over a `ModelCatalog`; tapping opens a bottom sheet with search and Refresh. Always editable: typing an id works with no list |
| `AgentView.swift` | The assistant's chat sheet (opened from a toolbar button on Library, Studio and Create) and `AgentToolbarButton`. Everything needing attention is one glass stack above the composer: a paused video's recovery choices, the approval, live progress for videos it started. Errors show in plain words with fix buttons. The one filled button in it is Approve |
| `ProjectLogView.swift` | The live log sheet |
| `ReusableImagesView.swift` | The recycle-pool manager (photo import, descriptions) |
| `ActivityShareSheet.swift` | `UIActivityViewController` wrapper |

### Build and resources.

| File | Responsibility |
|---|---|
| `project.yml` | XcodeGen spec. **`Info.plist` and `AVGStudio.xcodeproj` are generated from it and never committed** |
| `.github/workflows/ios.yml` | Runs the unit tests on a simulator, then builds an unsigned IPA, on every push that touches `AVGStudio/**`, `AVGStudioTests/**` or `project.yml` |
| `AVGStudioTests/` | The unit tests. See §10 |
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
| DPAPI `secrets.dat` | Keychain | Service `com.avgstudio.keys`, device-only (`AfterFirstUnlockThisDeviceOnly`), so keys are not in backups |
| Gemini key as header **and** `key=` query | header only | The query copy leaked into URLs, logs and `URLError`; the PC quirk was deliberately dropped |
| `GET /api/keys` returns booleans | `Keychain.has`, `savedKeys: Set<APIKey>` | Same rule: the UI learns whether a key exists, never its value |
| Edge TTS | `AVSpeechSynthesizer` | On-device, free, always available |
| Groq, Hugging Face, Pollinations text | Apple Intelligence, Gemini, Groq, Pollinations | Hugging Face has no iOS backend, and its error patterns were left out on purpose |
| `SETTINGS` catalogue (75 specs) | hand-built `AppSettings` and `Channel` | See §5 |
| `invalidates` + clone-a-take | *not ported* | Tapping a phase row asks "Redo from X?" and re-runs X and everything after it. Phase files are overwritten; each render adds a new `take_<id>.mp4`, so earlier videos are kept |
| Pillow takeover-card renderer | Core Graphics, `drawCard` (drawn to a bitmap, then composited on the GPU path) | Same rule: a card owns its scene's whole window |
| `film_grain.mov`, `light_leak.mov` | procedural 128×128 grain tile, light-leak gradient | No bundled video overlays |

---

## 4. The pipeline

Order is `PhaseID.allCases` in `Core/Models.swift`. It is the single source of
truth; `StudioView` and `WelcomeView` list the same five names in their own
copy, so change them together.

| # | `PhaseID` | What it does | Writes |
|---|---|---|---|
| 1 | `writing` | Optional topic research, ~120-word brief, narration (~150 wpm) with `[pause]` and `((question))` markers, then a JSON scene split. Scenes are numbered **after** blank entries are dropped, so indices never have holes. Falls back to `sentenceScenes` if the JSON fails | `project.json` fields |
| 2 | `visuals` | One image per scene, three at a time. A `[RECYCLE #k]` pick reuses a pool image instead of generating. On a **retry** inside the same run (`resumeExisting`), scenes whose image is already on disk are kept, so a failure at scene 7 of 8 does not pay for all eight again. A deliberate "Redo from Visuals" still makes fresh art | `images/scene_NN.jpg` |
| 3 | `voice` | Splits at `[pause]`, synthesises each segment, trims dead air per segment (the scan runs **off the main actor**, so the UI stays live), joins with silent gaps in an `AVMutableComposition` | `audio/segment_N.caf`, `clean_N.caf`, `narration.m4a` |
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
- **The Assistant** (Settings > Assistant) is more `AppSettings` fields, all
  prefixed `agent`: `agentProvider` (groq), `agentGroqModel`,
  `agentGeminiModel`, `agentFailover` (true), the seven **capability** switches
  `agentAllowCreate` / `agentAllowRedo` / `agentAllowCancel` /
  `agentAllowManageProjects` / `agentAllowChangeSettings` /
  `agentAllowEditChannels` / `agentAllowDiagnostics` (all true; an off
  capability's tools are not offered to the model at all, and anything that
  changes something still asks), `agentApproval` (`always`, or `paidOnly`, where only
  exactly-free work starts without a tap), `agentChatSpendCapUSD` (1.00,
  0.25 to 10), `agentReplyStyle` (brief), `agentAnnounceResults` (true),
  `agentKeepChat` (false) and `agentInstructions` (400 characters). The
  **Test assistant** and **Clear saved chat** rows are actions, not fields.
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
tolerant `init(from:)` (`c.value("key", key)`), a control on the right page in
`SettingsScreens.swift` (or `ChannelEditor`), a `SettingAnchor` and a
`SettingsIndex` entry so search finds it, and the code that reads it.

---

## 6. The six recipes

**Add a setting.** Add a field with a default to `AppSettings` (or `Channel`)
and a `c.value(...)` line in its `init(from:)`. If you skip the decode line, an
existing user's saved settings will reset to the default. Add the control on the matching page in
`SettingsScreens.swift` (or in `ChannelEditor`), give it a `SettingAnchor` and an
entry in `SettingsIndex` (a test fails if you forget), and read it where it is
used, through `store`.

**Add a phase.** Add a case to `PhaseID` in `Models.swift`, **in order**. Add
its body to `Pipeline` and a `case` in `Pipeline.execute`. Add its label in
`StudioView`'s phase rows and `WelcomeView`'s walkthrough. Decide what
`Recovery.aiKind(for:)` returns for it. Add a `cost…` recorder if it spends
money.

**Add a provider.** A struct satisfying the protocol in its kind's file
(`TextProvider`, `ImageProvider`, `VoiceProvider`). Add its case to the kind
enum in `Models.swift` (`TextProviderKind` etc.) and to that phase's resolver
in `Pipeline` (`textProvider(for:)`, `imageProvider(for:)`, `voiceProvider()`).
Add its `APIKey` case in `Keychain.swift` if it needs one, and send the key in a **header**, never a query string. Wrap every network
call in `HTTP.retrying(provider:)`. The `provider:` argument is **required**,
because the provider-specific patterns in `ErrorPolicy.swift` key off it. Add
its patterns there, add a catalogue source in `ModelCatalogSources.swift` if
it advertises models, and add a cost rate in `CostTable` (`Costs.swift`).

**Add a screen.** A view in `UI/`, a case in `AppTab` (`Store.swift`), and a
tab in `RootView` (`App.swift`). Its data comes from `store`; a view does not
call a provider. Use `Theme` for every colour and radius. One filled
(`.glassProminent`) action per screen.

**Add an agent tool.** Add its `AgentToolSpec` to `AgentTools.allSpecs` (one
short sentence of `summary`: it is sent on every request), a `case` in
`AgentTools.run`, and a result that is short plain text. It must belong to a
capability (`AgentCapability.tools`) or be in `AgentCapability.alwaysOn` - a
test fails otherwise - so there is always a switch that removes it. If it
spends money or changes anything, return an `AgentApproval` (`kind`: `.spend`,
`.change` or `.destructive`) and add its `AgentApprovedAction` case to
`perform`; never act from `run` itself; add it to `AgentTools.isGated`. Add a
`FakeAgentHost` test and keep
`AgentPolicyTests.testSystemPlusToolsStaysUnderTokenBudget` passing (every
extra tool costs tokens on every request).

**Let the assistant change a setting.** Add ONE entry to
`AgentSettingsCatalog.all` (key, title, group, kind, read, write). Never a key
or an `agent...` field: the catalogue test would fail.

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
| Background | two animated orbs; one still frame under Reduce Motion **or Low Power Mode** |
| Glass level | `Settings → glassOpacity`, delivered through `EnvironmentValues.glassLevel` (`.environment(\.glassLevel, …)` in `RootView`). There is no `Theme.glassLevel` static any more |
| Status colours | `Theme.ok` (sage), `Theme.bad` (`#c4544a`), `Theme.accent`. Screens do not use `.red` / `.green` / `.yellow` |

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
| `.avg-meta.json` | `Documents/` | settings, channels, active channel, reusable-image descriptors. Written ~0.4 s after the last edit (debounced) and immediately when the app leaves the foreground. An unreadable file is kept as `meta.unreadable-<timestamp>.json` |
| Reusable images | `Documents/ReusableImages/<UUID>.jpg` | `Store.addReusableImage` |
| Kept assistant chat | `Documents/.avg-agent-chat.json` | `AgentChatStore`. Only when **Keep chat** is on; user and assistant text, newest 40, key-shaped strings masked. Deleted by New chat, Clear saved chat and turning Keep chat off |
| Model catalogues | `Application Support/ModelCatalogs/<key>.json` | `ModelCatalog`. Replaced only by a **non-empty** successful fetch |
| API keys | Keychain, service `com.avgstudio.keys`, account `<provider>` (main) and `<provider>.backup`, `AfterFirstUnlockThisDeviceOnly` | `Keychain.swift` only |
| Backup-key windows | UserDefaults `avg.backupKeyUntil` (provider -> end date; not a secret) | `KeyBackup.swift` |
| Pending render id | `UserDefaults` `AVGPendingRenderProjectID` | `BackgroundRender` |
| CapCut bundle | `temporaryDirectory/CapCut Export - <title> - <take>/` | `CapCutExporter`, handed to the share sheet |
| Thumbnails | in memory only (`NSCache`, 300 items / 80 MB), keyed by path, size and modification date | `ImageCache`. Redoing Visuals overwrites files in place, and the date in the key stops a stale picture showing |
| Saved videos | the Photos library | `PhotoSaver`, add-only permission (`NSPhotoLibraryAddUsageDescription`) |

The Documents folder is exposed to the Files app (`UIFileSharingEnabled`), so
`Projects/` is visible to the user.

A project's costs are kept in `Project.costs`, capped at 200 entries. The
rates are flat guesses, not metering (the single table is `CostTable` in `Costs.swift`): Gemini text about $0.25 per million
characters, Groq about $0.15, Gemini image $0.02, OpenAI image $0.04,
ElevenLabs $0.00003 per character, AssemblyAI $0.01 per minute.

---

## 9. Where state lives

The `Store`, one instance for the process lifetime. `AVGStudioApp` builds it and hands the same instance to `RootView` and to `BackgroundRender.attach`:

| Property | Meaning |
|---|---|
| `projects`, `channels`, `activeChannelID` | The loaded library and channels |
| `settings`, `reusableImages` | Global settings and the recycle pool |
| `jobs` | `[projectID: JobState]`: phase, progress, message for each running run |
| `tasks` (private) | The running `Task` per project. Several projects can run at once |
| `log` | Recent `LogEntry`s (the Live List and Health's Recent Activity) |
| `tab`, `currentProjectID` | Navigation. Studio always shows `currentProject` |
| `settingsPath`, `openSettings(_:)` | What the Settings tab has pushed. Any error can land the person on the page that fixes it (`openSettings(.category(.keys, focus: .apiKeys))`); the Recovery prompt's "Fix my API key" and the assistant's error buttons both use it |
| `recoveryPlan`, `pendingReword` | A paused run waiting on the user |
| `interruptedProjectID` | A run cut off by the app closing; shown as a "tap to resume" banner |
| `agent` | The assistant (`let`, one per `Store`). Holds the transcript, the pending approval, the chat's approved spend, and `isOpen` for the sheet. It drives the store only through `AgentHost`, and never constructs a second `Store` |

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
- **Tests:** `AVGStudioTests/` is a `bundle.unit-test` target (57 tests, XCTest,
  `@testable import AVGStudio`). CI runs it on an iPhone simulator **before**
  the release build, and a failing test stops the IPA from being produced.
  Coverage is deliberately narrow, on the code where a mistake is silent:
  - `ModelsTests` (4): `AppSettings` decodes `{}` to defaults, ignores unknown
    keys, round-trips, and `PhaseID.allCases` is the documented order.
  - `ErrorPolicyTests` (5): `classify` / `classifyURLError` results, and that
    every `ErrorKind` has a coherent `POLICY` row.
  - `PipelineTextTests` (17): `[pause]` splitting, `spoken`, question markers,
    proportional timing, JSON extraction, scene parsing and the sentence
    fallback, and the recycle-pool tokens.
  - `RenderPlanTests` (8): `VideoRenderer.plan` (scene placement, crossfade,
    takeover card ownership, slide and fade entrances, a missing image) and
    the `RenderStats` summary text.
  - `SettingsIndexTests` (8): every `SettingAnchor` and category is
    searchable, titles are unique, ranking and multi-word queries.
  - `StepsAndCostTests` (15): every phase has unique steps, older projects show
    finished phases as done, `CostTable` estimates and labels, and
    `RenderLease` ordering and cancelled waiters (these three use short
    `Task.sleep` waits, so a rare timing flake is possible).

  Nothing tests providers, the pixels the renderer produces, the `Store` or any view; that is still
  the CI build plus running the app. From Xcode: `⌘U`.
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
