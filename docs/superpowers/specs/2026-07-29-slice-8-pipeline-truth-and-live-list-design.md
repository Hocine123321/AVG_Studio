# Slice 8 — Pipeline truth, honest progress, and the Live List

Design document. Written 2026-07-29, after Slice 7 landed (613 tests, commit
`39fd37b`).

**What this slice is for.** Four defects the user hit while making a real
video, then the two things that make the Studio screen worth opening: every
phase showing its actual output, and a curated Live List of the models and
voices that work.

The four fixes come first. The artifact panel depends on the phase-state fix
being right, and the Live List depends on the picker plumbing that Slice 7
left half-finished.

---

## Findings this design is built on

Verified against the running app and the code on 2026-07-29. A screenshot of
the Studio screen mid-render is the evidence for F1 and F2.

| # | Finding | Evidence |
|---|---|---|
| F1 | **A take's `phases` is a snapshot, and a first run never updates it.** `GET /api/projects/{id}/takes` migrates a project into Take 1 lazily (`_migrate_and_persist` → `migrate_to_takes`), copying `project.phases` at that moment. The runner then keeps mutating `project.phases`, which is a different dict. Studio reads `selectedTake.phases`, so on a first run it renders whatever was true when the screen was opened. | Screenshot: Script unfinished while Director I, Visuals, Voice, Director II and Captions are all done and Render is at 35% |
| F2 | **The status pill cannot see a job.** `activity.ts` registers one activity per HTTP call, for that call's lifetime. `POST /render` returns 202 immediately, so the activity ends before it can be read; the pill says "Ready!" through an entire render. The flicker is the same mechanism — several short activities, "oldest wins", labels swapping under each other. | Screenshot: pill "Ready!" while the Transport bar reads "Rendering scene 10/23 · 35%" |
| F3 | **`useEvents` keeps only the newest event.** Every branch of `reduce` returns `{...INITIAL_STATE, <one field>}`, so `phaseFinished` is erased by the next `phase_started`. `applyLiveState` folds that single event onto a cached project. Finished phases never accumulate client-side. | `web/src/useEvents.ts:110-158`, `web/src/App.tsx:47-77` |
| F4 | **A render is silent for as long as it takes to copy the project.** `start_render` copies `images`, `audio`, `render` and `script` into a temp workspace *before* the child process starts, and the first event any client sees is the encoder's first progress line. | `avg/api/app.py:727-736`, `avg/media/environment.py:194-218` |
| F5 | **Every render leaks a full copy of the project to `%TEMP%`.** `render_workspace(req.project_folder, keep=True)` — the workspace is deliberately never cleaned up. | `avg/api/app.py:735` |
| F6 | **Every phase writes real files that nothing reads back.** A finished project holds `script/brief.json`, `topic_research.txt`, `script_draft.txt`, `timeline.json`, `markers.json`, `pause_map.json`, `beat_map.json`, `video_map.json`, `render/direction_plan.json`, `render/card_plan.json`, `render/scene_timings.json`, `failed_images.json`, `images/`, `audio/`. The artifact panel shows "Done." for six of the eight phases. | Real project folder under `D:\AI_Video_Gen\output`; `web/src/Studio.tsx:308` |
| F7 | **`/images`, `/video` and `/thumbnail` are project-scoped, not take-scoped.** A selected take that is not the current take cannot show its own files; Studio says so in words instead. Flagged in Slice 5 Task 4, never closed. | `avg/api/app.py`, `Studio.tsx:217-245` |
| F8 | **Image backends have no `list_models`; voice backends do have `list_voices`.** `avg/providers/images/` exposes generation only. `VoiceBackend.list_voices` exists and is a plain GET on both providers. | `avg/providers/images/*.py`, `avg/providers/voice/base.py:53` |
| F9 | **The model picker only covers text.** Slice 7 added `SettingSpec.source = "model_cache"` on the eleven text-model settings. `image_model`, `voice_id` and `voice_model_id` are still free-text. | `avg/core/settings_schema.py`, `web/src/fields/index.tsx` |

---

## Decisions taken with the user

Recorded here because each one closes off an approach that would otherwise
look reasonable to a later reader.

1. **No generation-based probing for images or voice.** Probing an image
   model means paying for an image; probing an ElevenLabs voice costs
   characters. The Live List is therefore a *curated* list over advertised
   catalogues, not a *proven* one. Text keeps the Slice 7 probe, which is
   the one kind where a working/not-working verdict is cheap.
2. **The Live List is app-wide by default and per-channel on request.** A
   note about a model is about the model, not the channel — but a Shorts
   channel and a documentary channel genuinely want different shortlists,
   so a channel can opt into keeping its own.
3. **The pill shows that a job is running, not what it is doing.** The
   Transport bar owns percent and phase. The same number must not render in
   two places at two update rates.
4. **Render work in this slice is honest reporting plus removing the copy
   stall.** Encoder tuning is out — it changes render behaviour and would
   need output quality re-verified.
5. **One spec, fixes first.** Six pieces of work, one plan, executed top to
   bottom.

---

## Part 1 — The four fixes

### 1.1 Take 1 exists from creation, and the run writes into it

**The rule:** a project has a take from the moment it exists, and the phase
records the runner mutates are that take's records. `ProjectDoc.phases`
stays as the compatibility mirror for projects imported from the legacy app,
which have no takes at all until they are migrated.

`new_project` creates take 1 and sets `current_take_id`. `POST /api/projects`
runs the pipeline against that take's `phases` dict, the same way
`routes_takes` already runs a re-roll against a cloned take's dict (it passes
a shadow project whose `phases` points at the take's records — that mechanism
exists and is tested, and this reuses it rather than inventing a second one).

`migrate_to_takes` stays exactly as it is: it is the path for legacy imports,
and it must remain idempotent and lazy for them.

Known cost: tests written when a fresh project had no takes will need
updating, and each one has to be read rather than mechanically re-baselined —
a test that asserted "no takes yet" may have been asserting something else
worth keeping.

Rejected: re-syncing the current take's phases from `project.phases` on every
read. It keeps two dicts and adds a rule about which one wins, and the rule
would have to be re-derived at every call site that touches either.

Rejected: preferring `project.phases` in the frontend while a job runs. The
stale snapshot still gets persisted, so the bug survives a restart.

### 1.2 Live phase state accumulates on the client

`useEvents` gains a phase map: `phases: Record<projectId, Record<phaseKey,
LivePhase>>`, folded forward by `phase_started` / `phase_finished` /
`phase_failed` / `pipeline_cancelled`, cleared for a project when a run
starts. The existing single-event fields stay — screens use them for the
"what just happened" banner — but the rail reads the map.

This is belt-and-braces with 1.1 rather than a substitute for it: 1.1 makes
the server's truth right, this makes the screen right *between* refetches,
including across a socket reconnect.

### 1.3 The status bar gets priorities

`activity.ts` grows a priority on each activity:

```ts
type Priority = "high" | "medium" | "low";
```

- `high` — a running job. Registered from the WebSocket's job events, not
  from an HTTP call, because the HTTP call that starts a job is over in
  milliseconds. Ends on the job's terminal event.
- `medium` — a write (`POST`, `PATCH`, `DELETE`): "Saving", "Saving key".
- `low` — a read: "Loading", "Checking".

The pill renders **the highest-priority activity, oldest-first within that
priority**. A lower-priority activity can never replace a higher one, which
is what removes the flicker: today two concurrent reads swap the label as
each resolves.

During a job the pill reads `Working · 02:14` — no percent, no phase name.
Both belong to the Transport bar.

### 1.4 A render says what it is doing from the first second

Two new events in `avg/core/events.py`:

- `JobStarted(project_id, kind)` — emitted by the API worker the instant the
  thread starts, before any file is touched. `kind` is `"render"` or
  `"pipeline"`. This is what the pill's `high` activity begins on.
- `JobStep(project_id, step, detail)` — a coarse named step for work that
  happens before the encoder can report a percentage. The workspace
  preparation emits one.

The workspace itself stops being a full copy: `render_workspace` hardlinks
each input file where the filesystem supports it (same volume, NTFS) and
falls back to `shutil.copy2` where it does not. Hardlinks make the
"disposable copy" free while keeping the property that matters — the
workspace starts with no `master_output.mp4`, so a file appearing there is
unambiguously this render's output.

`keep=True` becomes `keep=False` (F5). The one reason it was `True` — being
able to inspect the workspace after a render — is served by a
`AVG_KEEP_RENDER_WORKSPACE` environment variable, so a debugging session can
opt in and normal use does not silently fill `%TEMP%`.

### 1.5 "Build model cache" → "Re-build model cache"

One label, conditional on `updated` being non-null. The timestamp already
renders underneath it.

---

## Part 2 — The per-phase artifact panel

### 2.1 One shell, eight bodies

Every phase renders the same header strip — status chip, duration, file
count, estimated cost — and below it the artifact for that phase:

| Phase | Body | Files read |
|---|---|---|
| Brief | The brief's fields as a definition list; research notes collapsed below | `script/brief.json`, `script/topic_research.txt` |
| Script | Draft text, then the segmented scenes with their `[pause]` markers marked inline | `script/script_draft.txt`, `script/timeline.json`, `script/markers.json` |
| Director I | One card per scene: the image prompt, in order | `script/video_map.json` |
| Visuals | The existing image grid, plus the failures it currently hides | `images/`, `failed_images.json` |
| Voice & audio | One audio player per scene with its duration | `audio/*.mp3` |
| Director II | Per-scene motion: direction, zoom, hold | `render/direction_plan.json` |
| Captions | Caption cards with in/out times | `render/card_plan.json` |
| Render | The existing player and summary | `master_output.mp4` |

A phase that has not run yet keeps today's "Not started yet." A phase whose
file is missing says so as a fact about the file, never as an error page.

### 2.2 Serving a take's files

One new take-scoped route family, replacing the project-scoped media routes
for this purpose and closing F7:

```
GET /api/projects/{id}/takes/{take_id}/artifacts
    -> {"files": {"brief": {"name": ..., "bytes": ...}, ...}}   what exists

GET /api/projects/{id}/takes/{take_id}/artifact/{key}
    -> the file, with Range support
```

`key` is an **artifact key from a whitelist**, never a path. The whitelist
lives in one module (`avg/core/artifacts.py`) mapping key → relative path →
media type, and it is the same table the panel's layout is driven from, so a
new phase artifact is one entry rather than a route change plus a UI change.

Path traversal is structurally impossible: a key that is not in the table is
a 404 before any filesystem access.

Images and audio are directories rather than single files, so two keys are
listing keys (`images`, `audio`) returning a name list, and the per-file
route takes the name as a second segment validated against that listing.

### 2.3 Cost per phase

The header strip's cost comes from `GET /api/costs`, already served, already
keyed on display leaves. No new source.

---

## Part 3 — The Live List

### 3.1 What it is

A screen of its own, opened from App Health. Three tabs — **Text**,
**Images**, **Voice** — each a table of entries the app knows about, where
the user decides which ones the settings pickers are allowed to offer.

Per entry:

| Column | Meaning |
|---|---|
| Provider · id | `groq/llama-3.3-70b-versatile`, `pollinations/flux`, `edge/en-US-AriaNeural` |
| Source | `probed` (a real call succeeded), `catalogue` (the provider advertises it), `manual` (the user typed it) |
| ✔ | Ticked entries are what the settings pickers offer |
| ★ | Favourites sort first, everywhere |
| Note | Free text. Shows as the field's help text at the moment the model is picked |

Voice rows also carry language and gender, with filters — Edge alone
advertises several hundred voices, and the shortlist is the point of the
feature rather than a limitation of it.

### 3.2 Where the entries come from

- **Text:** the Slice 7 probe cache (`working_models.json`). `probed`.
- **Images:** the provider's own catalogue endpoint where one exists
  (Pollinations, Stable Horde). `catalogue`. Providers with no endpoint —
  OpenAI, Gemini — get no invented list; the user adds what they use.
- **Voice:** `VoiceBackend.list_voices` on both providers, which is a plain
  GET and costs nothing. `catalogue`.
- **Manual:** any entry the user types, in any tab, marked `manual` so it can
  never be mistaken for something the app verified.

No hardcoded catalogue, in keeping with `avg/providers/models.py`'s standing
rule: the only fallback is the previous fetch, never a literal in source.

### 3.3 Storage

A new document, `%APPDATA%/AVG Studio/model_prefs.json`:

```json
{
  "schema_version": 1,
  "entries": {
    "text": {
      "groq/llama-3.3-70b-versatile": {"selected": true, "favourite": true, "note": "best for briefs"}
    },
    "image": {"pollinations/flux": {"selected": true, "favourite": false, "note": ""}},
    "voice": {"edge/en-US-AriaNeural": {"selected": true, "favourite": false, "note": "narration default"}}
  }
}
```

Deliberately **not** merged into `working_models.json`: that document is
replaced wholesale on every sweep, and a merge is precisely the mechanism
that kept a stale entry alive in the legacy app.

A new channel-scoped setting, `live_list_source` (`global` | `channel`,
default `global`, `invalidates=None`), lets a channel keep its own copy of
the same structure inside its channel document. Resolution is one function
with no merge layer, matching how channel settings already work: the channel
either uses the global document or its own, never a blend of both.

### 3.4 Wiring into settings

`SettingSpec.source` already outranks `type` in the field registry
(Slice 7). It gains two more values:

- `live_list:image` → `image_model`
- `live_list:voice` → `voice_id`, `voice_model_id`

The existing `model_cache` source becomes a Live List read too, so a ticked
text model is what the picker offers and an unticked one is not. An empty
Live List falls back to the same free-text input Slice 7 established — a
fresh install must never be locked out of a setting it could always edit.

### 3.5 Routes

```
GET    /api/live-list                     merged entries per kind, resolved for the active scope
POST   /api/live-list/refresh             re-fetch image and voice catalogues (free; no generation)
PATCH  /api/live-list/{kind}/{entry_id}   {selected?, favourite?, note?}
POST   /api/live-list/{kind}              add a manual entry
DELETE /api/live-list/{kind}/{entry_id}   remove a manual entry (never a catalogue one)
```

---

## Module layout

**New**

| Path | Responsibility |
|---|---|
| `avg/core/artifacts.py` | The phase → artifact-key → relative path table, and safe resolution against a take folder. No HTTP. |
| `avg/core/model_prefs.py` | Reading/writing `model_prefs.json` and resolving global-vs-channel scope. No network. |
| `avg/providers/catalogues.py` | Listing image models and voices from providers that advertise them. The only new module that touches the network. |
| `web/src/LiveList.tsx` | The screen: three tabs, filters, tick/favourite/note. |
| `web/src/artifacts/` | One small component per phase body, plus the shared header strip. |

**Modified**

| Path | Change |
|---|---|
| `avg/pipeline/create.py` | `new_project` creates take 1 |
| `avg/api/app.py` | Pipeline runs against the current take; `JobStarted`/`JobStep`; workspace no longer kept |
| `avg/api/routes_takes.py` | The artifact routes |
| `avg/api/routes_settings.py` | The Live List routes |
| `avg/media/environment.py` | Hardlink-then-copy workspace; `keep` from the environment |
| `avg/core/events.py` | `JobStarted`, `JobStep` |
| `avg/core/settings_schema.py` | `live_list_source`; `source` on the image and voice settings |
| `web/src/activity.ts` | Priorities; job activities from events |
| `web/src/StatusBar.tsx` | Highest priority wins; short job label |
| `web/src/useEvents.ts` | The accumulated phase map |
| `web/src/Studio.tsx` | Rail reads the map; artifact panel delegates per phase |
| `web/src/ModelPicker.tsx` | Reads the Live List, favourites first, note as help |
| `web/src/AppHealth.tsx` | "Re-build model cache"; entry point to the Live List |

---

## Testing

Python, in the existing style — no live calls, no writes to the real
`%APPDATA%`:

- A pipeline run advances the **current take's** records, and a second `GET
  /takes` shows the advance. This is the regression test for F1 and it must
  fail against today's code.
- `render_workspace` leaves nothing behind by default, and hardlinks when the
  filesystem allows it (assert inode/link count, skip where unsupported).
- `JobStarted` is the first event of both job kinds.
- The artifact table refuses a key outside the whitelist with a 404 and never
  touches the filesystem for one.
- Range requests on an audio artifact return 206 with the right slice.
- `model_prefs` round-trips; a channel set to `channel` scope does not read
  the global document; a catalogue refresh never drops a user's note.
- A `manual` entry cannot be created for a kind that does not exist, and a
  `catalogue` entry cannot be deleted.

No frontend unit tests, per the standing decision. The UI is verified by
looking at it, at 1280×820 and 900×700, in both themes.

---

## Out of scope

- Encoder tuning and render-speed work beyond removing the copy stall.
- Generation-based probing for images or voice (decision 1).
- Toasts and the device-frame player, still open from Slice 6C.
- The codebase-simplification refactor, which has its own plan pending.

---

## Acceptance

1. Create a video from scratch and watch the rail: every phase turns sage as
   it finishes, live, without a refresh.
2. Press render: the pill says a job is running within a second, the
   Transport bar carries the percent, and neither flickers while other
   requests come and go.
3. `%TEMP%` holds no `avg-render-*` folder after a render finishes.
4. Click each of the eight phases on a finished project and see its real
   output — text, prompts, images, audio, motion, cards, video.
5. Open the Live List, refresh the catalogues, tick four voices, favourite
   one, write a note on it. The voice picker in Settings then offers exactly
   those four, favourite first, with the note under the field.
6. `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than
   613 passing.
