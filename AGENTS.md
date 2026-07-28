# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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

**One job at a time.** `app.state.render_thread` is a single slot shared by full-pipeline runs and `/render` jobs; a second start gets a 409.

### Key single-sources-of-truth

| Concern | Owner |
|---|---|
| Phase order | `avg/pipeline/registry.py` (`REGISTRY`) — canonical on the backend; a second Python copy existed once and was removed. **`web/src/phases.ts` still restates all eight keys** and its own docstring admits it mirrors `REGISTRY`. That is live duplication, not a resolved problem: change phase order and both must move together until the frontend reads it from an endpoint |
| Every user-configurable setting | `avg/core/settings_schema.py` (`SETTINGS`) — the UI renders itself from `GET /api/settings/schema`; adding a setting is one entry, no route or UI change |
| What an HTTP status/body means | `avg/providers/errors.py` `classify()` — nowhere else greps an error string or branches on a status |
| What to do about a failure | `avg/providers/errors.py` `POLICY` — a data table keyed by `ErrorKind`, never re-derived |
| Writing `project.json` | `avg/pipeline/create.py` `save_project`, atomically |
| The session token / `X-AVG-Token` | `web/src/api.ts` on the frontend; `avg/api/deps.py` on the backend |

### Config scopes (no merge layer)

- `scope="app"` settings live in `SettingsDoc.values` (`%APPDATA%/AVG Studio/settings.json`).
- `scope="channel"` settings live in the **active channel's** document, full stop. No global copy, no apply/capture step, no absent-means-inherit. Three bugs in the legacy app came from the copy mechanism this deletes.
- `type="secret"` values never travel through the **settings surface**: `/api/settings/values` refuses them, `SettingsDoc.values` never holds one, and `GET /api/keys` reports only *whether* a provider has a key. `avg/config/secrets.py` is the only module that encrypts, decrypts or persists them (DPAPI, `CryptProtectData`).
  This is a boundary on the *config and API surface*, not a ban on reading a key. `get_key()` is `secrets.py`'s public API and exists precisely so a provider can sign its request — every backend under `avg/providers/` calls it, by design. What must never happen is a key reaching a log line, an exception message, a `repr()`, a settings response, or a recorded HTTP fixture (`transport.py` scrubs those before writing).

### Takes

A project owns zero or more `Take`s (`<project.folder>/takes/<id>/`). Changing a setting and re-running clones the current take, keeping only the phases the change does not invalidate. `SettingSpec.invalidates` (a phase key) drives `avg/core/invalidation.py`; `avg/core/takes.py` acts on the result. `current_take_id` is canonical (Home thumbnail, `master_output`, export default) and only advances when a take *finishes* — merely viewing a take never moves it. A cloned take never inherits `master_output`.

## Rules that are not negotiable

Each of these has caused a real defect at least once.

- **No live API calls in tests, ever.** `avg/providers/transport.py` replays recorded fixtures from `tests/fixtures/http/` and raises `UnmatchedRequestError` on anything unmatched — there is structurally no path from replay mode to the network. Recording requires `AVG_HTTP_RECORD=1`. ElevenLabs / image providers cost real money; one paid recording was already lost.
- **`avg/media/vendor/` is protected.** Byte-for-byte copies of the old render engine, pinned `-text` in `.gitattributes` because `core.autocrlf` silently corrupted the stored blob once while the working file looked fine. Never edit. The only change ever permitted was a 9-line progress hook.
- **One terracotta (`#e08a4e`) *primary action* per screen** — `.btn--primary`, the filled one. Terracotta itself is the brand accent and appears freely as identity (links, `:focus-visible` rings, `::selection`, chips); what is scarce is the filled call-to-action. Sage `#aebf92` means settled/finished, and the pulsing halo — never colour alone — marks the live phase. `web/src/tokens.css`'s header is the authority here and says so explicitly ("Amber-means-one-thing is relaxed here on purpose… the accent carries identity as well as 'happening right now'"); the older, stricter "terracotta means happening right now and nothing else" wording in `docs/superpowers/specs/project handoff.md` predates the Organic Night theme and is superseded. Four tasks have had to fix over-use of the filled action.
- **Never add a Google Fonts `@import`.** Caprasimo and Figtree are self-hosted in `web/public/fonts` so the app works offline and does not ping Google on launch.
- **Every `SettingSpec` must declare `invalidates`** — a required field with no default, so omitting it fails at import. A test enforces completeness.
- Tests must not write to the real `%APPDATA%/AVG Studio` — pass the `create_app` path overrides.

## How work is run

Every slice: brainstorm → spec → plan → subagent-driven execution, using the `superpowers` skills. One fresh subagent per task, checkpoint reviews, one commit per task. Speed over ceremony: no per-step RED/GREEN, no frontend unit tests, Python tests kept.

- `docs/superpowers/specs/` and `docs/superpowers/plans/` — the design and plan per slice. **`docs/superpowers/specs/2026-07-28-slice-6-handoff.md` is the current cold-start brief**; the Slice 1–5 plans are historical and name pre-consolidation module paths.
- `.superpowers/sdd/progress.md` — the running ledger of every decision and defect found. Sessions have died mid-slice six times; **that file plus `git log` is what makes resuming possible. Trust it over recollection.**
- `docs/decisions/` — packaging and other standing decisions.

Slices 1–5 are done (installer, config, providers, 8-phase pipeline, takes + CapCut export), 537 tests passing. Slice 6 is the six features from the user-supplied mockup; two of them (cost tracking, the `partial` phase state) still need design decisions, and the restyle is blocked on one unanswered question (shadows or no shadows — the current app deliberately has none).

## Account state (affects what you can actually exercise)

Only **Groq** currently generates text — Gemini is at zero free-tier quota, Pollinations at zero pollen budget, HuggingFace returns `model_not_supported`. For images, **Pollinations and Stable Horde work and are free**; OpenAI and Gemini do not. Voice: Edge TTS is free and always works.
