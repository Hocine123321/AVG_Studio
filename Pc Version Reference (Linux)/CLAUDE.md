# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AVG Studio — a Windows desktop app that turns a topic into a short-form (9:16) or long-form (16:9) video: AI writes a brief and script, generates one image per scene, synthesises narration, plans motion and captions, and renders an mp4.

It replaces a PowerShell 7 CLI (~20,000 lines) that still lives at `D:\AI_Video_Gen`. **That folder is read-only reference** — ten real user projects live there. Module docstrings throughout this repo cite the legacy file and function each piece replaces, and the bug it exists to prevent. Read the docstring before changing a module; it usually explains why the obvious approach was rejected.

One process: Python engine + FastAPI on `127.0.0.1` with a per-session token + React 19 UI in WebView2 via `pywebview`. Rendering runs in a spawned child process against a vendored copy of the old engine.

## Commands

**`.venv-build\Scripts\python.exe` is the interpreter for everything.** System `python` lacks the dependencies; `.venv` exists but `.venv-build` is the one that carries pyinstaller and the media deps.

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_runner.py::test_name -q
```

```bash
.venv-build/Scripts/python.exe -m mypy
```

Dev UI against real projects (kill anything on 8756 first) — serves `web/dist`, so run `npm run build` in `web/` first:

```bash
.venv-build/Scripts/python.exe tools/_dev_serve.py
```

Then open `http://127.0.0.1:8756/?token=devtoken`.

Frontend (`cd web`): `npm run dev`, `npm run build` (`tsc -b && vite build`), `npm run lint` (oxlint). No frontend unit tests — the UI is verified by looking at it.

Full app + installer from current source (~4 min): run `Build Installer.bat`, which wraps `tools/make_release.ps1`. Produces `AVG APP/` (runnable) and `installer/AVG-Setup.exe`. `-SkipInstaller` skips the slow Inno Setup compress. `tools/build.py pyinstaller|installer|all` is the layer underneath; it requires `web/dist` and `vendor/ffmpeg/ffmpeg.exe` (`tools/fetch_ffmpeg.py`) and fails loudly if either is missing.

## Architecture

Strict layering, enforced by convention and tests:

```
avg/shell.py          pywebview window + uvicorn on a daemon thread, one process
avg/api/              FastAPI — THE ONLY layer allowed to face outward
avg/pipeline/         phase sequencing (registry, runner, phases/)
avg/providers/        text/image/voice backends, transport, error taxonomy
avg/media/            render child process + vendored legacy engine
avg/config/           settings, channels, DPAPI secrets, legacy import
avg/core/             models, events, errors, settings catalogue, takes, invalidation
web/                  React 19 + Vite + TypeScript, no router (screen union in App.tsx)
```

Providers and phases are grouped, not one file per implementation — a file
consolidation landed 2026-07-28. Backends live in `providers/text/community.py`
(Groq, Hugging Face, Pollinations), `providers/images/community.py` (the free
three) and `providers/images/premium.py` (Gemini, OpenAI), `providers/voice/
providers.py`; phases in `pipeline/phases/writing.py` (brief, script) and
`pipeline/phases/director.py` (I and II). **Every document written before
that date names the old per-implementation paths** — see the translation
table in `docs/superpowers/specs/2026-07-28-slice-6-handoff.md`. Before
merging any two modules again, read the "colliding module-level globals"
section of `docs/superpowers/plans/2026-07-28-file-consolidation.md`: sibling
modules define the same private names, Python rebinds silently, and no test
catches it.

**Nothing below `avg/api/` may print or prompt.** Lower layers emit typed Pydantic events (`avg/core/events.py`); the API layer decides what reaches the user, as an HTTP response or a `/ws/events` WebSocket message. This replaced 914 `Write-Host` calls.

**Generators of typed events** are the app's one concurrency pattern. `MediaEngine.render()` and `pipeline.runner.run()` are both generators; `create_app`'s background-thread workers drain them and broadcast each event. Wiring a new long job in means writing another generator, not a new mechanism. A drain loop is never broken out of early — that is what guarantees the child process gets joined.

**`create_app(token, output_root, ...)` is the only construction entry point.** No module-level globals; every test builds a fully isolated instance, and the optional `thumb_cache_dir` / `settings_path` / `channels_dir` / `secrets_path` overrides exist so tests never touch the real `%APPDATA%` / `%LOCALAPPDATA%`.

**Several jobs at once, but one render.** `app.state.jobs` is a `JobRegistry` — the single slot `app.state.render_thread` used to be is gone. `max_parallel_jobs` (default 2) caps how many pipelines run concurrently; a job over the cap is **queued, never refused**, and starts when a slot frees. A cap of `1` therefore means one video at a time in the order asked for, not a 409. The 409 that remains is a different guard entirely: starting the **same** project twice, which would have two runners writing one `project.json`, and that refuses at any cap.

**Renders still serialise.** `app.state.engine` is one `MediaEngine` tracking one child process, so however many pipelines are in flight, the render phase takes turns behind a lease. Two renders at once is not a goal and must not become possible. And a drain loop is still never broken out of early — that is what guarantees the child process gets joined.

### Key single-sources-of-truth

| Concern | Owner |
|---|---|
| Phase order | `avg/pipeline/registry.py` (`REGISTRY`) — canonical on the backend; a second Python copy existed once and was removed. **`web/src/phases.ts` still restates all eight keys** and its own docstring admits it mirrors `REGISTRY`. That is live duplication, not a resolved problem: change phase order and both must move together until the frontend reads it from an endpoint |
| Every user-configurable setting | `avg/core/settings_schema.py` (`SETTINGS`) — the UI renders itself from `GET /api/settings/schema`; adding a setting is one entry, no route or UI change |
| What an HTTP status/body means | `avg/providers/errors.py` `classify()` — nowhere else greps an error string or branches on a status |
| What to do about a failure | `avg/providers/errors.py` `POLICY` — a data table keyed by `ErrorKind`, never re-derived |
| Writing `project.json` | `avg/pipeline/create.py` `save_project`, atomically |
| The session token / `X-AVG-Token` | `web/src/api.ts` on the frontend; `avg/api/deps.py` on the backend |
| Which files a phase writes | `avg/core/takes.py` `PHASE_ARTIFACTS` — what a clone copies. `avg/core/artifacts.py` adds display metadata over it and must never restate a path; a test asserts they cannot drift, because an artifact the UI shows but a clone does not copy is a file that vanishes in the next take |
| Which models/voices a picker may offer | `avg/core/model_prefs.py` (`model_prefs.json`) — the user's ticks, favourites and notes. Kept apart from `working_models.json`, which is machine-owned and replaced wholesale on every sweep |

### Config scopes (no merge layer)

- `scope="app"` settings live in `SettingsDoc.values` (`%APPDATA%/AVG Studio/settings.json`).
- `scope="channel"` settings resolve through one function,
  `avg.core.settings_schema.resolve_channel_settings` (Slice 19 §2.3):
  the active channel's own document wins if it has the key, else the
  app-wide `channel_defaults.json` wins if it has the key, else the
  `SettingSpec`'s own default. Absence is the only inheritance signal —
  presence is checked with `in`, not truthiness, so an explicitly-stored
  empty string is returned as-is, never treated as a gap. Every reader,
  pipeline phase or API route, goes through this one resolver; a test
  (`tests/core/test_settings_schema.py`'s drift test) fails if a reader
  bypasses it. Porting a channel's values into the defaults is still one
  explicit, user-pressed snapshot (`POST /api/settings/port-to-global`),
  never a live copy. A channel can also edit the defaults document
  directly via the Settings screen's Global/Channel toggle
  (`target: "global"` on `POST /api/settings/values`) — that path needs
  no active channel, since there is nothing above the defaults document
  to inherit from.
- `type="secret"` values never travel through the **settings surface**: `/api/settings/values` refuses them, `SettingsDoc.values` never holds one, and `GET /api/keys` reports only *whether* a provider has a key. `avg/config/secrets.py` is the only module that encrypts, decrypts or persists them (DPAPI, `CryptProtectData`).
  This is a boundary on the *config and API surface*, not a ban on reading a key. `get_key()` is `secrets.py`'s public API and exists precisely so a provider can sign its request — every backend under `avg/providers/` calls it, by design. What must never happen is a key reaching a log line, an exception message, a `repr()`, a settings response, or a recorded HTTP fixture (`transport.py` scrubs those before writing).

### Takes

A project owns one or more `Take`s. **Take 1 exists from `new_project`** (Slice 8) and its `phases` dict is the SAME OBJECT as `ProjectDoc.phases` — the runner mutates one set of records, not a live copy and a displayed copy. That aliasing has to be re-applied after any load, because a JSON round trip breaks it (`avg.core.takes.alias_root_take_phases`); pydantic copies a dict passed to a constructor, so passing one dict to both models is not enough.

`Take.storage` says where a take's files are: `"root"` (take 1 — every phase writes in place at the project folder) or `"own"` (`<project.folder>/takes/<id>/`, what a clone and a migrated legacy project get). **Read a take's files through `take_root`, never `take_folder`** — the latter only ever names the `takes/<id>/` path. Never infer storage from whether that folder exists; a take whose folder is merely absent is not a root take, and inferring it once made such a take adopt the project's records and lose its own.

Changing a setting and re-running clones the current take, keeping only the phases the change does not invalidate. `SettingSpec.invalidates` (a phase key) drives `avg/core/invalidation.py`; `avg/core/takes.py` acts on the result. `current_take_id` is canonical (Home thumbnail, `master_output`, export default) and only advances when a take *finishes* — merely viewing a take never moves it. A cloned take never inherits `master_output`.

## Rules that are not negotiable

Each of these has caused a real defect at least once.

- **No live API calls in tests, ever.** `avg/providers/transport.py` replays recorded fixtures from `tests/fixtures/http/` and raises `UnmatchedRequestError` on anything unmatched — there is structurally no path from replay mode to the network. Recording requires `AVG_HTTP_RECORD=1`. ElevenLabs / image providers cost real money; one paid recording was already lost.
- **`avg/media/vendor/` is protected.** Byte-for-byte copies of the old render engine, pinned `-text` in `.gitattributes` because `core.autocrlf` silently corrupted the stored blob once while the working file looked fine. Never edit. The only change ever permitted was a 9-line progress hook.
- **One terracotta (`#e08a4e`) *primary action* per screen** — `.btn--primary`, the filled one. Terracotta itself is the brand accent and appears freely as identity (links, `:focus-visible` rings, `::selection`, chips); what is scarce is the filled call-to-action. Sage `#aebf92` means settled/finished, and the pulsing halo — never colour alone — marks the live phase. `web/src/tokens.css`'s header is the authority here and says so explicitly ("Amber-means-one-thing is relaxed here on purpose… the accent carries identity as well as 'happening right now'"); the older, stricter "terracotta means happening right now and nothing else" wording in `docs/superpowers/specs/project handoff.md` predates the Organic Night theme and is superseded. Four tasks have had to fix over-use of the filled action.
- **Never use a native form control.** No `<select>`, no `<input type="checkbox">`, no `<input type="radio">`, no native `<button>` styling left at browser defaults. Windows draws all of them itself — OS grey, square corners, OS font — and they are the only things capable of ignoring the theme entirely. Use what already exists: `web/src/Select.tsx` for any choice (it is portalled, so no ancestor can clip it, and it keeps keyboard, type-ahead and ARIA), `.switch` for any boolean (see `BoolField`), `.select-dot` for multi-select on a card. A checkbox that must exist for state or keyboard focus is `position: absolute; opacity: 0` behind a styled element, never visible — `.select-dot input` is the pattern. This has been fixed on three separate controls across two screens; grep for `<select` and `type="checkbox"` in `web/src/` before shipping any screen.
- **Reuse the design language's own values.** Radii, spacing, colour: take them from `web/src/tokens.css` or from the nearest existing control doing the same job, never from a fresh guess. Two controls performing the same gesture with different corner radii is the same defect as an unthemed control, just quieter.
- **Look at the screen once per slice, at the end — not per task.** A UI task is done when it builds, lints, and its DOM/computed-style checks pass; screenshots are the *slice's* final verification, run once against the finished set of screens. Per-task screenshotting was tried across Slice 10's twelve tasks and was pure cost: the Browser pane often cannot composite at all (one agent burned ~70 tool calls discovering that), and re-shooting the same screen after every task found nothing that the end-of-slice pass did not.
  When you do run that final pass: `preview_start` with the dev-server URL opens the Browser pane, and `computer{action:"screenshot"}` only works once that pane is displayed — an attempt without it fails with "the page is not compositing frames". **If the pane will not composite, say so and defer the visual pass rather than faking it**; record it as pending in `.superpowers/sdd/progress.md`. Computed styles and geometry are a *substitute for iteration, never for the final look*: they confirmed a native `<select>` was "themed" because the wrapper was, and they cannot see a placeholder repeating 345 times down a page.
- **Never add a Google Fonts `@import`.** Caprasimo and Figtree are self-hosted in `web/public/fonts` so the app works offline and does not ping Google on launch.
- **Every `SettingSpec` must declare `invalidates`** — a required field with no default, so omitting it fails at import. A test enforces completeness.
- Tests must not write to the real `%APPDATA%/AVG Studio` — pass the `create_app` path overrides.

## How work is run

Every slice: brainstorm → spec → plan → subagent-driven execution, using the `superpowers` skills. One fresh subagent per task, checkpoint reviews, one commit per task. Speed over ceremony: no per-step RED/GREEN, no frontend unit tests, Python tests kept.

- `docs/superpowers/specs/` and `docs/superpowers/plans/` — the design and plan per slice. **`docs/superpowers/specs/2026-07-28-slice-6-handoff.md` is the current cold-start brief**; the Slice 1–5 plans are historical and name pre-consolidation module paths.
- `.superpowers/sdd/progress.md` — the running ledger of every decision and defect found. Sessions have died mid-slice six times; **that file plus `git log` is what makes resuming possible. Trust it over recollection.**
- `docs/decisions/` — packaging and other standing decisions.

Slices 1–10 are done, **671 tests passing**: installer, config, providers, the 8-phase pipeline, takes + CapCut export, the mockup's six features, repair/diagnostics/model cache, and pipeline truth + the per-phase artifact panel + the Live List. Still open and cosmetic: toasts and the device-frame player (Slice 6C leftovers). Deferred with its own plan pending: the codebase-simplification refactor, whose input is `docs/APP_CODING_GUIDE.md`.

## Account state (affects what you can actually exercise)

Only **Groq** currently generates text — a Slice 7 probe found 5 of its 10 models working. Gemini has exactly one working text model (32 listed); HuggingFace returns `model_not_supported` on all 20; Pollinations has no pollen budget.



Voice: **Edge TTS is free, always works, and advertises 322 voices**; ElevenLabs is keyed with 23. Listing voices costs nothing on either; synthesising costs characters on ElevenLabs, which is why the Live List lists image models and voices rather than probing them.
