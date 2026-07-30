# AVG Studio — coding guide

A map, not a manual. `CLAUDE.md` says what the rules are and what must never
be done; this says **where everything is and how a change flows through it**,
so a change can be made without reading the tree first.

Anything explained here in detail is explained here *only* — every module's
own docstring carries the reasoning, and this file points at it.

---

## 1. The system, in one paragraph

One process. `avg/shell.py` opens a pywebview window and starts uvicorn on a
daemon thread, bound to `127.0.0.1` with a per-session token; the React UI in
`web/` is served as static files from that same server and authenticates with
that token. A request enters at `avg/api/`, which is the only layer allowed
to face outward — it prints, raises HTTP errors, and decides what reaches the
user. Below it, `avg/pipeline/` sequences the eight phases, `avg/providers/`
talks to AI backends, `avg/config/` owns settings/channels/secrets,
`avg/core/` holds the shared vocabulary (models, events, errors, catalogues),
and `avg/media/` renders in a **spawned child process** against a vendored
copy of the legacy engine. Nothing below `avg/api/` prints or prompts; long
work is a generator of typed events that the API layer drains on a background
thread and broadcasts over `/ws/events`.

Layer rule: a module may import from its own layer and any layer above it in
this list, never below.

```
web/            React 19, no router (a screen union in App.tsx)
avg/shell.py    window + server, one process
avg/api/        FastAPI, the only outward-facing layer
avg/pipeline/   phase sequencing
avg/providers/  text / image / voice backends, transport, error taxonomy
avg/media/      render child process + vendored legacy engine
avg/config/     settings, channels, DPAPI secrets, legacy import
avg/core/       models, events, errors, catalogues, takes, invalidation
```

---

## 2. Every module, one line each

### `avg/core/` — shared vocabulary. Knows about no other layer.

| Module | Responsibility | Must not know about |
|---|---|---|
| `models.py` | `ProjectDoc`, `Take`, `PhaseRecord`, `RenderRequest` — the documents everything else passes around | HTTP, providers |
| `events.py` | Every typed event a long job can emit | who is listening |
| `errors.py` | `AvgError` and `ErrorKind` | HTTP status codes |
| `settings_schema.py` | `SETTINGS` — every user-configurable setting, the one catalogue | the UI, the router |
| `invalidation.py` | Which phases a changed setting invalidates | takes |
| `takes.py` | Creating, cloning, deleting takes; `current_take_id` rules | HTTP |
| `costs.py` | The maintained per-phase cost table | live usage metering |
| `health.py` | Component and stage checks for App Health | the UI's layout |
| `error_store.py` | Recording/persisting app errors, redacted | HTTP, printing |
| `model_cache.py` | Storage for the working-models cache | the network |

### `avg/config/` — what the user configured.

| Module | Responsibility |
|---|---|
| `settings.py` | `SettingsDoc` (`%APPDATA%/AVG Studio/settings.json`), atomic save, legacy render settings |
| `channels.py` | Channel documents and the active-channel pointer |
| `secrets.py` | The **only** module that encrypts/decrypts/persists keys (DPAPI). `get_key()` is its public API |
| `legacy_import.py` | Importing projects from the old app's output folder |

### `avg/providers/` — talking to the outside.

| Module | Responsibility |
|---|---|
| `transport.py` | One HTTP call. Modes: `live` (default), `record`, `replay` |
| `errors.py` | `classify()` — what a status/body means. `POLICY` — what to do about it |
| `models.py` | Fetch-then-cache a provider's **advertised** model list |
| `probe.py` | Probe models with a minimal call; fills the working-models cache |
| `text/router.py` | Per-phase routing and one-shot fallback; `_BACKENDS` maps names to classes |
| `text/community.py`, `text/gemini.py` | The text backends |
| `images/community.py`, `images/premium.py` | The image backends |
| `voice/providers.py` | Edge TTS and ElevenLabs (`edge_tts` imported lazily — it cost 2s of startup) |

### `avg/pipeline/` — sequencing.

| Module | Responsibility |
|---|---|
| `registry.py` | `REGISTRY` — the phase order. The single source of truth |
| `phase.py` | The `Phase` protocol and `PhaseContext` |
| `runner.py` | Runs phases in order as a generator of events; owns `project.phases` bookkeeping |
| `create.py` | `new_project`, `save_project` (atomic; the only writer of `project.json`) |
| `prompts.py`, `personality.py` | Prompt construction and the bundled prompt assets |
| `phases/writing.py` | Brief, Script | 
| `phases/director.py` | Director I and II |
| `phases/visuals.py`, `voice.py`, `captions.py`, `render.py` | The remaining phases |

### `avg/media/` and `avg/export/`.

| Module | Responsibility |
|---|---|
| `engine.py` | `MediaEngine.render()` — spawns the child, yields events, joins it |
| `child.py` | What runs in the child process |
| `environment.py` | ffmpeg discovery, the disposable render workspace |
| `thumbnails.py` | Thumbnail generation and cache |
| `vendor/` | **Protected.** Byte-for-byte copies of the old engine. Never edit |
| `export/capcut.py` | CapCut draft export |

### `avg/api/`.

| Module | Responsibility |
|---|---|
| `app.py` | `create_app()` — the only construction entry point. Projects, render, pipeline, media, WebSocket, the error middleware |
| `routes_settings.py` | Settings, channels, keys, health, errors, costs, model cache |
| `routes_takes.py` | Takes and export |
| `deps.py` | `require_token` — the `X-AVG-Token` check |

### `web/src/`.

| File | Responsibility |
|---|---|
| `App.tsx` | The screen union. There is no router |
| `api.ts` | Every HTTP call and the token. Nothing else fetches |
| `useEvents.ts` | The `/ws/events` socket and derived phase state |
| `phases.ts` | **A hand-kept mirror of `REGISTRY`.** Change phase order and both move together |
| `tokens.css` | The design system, and the authority on the accent |
| `Home / Studio / Settings / Channels / AppHealth / Create / Welcome` | The screens |
| `fields/` | One renderer per `SettingSpec.type`, chosen by lookup; `source` outranks `type` |
| `Select.tsx` | The app's own select; its list is portalled to `document.body` |
| `ErrorLog.tsx`, `ModelCacheCard.tsx`, `ModelPicker.tsx` | Diagnostics and model selection |

---

## 3. Routes

All require `X-AVG-Token` (the WebSocket takes it as a query parameter).

| Method | Path | Returns | Owner |
|---|---|---|---|
| GET | `/api/projects` | library + loading + storage bytes | `app.py` |
| POST | `/api/projects` | 201, starts the full pipeline | `app.py` → `pipeline.runner` |
| POST | `/api/projects/archive` | bulk archive/unarchive, all-or-none | `app.py` |
| POST | `/api/projects/{id}/render` | 202, starts a render | `app.py` → `media.engine` |
| POST | `/api/projects/{id}/cancel` | cancels the running job | `app.py` |
| GET | `/api/projects/{id}/thumbnail` \| `/video` \| `/images` \| `/images/{file}` | media bytes (video honours Range) | `media.thumbnails` |
| WS | `/ws/events` | every event from the running job | `app.py` `_ConnectionManager` |
| GET/POST | `/api/settings` | the app document (output root, onboarding) | `config.settings` |
| GET | `/api/settings/scan` | a suggested library folder | `config.legacy_import` |
| GET | `/api/settings/schema` | `SETTINGS`, which the UI renders itself from | `core.settings_schema` |
| GET/POST | `/api/settings/values` | resolved app+channel values; refuses secrets | `config` |
| GET/POST/PATCH/DELETE | `/api/channels[/{id}]`, `/{id}/activate` | channel documents | `config.channels` |
| GET/POST/DELETE | `/api/keys[/{provider}]` | **booleans only**, never a key | `config.secrets` |
| GET | `/api/health`, `/api/health/report` | components, three stages | `core.health` |
| POST | `/api/health/install` | installs one of this app's own dependencies; refuses in a frozen build | `core.health` |
| GET/POST/DELETE | `/api/errors` | the error log; POST is how the UI reports itself | `core.error_store` |
| GET | `/api/costs` | the maintained cost table | `core.costs` |
| GET | `/api/models/cache` | proven models per provider | `core.model_cache` |
| POST | `/api/models/cache/build` | Test Providers: 202, or 409 while one runs. Ticked text models get a completion; image/voice providers only a catalogue fetch | `providers.probe` |
| GET/POST/PATCH/DELETE | `/api/projects/{id}/takes/...` | takes, preview, select, export | `core.takes`, `export.capcut` |

---

## 4. The five recipes

**Add a setting.** One entry in `avg/core/settings_schema.py` `SETTINGS`, with
`invalidates` (required — omitting it fails at import). Nothing else. The UI
renders it from `GET /api/settings/schema`. If a phase must read it, read it
from the channel's settings dict in that phase.

**Add a phase.** Write `avg/pipeline/phases/<name>.py` implementing the
`Phase` protocol (`id`, `key`, `name`, `inputs_ready`, `run`). Add it to
`REGISTRY` in `avg/pipeline/registry.py` **in order**. Mirror the key into
`web/src/phases.ts`. Add its cost row in `avg/core/costs.py`. Point the
settings that should re-run it at its key via `invalidates`.

**Add a provider.** A class satisfying the backend protocol in
`avg/providers/<kind>/base.py`, living in that kind's `community.py` or
`premium.py` — **check for colliding module-level private names first**
(`_BASE_URL`, `_extract_text`, …); siblings define the same ones and Python
rebinds silently. Register it in `text/router.py`'s `_BACKENDS` (or the image
/ voice equivalent). Add its key id to the schema's secret placeholders.
Record fixtures with `AVG_HTTP_RECORD=1`; never let a test call live.

**Add a screen.** A component in `web/src/`, a member of the screen union in
`App.tsx`, and an item in `Rail.tsx`. Its data comes from a function in
`api.ts` — no component fetches directly. One filled terracotta action per
screen.

**Add an event.** A model in `avg/core/events.py`. Emit it from the generator
that owns the work (`ctx.emit` in a phase, `yield` in the engine). The API's
drain loop broadcasts it without changes; handle it in `useEvents.ts`.

---

## 5. Data documents

| Document | Location | Shape / owner |
|---|---|---|
| `project.json` | `<project folder>/` | `ProjectDoc`. Written **only** by `pipeline.create.save_project`, atomically |
| Take artifacts | `<project folder>/takes/<id>/` | Per-take phase outputs; a clone never inherits `master_output` |
| `settings.json` | `%APPDATA%/AVG Studio/` | `SettingsDoc` — app-scope values plus the active-channel pointer |
| Channel documents | `%APPDATA%/AVG Studio/channels/` | Channel-scope settings. No global copy, no inheritance |
| `secrets.dat` | `%APPDATA%/AVG Studio/` | DPAPI-encrypted keys. Only `config/secrets.py` touches it |
| `errors.json` | `%APPDATA%/AVG Studio/` | Capped, newest-first error log, redacted before write |
| `working_models.json` | `%APPDATA%/AVG Studio/` | `{"updated", "models": {provider: {model: bool}}}` — proven models only |
| `model_cache.json` | `%APPDATA%/AVG Studio/` | A provider's *advertised* list. Different file, different meaning |
| Thumbnails | `%LOCALAPPDATA%/AVG Studio/thumbs/` | Derived, safe to delete |

Every one of these paths is overridable through `create_app(...)`, which is
how tests avoid the real user profile.

---

## 6. Where state lives

`app.state`, set in `create_app` and living for the process:

| Key | Lifetime / meaning |
|---|---|
| `token` | The session token every request must present |
| `output_root` | The library folder; changes when the user moves it |
| `projects`, `projects_loading`, `library_loaded`, `storage_bytes` | The scanned library. `library_loaded` is the event `GET /api/projects` waits on |
| `engine` | The one `MediaEngine`; it tracks one child process |
| `manager` | Connected `/ws/events` sockets |
| `render_thread`, `rendering_project_id`, `pipeline_cancel_token` | **One job at a time.** A second start gets 409 |
| `probe_thread` | The model-cache sweep's own single slot — deliberately not the render slot |
| `error_store`, `model_cache` | The two on-disk logs, as objects |
| `settings_path`, `channels_dir`, `secrets_path`, `thumb_cache_dir` | Path overrides; `None` means "each module resolves its own default" |

The frontend keeps almost none: `useEvents.ts` holds the live job's state,
`activity.ts` holds in-flight requests for the status pill, and everything
else is fetched per screen.
