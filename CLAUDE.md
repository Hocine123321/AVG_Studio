# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AVG Studio for iPhone: a native SwiftUI app that turns a topic into a short-form (9:16), long-form (16:9) or square (1:1) video **entirely on the device**. AI writes a brief and script, generates one image per scene, synthesises narration, times captions, plans motion, and renders an mp4.

It is a port of the desktop app in `Pc Version Reference (Linux)/`. **That folder is read-only reference.** Do not edit it, do not run its tooling, and do not treat its docs as describing this app: its `CLAUDE.md` and `README.md` describe the Python/React/Windows app and were written for the old repo layout. Swift comments throughout this repo cite the Python file and function each piece replaces, and the bug it exists to prevent. Read the comment before changing a file; it usually explains why the obvious approach was rejected.

One process, one target, no server: SwiftUI + a single `@MainActor @Observable` `Store`. Frames are drawn with Core Graphics and encoded by the iPhone's hardware HEVC encoder, replacing the PC's ffmpeg child process. Deployment target is **iOS 26**; the default on-device writer needs Apple Intelligence.

`README.md` is the map: every file, the PC-to-iOS correspondence table, the recipes. Read it before touching the tree.

## Commands

**There is no local toolchain on this machine.** Swift, Xcode and XcodeGen are macOS-only, and the sandbox here is Linux. Do not claim a change "builds" or "passes" unless CI ran it. Say what you checked (reading, grep) and that the compile is unverified.

**CI is the only thing that compiles or tests anything.** There is a small XCTest target, `AVGStudioTests/` (26 tests: settings decoding, `ErrorPolicy`, the pure text and timing helpers in `Pipeline`). It exists to pin the code where a mistake is silent. Nothing covers providers, rendering, the `Store` or views. When you change a pure helper (`Pipeline.splitOnPause`, `parseScenes`, `allocateProportional`, `classify`, an `AppSettings` field), **add or update its test in the same commit**. Do not add tests that need the network, a real key or a device.

CI (`.github/workflows/ios.yml`) runs on push to `ios-native` when `AVGStudio/**`, `AVGStudioTests/**`, `project.yml` or the workflow itself changes. It runs on `macos-26`, generates the project with XcodeGen, **runs the unit tests on an iPhone simulator**, builds Release with signing off, and uploads `AVGStudio-unsigned.ipa` and the `*.log` files. A failing test stops the IPA from being built. **Read the `error:` / `XCTAssert` lines in the job output (or `test.log` / `build.log`) to diagnose a red run**; the workflow prints them on failure. Sideload the IPA with Sideloadly or AltStore.

Local, on a Mac only, from the repo root:

```bash
brew install xcodegen && xcodegen generate && open AVGStudio.xcodeproj
```

**`AVGStudio.xcodeproj` and `AVGStudio/Info.plist` are generated from `project.yml`. They are not committed and must not be.** Add a file under `AVGStudio/` and XcodeGen picks it up; change a plist key, a bundle setting, a background-task id or a font in `project.yml`, never in a generated file.

## Architecture

Strict layering, enforced by convention. A file may use its own layer and the layers below it, never above:

```
AVGStudio/UI/         SwiftUI screens; read and write only through Store
AVGStudio/Core/       Store, Pipeline, Recovery, models, export, audio
AVGStudio/Render/     Core Graphics frames + AVAssetWriter HEVC + AVFoundation mux
AVGStudio/Providers/  text / image / voice / transcription backends, HTTP, error taxonomy, catalogues
```

**The `Store` is the boundary.** It plays the role `avg/api/` plays on PC. Below it, nothing touches UI state: a phase reports progress through a closure and fails with a typed `AVGError`; the `Store` decides whether that becomes a log line, a banner or a `RecoverySheet`. There is no `print` or `NSLog` anywhere in the app. Keep it that way: use `store.record(...)` (or `RenderLog.emit`, inside providers) so the line lands in the project's log.

**`@MainActor` is the concurrency contract.** `Store`, `Pipeline`, `Recovery` and `CapCutExporter` are main-actor. Concurrent work (image batches in a `withThrowingTaskGroup`) **captures what it needs by value on the main actor first** and never reads or mutates `store` from a child task. Copy the pattern in `Pipeline.visuals()`, don't invent a new one.

**There is exactly one `Store`.** `AVGStudioApp` builds it and passes that instance to `RootView` and to `BackgroundRender.attach(_:)`. The background handler once built its own `Store()`, giving two copies of the library writing the same project files. Never construct a second one.

**Several projects may run at once.** `Store.tasks` holds one `Task` per project id and `start(_:from:)` refuses a second start for the same id. `RenderLog.record` is a `@TaskLocal` precisely so two runs never write into each other's log. Never replace it with a mutable global.

**A phase failure pauses and asks; it does not just stop.** `Store.executeWithRecovery` catches a failed phase, builds a `RecoveryPlan` (`Recovery.plan`), and suspends on `withCheckedContinuation` until the user picks an action on the `RecoverySheet`. Nine safeguards are encoded there (busy/network/timeout, rate limit, quota/billing, bad key, model missing or too long, content filter, offline, low storage under 300 MB, repeated provider failures). A new failure mode is a new branch in `Recovery.plan`, not a new mechanism.

### Key single-sources-of-truth

| Concern | Owner |
|---|---|
| Phase order | `PhaseID.allCases` in `Core/Models.swift`. **`StudioView` and `WelcomeView` restate the five names** in their own copies. Change phase order and all three move together |
| What an HTTP status/body means | `Providers/ErrorPolicy.swift` `classify()`. Nowhere else greps an error string or branches on a status code |
| What to do about a failure | `ErrorPolicy.swift` `POLICY`, a table keyed by `ErrorKind`. Never re-derived at a call site |
| Retrying a request | `HTTP.retrying(_:provider:)`. Nothing else sleeps and retries. It is the only place that constructs `AVGError.provider` |
| Reading or writing an API key | `Core/Keychain.swift`. Service `com.avgstudio.keys`, one account per `APIKey` case |
| Writing `project.json` | `Store.save(_:)` |
| Writing settings, channels, the pool manifest | `Store.saveMeta()` to `.avg-meta.json` |
| Which colours, radii, glass and fonts exist | `UI/Theme.swift`. Take values from there or from the nearest existing control doing the same job |
| Pause handling | `Pipeline.splitOnPause` / `stripPauseMarkers`. The marker never reaches a TTS engine |
| Ken Burns, ease, transition and grade maths | `Render/VideoRenderer.swift` (`ease`, `drawScene`, `gradeImage`). The grade values are copied verbatim from the PC's `_GRADE_PRESETS` |
| Model lists a picker may offer | `Providers/ModelCatalog.swift`, one class, nine instances, disk-cached |

### Settings (no schema catalogue, no inheritance)

Settings are two hand-written `Codable` structs in `Core/Models.swift`. `AppSettings` is global; `Channel` is per channel. There is no `SettingSpec`, no `invalidates` and no merge layer. Adding a setting is a field with a default **and a matching line in that struct's tolerant `init(from:)`** (`c.value("key", key)`). Skip the decode line and every existing user's saved value silently resets to the default: this has happened (`608f7e9`, "Fix settings reset on update").

Decoding is tolerant on purpose. A missing key takes its default; an unreadable `.avg-meta.json` is copied to `meta.unreadable-<timestamp>.json` rather than overwritten. A schema change must never cost a user their channels or their keys' companion settings.

`.avg-meta.json` holds settings, channels, the active channel and the reusable-image descriptors. **It never holds a key.**

### Takes

A `Take` here is a rendered file: `Take(id, label, created, file)`, saved as `render/take_<id>.mp4`, with `id = max + 1`. Unlike the PC there are no per-take artifact folders, no `settings_snapshot`, no clone and no `invalidates`. Re-running from a phase (`store.start(id, from: phase)`) overwrites the project's phase outputs in place and, at `render`, appends a **new** take, so earlier videos survive.

Consequence to remember: the project keeps **one** current set of scenes, captions and audio. Exporting an older take to CapCut pairs that take's video with the *current* scenes, captions and timing, and `how-to-import.txt` says so. Do not "fix" this by pretending otherwise.

Logs are one `take.log` per **project** (not per take); each run begins with a `▶ Started…` separator.

## Rules that are not negotiable

Each of these has caused a real defect, or is a documented invariant carried over from a PC defect.

- **API keys live only in the Keychain.** Never in `project.json`, `.avg-meta.json`, `UserDefaults`, a log line, an error message, a `CustomStringConvertible`, or a committed file. `Keychain.require` throws `AVGError.missingKey`; use it rather than reading an empty string. Keys are stored `AfterFirstUnlockThisDeviceOnly`, so they stay out of backups. `Keychain.set` returns whether the write landed and updates in place; never go back to delete-then-add, which destroyed the saved key when the add failed.
- **The UI learns only *whether* a key exists, never its value.** `SettingsView` keeps a `Set<APIKey>`, and `APIKeySheet` never pre-fills a saved key. To replace a key the user pastes a new one; to remove it they tap Remove. Do not read a key into `@State`.
- **Send API keys in a header, never in a URL.** Gemini used to get the key twice (`x-goog-api-key` and `?key=`), and the query copy ended up in system logs, `URLError` and proxies. It is header-only now; do not put a key in a query string, a URL, or any log or error text.
- **`[pause]` becomes real silence, never spoken text.** The narration is split at each marker *before* synthesis, and the marker text never reaches `AVSpeechSynthesizer`, ElevenLabs or Gemini. `dynamicPausesEnabled` may turn deliberate pauses off; the "never spoken" half is not optional. Dead air is trimmed **per segment before the gap is joined**, otherwise the deliberate gap is crunched with the rest.
- **Never discard audio a provider already charged for.** If a trim removes more than `voiceMaxRemovableSilencePct`, retry once one aggressiveness step lower; do not throw the synthesis away.
- **A takeover card owns its whole scene window.** No scene image, no Ken Burns, no crossfade and no captions underneath it; it cuts in and out instantly.
- **`ImageOnFailure.stop` never falls back.** A backup provider is used only when the channel explicitly chose `.fallBack`. Content-filter blocks and bugs never fall back regardless.
- **Never retry a non-retryable failure.** `auth`, `quota`, `billing`, `modelMissing`, `contentFilter`, `bug` and `dependency` are one attempt. Retrying the same billing error burns the user's patience and sometimes their money.
- **Every `HTTP.send` / `HTTP.retrying` call names its `provider:`.** It is required so the provider-specific patterns in `ErrorPolicy.swift` fire. Omitting it silently downgrades to generic patterns and loses, for example, Gemini's safety-block detection.
- **No AI call, no UI, no printing below `Store`.** See Architecture.
- **A render interrupted by the app closing is never silently resumed.** `BackgroundRender` cannot promise completion (`BGProcessingTask` runs later, on iOS's schedule, and might not run). The honest behaviour is `Store.interruptedProjectID` plus a visible "Resume" banner. Do not "improve" this into auto-resume: it would re-fire paid API calls without the user asking.
- **`BackgroundRender.register()` runs in `App.init()`**, before launch finishes. Moving it into a view or `.task` makes iOS reject the identifier.
- **A catalogue refresh replaces the cache only on a non-empty success.** A failed or empty fetch keeps the last good list.
- **`ModelPickerRow` always falls back to an editable text field** when no catalogue exists. A disabled picker on a fresh install leaves the user unable to set a model at all.
- **Do not pass foreign model ids to Pollinations.** `normalizeModel` maps `gpt-image*`, `dall-e*`, `imagen*`, `gemini*`, empty or slashed ids to `flux`.
- **AssemblyAI is never required.** No key, or a failing one, degrades to estimated caption timing. It must not fail the `captions` phase.
- **A log that cannot be written must never break a run.** `Store.appendToProjectLog` swallows its own write errors.
- **Reduce Motion and Low Power Mode are honoured.** The animated background falls back to one still frame (the two blurred orbs cost real battery and compete with the video encoder), the onboarding slide to a cross-fade, and the phase halo stops pulsing. New motion checks `accessibilityReduceMotion`.
- **One filled call to action per screen** (`.buttonStyle(.glassProminent)`). Terracotta `#e08a4e` is the brand accent and appears freely as identity (tint, selection, focus); what is scarce is the filled button.
- **Take colours, radii, spacing and fonts from `Theme.swift`.** Two controls doing the same gesture with different corner radii is a defect, just a quiet one. Thumbnail overlays (white or black at some opacity over an image) are the deliberate exception, since they sit on arbitrary photos.
- **Glass is `Theme`'s hand-rolled `GlassSurface`, not `.glassEffect`.** Apple's material exposes no fill or blur controls, and Skin v2 is a pixel-level port of a mock. The tab bar is the exception: it is the native iOS 26 tab bar. A hand-rolled `FloatingTabBar` was tried and removed (`680410e`); do not reintroduce it.
- **Fonts are bundled, not fetched.** Caprasimo ships in `AVGStudio/Resources/` (OFL) and is registered in `project.yml` (`UIAppFonts`) and again in `Theme.registerFonts()`. `Theme.disp` falls back to system serif. Never load a web font.
- **Sizes and durations in CapCut export use integer milliseconds**, never float seconds, so SRT timestamps do not drift.
- **A `beginBackgroundTask` expiration handler must end the task, synchronously.** `Store` uses `BackgroundTaskBox` for this; an empty handler makes iOS kill the app as a watchdog violation, and hopping through a `Task` ends it too late.
- **Do not do CPU-heavy work on the main actor.** `Pipeline` is `@MainActor`, so the silence scan runs in `Task.detached` via `Pipeline.trim`. Image decoding goes through `ImageCache` / `CachedThumb`, never `UIImage(contentsOfFile:)` inside a view `body` (every card re-decoded on every progress tick).
- **A retry must not repay for finished work.** `Pipeline(resumeExisting:)` keeps scene images already on disk when the recovery sheet retries a phase; only an explicit "Redo from…" regenerates. New paid steps should follow the same rule.
- **Settings writes are debounced.** `Store.settings` and `reusableImages` call `scheduleMetaSave()`; `flushMeta()` runs when the scene leaves `.active`. Do not call `saveMeta()` from a `didSet` or a slider handler.
- **Saving to Photos reports its outcome.** Use `PhotoSaver.saveVideo(at:)`, which asks for add-only access and throws a readable error. Never go back to fire-and-forget `UISaveVideoAtPathToSavedPhotosAlbum`, which showed "Saved" even when access was denied.
- **Glass level comes from the environment.** Read `@Environment(\.glassLevel)`; do not reintroduce a `Theme.glassLevel` static mutated during `body`.
- **Use `Theme.ok` / `Theme.bad` / `Theme.accent` for status colour,** not `.red`, `.green` or `.yellow`.
- **Never hand-edit a generated file, and never commit one.** See Commands.
- **`Pc Version Reference (Linux)/` is read-only**, and `avg/media/vendor/` inside it is protected byte-for-byte (`-text` in `.gitattributes`). A change that seems to need an edit there is a change to the Swift port instead.

## How work is run

Commits follow the branch's existing style: a short imperative subject, `iOS:` or `fix(ios):` prefix for a screen or bug, `docs:` for plan updates. One logical change per commit. Push to `ios-native`; CI builds every push that touches the app.

- `SKIN_V2_PLAN.md`: the design plan. **All nine phases are done.** The plan text still describes the hand-rolled `FloatingTabBar`; the code uses the native tab bar (see the rule above). One inherited item is open: a Dynamic Type check on the card grid and Studio text, which needs a simulator.
- `UI_POLISH_PLAN.md`: the earlier plan, superseded by Skin v2 except for that same Dynamic Type item.
- When a plan phase lands, tick it in the plan file in a separate `docs:` commit, as history shows.
- `git log` is the ledger. There is no `.superpowers/sdd/progress.md` for the iOS side; the one under `Pc Version Reference (Linux)/` records the PC app's slices, not this app's.

**Comments are load-bearing here.** The author records *why* next to each non-obvious choice, and cites the PC file being ported. Preserve that habit: when you diverge from the PC, say so in a comment and say why. Several existing "differences" are deliberate (no `director1`, per-project log, procedural film grain, one-image-per-scene); do not "restore parity" without asking.

## State of the port (what you can and cannot exercise)

Ported and working: five-phase pipeline, on-device unit tests for the pure logic, four text providers, four image providers, three voice providers, AssemblyAI timing, per-phase provider / backup / on-failure routing, model fallback for the writer, image and voice AIs, the nine-safeguard recovery flow, takes, CapCut bundle export, reusable-image pool, takeover cards (focused and explainer), Ken Burns and Director II motion, transitions, colour grades, six caption animations, film-grain and light-leak filters, silence trimming, on-device HEVC render, interrupted-render resume, first-run onboarding, App Health, error log.

Deliberately not ported: `director1` (iOS always makes one image per scene), Hugging Face, Edge TTS, the settings schema and `invalidates`, the take clone, the HTTP layer, the PC's `max_parallel_jobs` queue (a run here starts immediately, one per project), and chapter cards (the PC's takeover-card switch covers Focused, Explainer and Chapters; here only the first two exist).

Account state: unknown from this repo. Defaults (Apple writer, Pollinations images, Apple voice) need no keys at all, so a fresh install can render a video with none configured. Which paid providers have credit is the user's to say.

## Working from a sandbox

If you are running on Linux (as the setup that produced this file was), you can read, edit and push Swift, but you cannot compile it. Two habits follow:

1. State plainly that a change is uncompiled. A previous commit fixed a build error from a non-existent `Section(title, content, footer)` initialiser (`33ad6cc`); that class of mistake only shows up in CI.
2. Pushing anything under `.github/workflows/` needs a GitHub token with the `workflow` scope; without it the push is rejected.
