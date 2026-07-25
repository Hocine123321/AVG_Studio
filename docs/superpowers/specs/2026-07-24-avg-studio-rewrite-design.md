# AVG Studio — rewrite design

**Date:** 2026-07-24
**Status:** approved (program-level design)
**Supersedes:** the PowerShell 7 CLI at `D:\AI_Video_Gen` (AVG `B2.20.2`)

---

## 1. Why

The current app works but has hit four ceilings at once, all of which the user named as drivers:

1. **Console-only.** Numbered menus and `Read-Host` prompts for a tool that produces images and video.
2. **Setup friction.** Requires pwsh 7, Python, pip packages and FFmpeg on PATH before it will run.
3. **Stability and speed.** StrictMode crashes, fragile `PSObject` handling, hard-to-debug failures.
4. **Maintainability.** 20,045 lines of PowerShell with UI fused into logic, and zero tests.

### Measured starting point

| | |
|---|---|
| PowerShell | 20,045 lines across 6 files, ~335 functions |
| Python | 5,310 lines (`avg.py` 4,287, `overlay_design.py` 578, `server.py` 445) |
| Console calls (`Write-Host`/`Read-Host`) | 914, spread across **every** file including `ai.ps1` (211) and `pipeline.ps1` (109) |
| Registered safeguards | 36, with a real taxonomy |
| Error-handling functions | 11+ overlapping, across 3 modules |
| Tests | 0 |
| Live projects in `output/` | 62 |

The 20k PowerShell lines are the problem. The 5.3k Python lines are the asset — `avg.py` encodes invariants that were paid for in production failures (zoompan single-frame, QSV fallback, RAM gating at 8 / 8.5 / 12 GB, segmented low-RAM render, safe-zone analysis, Ken Burns easing, filter compositing, caption burn). **Rewriting the media layer is the single largest risk in any plan and delivers nothing the user can see.**

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | **Python, end to end** | Kills the two-language split; keeps the media layer nearly verbatim |
| Front-end | **Web UI (HTML/CSS/JS, React + Vite + Tailwind)** in a native shell | The look the user asked for by name; WebView2 is preinstalled on Win 11, so it is *lighter* than bundling Qt |
| Shell | `pywebview` → WebView2 | Real window, taskbar entry, native file dialogs |
| Bridge | FastAPI on `127.0.0.1`, random port + per-session token, WebSocket event stream | Pydantic models double as the API schema; engine testable headlessly |
| Packaging | Small `AVG.exe` (~20–30 MB) with embedded Python + shell + built web assets | First launch installs heavy deps into a private venv |
| Media isolation | **Child process** (`multiprocessing`), not a thread | A moviepy OOM must kill the render, not the app |
| Data | Clean v2 schema + one-way importer | Chosen over read-compatibility |
| Version control | New repo at `D:\AVG_Studio`; the PowerShell app stays untouched as read-only reference | |

### Rejected, and why

- **C# / .NET + WPF.** Strongest on single-exe and static typing, but requires re-implementing `avg.py` against the FFmpeg CLI — most of the project's budget spent re-earning behaviour that already works.
- **C# GUI + bundled Python engine.** Lowest render risk, but deliberately preserves the two-language split the rewrite exists to remove.
- **Qt Widgets / QML.** Qt Widgets is genuinely well-suited to this app's dense settings surface, and was the working assumption until the user asked for a web UI by name. QML adds a niche language for a smaller ecosystem.
- **Read-compatibility with the existing config and projects.** Rejected in favour of a clean schema plus an importer.

## 3. Architecture

```
avg/                  Python — the whole engine
  core/               domain models (Project, Phase, Brief, Script, Timeline) — pure data, no I/O
  config/             settings v2, channels, DPAPI secrets, catalogs
  media/              avg.py + overlay_design.py, moved near-verbatim, behind MediaEngine
  providers/          text router + image providers, retry policy, model caches
  pipeline/           the 8 phases as a state machine
  api/                FastAPI app — typed commands in, event stream out
  shell.py            pywebview window + composition root
web/                  React front-end, built to static assets
```

### The rule that replaces 914 console calls

**Nothing below `api/` may print or prompt.** Lower layers emit typed events (`PhaseStarted`, `Progress`, `NeedsDecision`, `PhaseFailed`) and log records; the UI decides how to render them. This is the constraint that makes the engine testable headlessly and prevents `providers/` from growing its own UI.

### Process and threading model

- Qt-free: the UI runs in WebView2, the engine in Python.
- The pipeline runs off the request thread; the API returns immediately and streams progress over the WebSocket.
- `MediaEngine` spawns a **child process** per render job. Crash and RAM isolation are preserved; what disappears is the hand-rolled JSON-lines protocol, the `avg.py <subcommand>` fallback path, the stdout-is-a-protocol hazard, and the manual orphan sweep — Python IPC carries typed objects.
- Cancellation is a cooperative token checked at phase boundaries and inside existing progress callbacks; FFmpeg subprocesses are killed directly, as today.
- The RAM gates (≤8 GB worker cap, ≤8.5 GB grain drop, <12 GB prebuild skip) become a `ResourceProfile` computed once and passed down — testable, and visible in the UI.

## 4. Packaging and first run

1. **`AVG.exe`** carries embedded Python, `pywebview`, FastAPI and the built web assets. The user never installs Python or edits PATH.
2. **First launch** shows a real setup screen — per-component rows with individual state and individual retry — installing numpy, Pillow, pydub, moviepy, edge-tts and assemblyai into a private venv, plus FFmpeg into the app folder.
3. **Later launches** verify in milliseconds. The same screen returns as Settings → Components, replacing Check Health.

Because WebView2 ships with Windows 11, the setup screen is the same web UI as the app — there is no chicken-and-egg problem of needing a UI before the UI toolkit is installed.

Trade-off accepted: this is not literally zero-setup — first run needs internet and a few minutes. The app cannot function offline anyway (every AI call), so nothing real is lost, and the download stays modest while dependencies stay updatable without re-shipping.

## 5. Data model

### Runtime data layout

Where the app keeps user data at run time. Separate from this repository, which holds only source.

```
%APPDATA%/AVG Studio/
  settings.json        app-wide only: paths, runtime, ui, resource profile, key refs
  secrets.dat          DPAPI, same mechanism as today
  channels/
    documentary.json   creative settings + models + voice + images + captions
    news-report.json
    zack-d-films.json
  projects/index.json  cached card data for the home grid — no folder walk for 62 rows
```

### The one structural change that matters

`config.json` today is 21 top-level keys: 15 groups (`images` alone has 30 keys) plus **6 loose creative scalars** — `content_shape`, `visual_tone`, `style_lock`, `auto_style_lock`, `brand_preset`, `visual_quality`. Channel profiles work by *copying* their bundle into the global config on activate and folding it back on save.

That copy mechanism produced three of the seven bugs fixed on 2026-07-24: `visual_tone` pinned as a catalog id that never expanded to prompt text, Quick Setup silently reverting on the next profile switch, and `[N] New profile` dropping the outgoing channel's edits.

**In v2 the channel is the settings scope.** Creative settings live on the channel document. There is no global copy, no apply step, no capture step, no absent-means-inherit rule, and no auto-save. The bug class ceases to exist, and the settings UI can be honest about which channel a change lands on.

### Project v2

Keeps today's good shape — `project.json` already has 9 sections, one per phase — and adds:

- Per-phase `status`, `started`, `finished`, `duration_ms`, `error`. The Studio phase rail shows per-phase durations; **that data is not recorded anywhere today.** Schema gap, not a rendering gap.
- `pipeline_state.json` folds in as a `state` section, written atomically via the temp-and-replace helper `avg.py` already has.
- `schema_version` on every document, with forward-only migrations — so nothing can resurrect the way deleted channel profiles did under `Merge-Defaults`.

### Importer

One-way, idempotent, dry-runnable.

- `config.json` → `settings.json` + one channel per existing profile, **plus a `Default` channel built from the 6 loose scalars**, so a user with profiles off does not lose their setup.
- 62 project folders → v2 documents **in place**. No media moves; the importer rewrites the JSON sidecar and nothing else, so a failed import costs nothing and the PowerShell app still opens the folder.
- Best-effort per project: an unreadable `project.json` is a row in the report with a reason, never a failed run. Inherits the legacy root-path fallbacks `Restore-ProjectDocument` handles.

## 6. Error handling

The 36-safeguard registry carries over intact, taxonomy and all (`RESOURCE`/`STATE`/`ACTION`/`DEPENDENCY`/`CONFIG` × `info`/`warn`/`critical` × `warn`/`block`/`recommend`/`log`). Each safeguard becomes a small predicate function, independently testable, and `block` gains a real dialog instead of a console prompt.

What does not carry over is the 11+ overlapping error functions. They collapse into:

**One taxonomy.** Every provider adapter maps its raw failure to one `kind`: `auth`, `quota`, `billing`, `rate_limit`, `model_missing`, `content_filter`, `network`, `timeout`, `resource`, `dependency`, `bug`. Nothing else in the codebase inspects an HTTP status or greps an error string.

**One retry policy, expressed as data.** `kind` → retryable, backoff, attempts, fallback action. `rate_limit` retries with backoff; `quota` and `billing` never retry and switch provider; `auth` stops and surfaces a fix-this-key action; `model_missing` drops to the backup model. Today this is re-implemented per provider, which is why the ElevenLabs backup-key behaviour took two releases to settle.

**Errors are events.** Each failure is a typed event carrying kind, provider, phase, human sentence and raw detail. The UI renders a red phase row plus the sentence, the raw detail in mono, and the actions the taxonomy declares valid — the buttons come from the taxonomy, not from hand-written UI at each call site.

A media child process dying arrives as a phase failure of kind `resource`, not an app crash.

The session error log survives with its current rule: a clean run leaves no file.

## 7. Testing

Zero tests exist today. Four layers, in descending value:

1. **Recorded provider fixtures.** Capture each provider's real responses once — success plus every error kind — and replay them. Asserts things like "a quota error switches provider" without being online or spending money. Highest value: provider failures are what break in production and cannot be reproduced on demand.
2. **Pure-function units.** Schema migrations, retry table, safeguard predicates, format/timeline math, scene splitting, caption timing. Covers the bug class behind all seven findings fixed on 2026-07-24.
3. **Pipeline state machine.** Resume from each of the 8 phases against a fixture project. The PowerShell app shipped a bug where the start phase defaulted to 1 and silently skipped Brief; that is a three-line test.
4. **Media golden tests.** Render a 3-scene fixture at low resolution and assert on structure — duration within tolerance, stream count, resolution, captions present — not pixels, which drift across FFmpeg builds. Two reference frames on a perceptual hash cover what fails silently: Ken Burns direction and safe-zone placement.

**Explicitly not tested:** whether the AI wrote a good script. That is what Run again is for — a human loop, not an assertion.

## 8. Interface design

Full design — screens, palette, type, shell anatomy: **[AVG Studio — interface design](https://claude.ai/code/artifact/bd86507a-729d-4f5f-933d-cdd18aee2614)** (published artifact; not yet mirrored into this repo).

Load-bearing decisions:

- **The transport bar.** Pinned to the bottom of every screen, borrowed from video editors. The pipeline takes ~20 minutes; a running job must be ambient, never something to go back and check on. Most important element in the app.
- **Graphite ground, one amber accent.** Dark-first because the user spends this app's time judging images and grades, and bright chrome around a 9:16 frame lies about exposure. Amber means exactly one thing — *happening right now* — and is never decorative. Light theme is a full citizen, not an inversion.
- **Segoe UI Variable + Cascadia Code.** The Windows system faces: it should read as a native app, and every timecode, duration, model id and log line lands in the mono that ships with Windows.
- **Studio is the hero screen.** The phase rail is the spine; clicking a phase shows what that phase produced. A running phase streams into the same view it is later inspected in, so there is no separate progress screen and no mode to leave.
- **`Preview a Phase` becomes `Run again`,** living on the phase it applies to. Still writes to `<project>\Previews\`.
- **Channel is a filter, not a folder** — the same topic gets cut for more than one channel.
- **Trending topics live inside the topic field**, not behind a separate mode.
- **Settings leads with search, categories second.** The 4,385-line settings menu existed because every option needed a numbered home. Per-phase models become a table.

## 9. Decomposition

Too large for one spec. Six sub-projects; each gets its own spec → plan → implementation cycle.

| # | Sub-project | Replaces |
|---|---|---|
| P0 | Foundation + packaging spike | — |
| P1 | Config, secrets, channels, importer | `Get-ConfigDefaults`, `Merge-Defaults`, DPAPI, `dynamic_options.txt`, channel profiles |
| P2 | Media adapter | `server.py` + `Invoke-RenderJob` + fallback + orphan cleanup |
| P3 | Provider layer | `ai.ps1` (4,872) + `visuals.ps1` (2,009) |
| P4 | Pipeline engine | `pipeline.ps1` (3,131) |
| P5 | Web UI | `settings_menu.ps1` (4,385) + `main.ps1` menus (2,489) |

### Build order: vertical slice first

**Slice 1 (the next spec):** one real video phase working end-to-end in the new app.

- Packaging spike — PyInstaller vs Nuitka against the real dependency set, measured on startup time and size
- Minimal settings v2 + the importer subset needed to read one project
- `MediaEngine` child process calling `avg.py`'s existing pure entry points with progress and cancel
- A minimal web UI: pick an imported project → Render → live progress → play the result

This touches P0, P1, P2 and a sliver of P5, and proves every risky assumption — bundling, child-process media, the bridge, WebView2, reading real data — before committing to rewriting 20k lines. Breadth follows: P3 → P4 → full P5.

## 10. Open questions and spikes

1. **DPAPI cross-language read.** PowerShell's `ConvertFrom-SecureString` writes a hex-encoded DPAPI blob of the UTF-16LE string; Python should read it via `CryptUnprotectData` through `ctypes`. **Verify in P0** — if it fails, the importer asks for keys once instead.
2. **Packaging.** PyInstaller (bundles CPython + bytecode, slower cold start, simpler with numpy/moviepy) vs Nuitka (compiles to C then binary, faster start, harder to read back, longer builds). Decide on measurement, not preference.
3. **Channel save visibility.** The UI surfaces channel auto-save as an explicit banner plus a `Save to <channel>` button, on the principle that visible beats magic. The alternative — keeping today's silent fold-in — is viable and changes the settings screen.
4. **Layout drift in the reference app.** `CLAUDE.md` documents `script/`, `audio/`, `render/`, `_tmp/` with three files at project root, but real folders have `images/` and a root `pipeline_state.json`. The importer follows disk; that doc line is stale.

## 11. Non-goals

- Cross-platform support. Windows only, deliberately.
- Migrating media files. Projects import in place.
- Preserving the PowerShell app. It stays runnable as reference, and is not maintained after cutover.
- Multi-user, cloud sync, or a hosted service.
- Automated evaluation of AI output quality.
