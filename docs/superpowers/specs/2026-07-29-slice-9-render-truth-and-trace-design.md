# Slice 9 — Render truth, parity with the old pipeline, and a trace worth reading

**Goal:** make the app able to finish a video. Today it cannot: the render's
required input file has no writer, the two compositing passes the old app ran
are never called, the voice phase imports a library that is not installed, and
when any of that fails nothing on disk or in the UI records why.

**Date:** 2026-07-29. Verified against the code and against ten real project
folders in `D:\AI_Video_Gen\output` on that date.

Companion slice: the eighteen items raised in this session split in two. This
spec is the correctness half. The visible-UI half (library redesign, script
preview, settings UI, status-pill animation, the duplicated-slider flash, Live
List search, "Test Providers", dependency auto-install, another pass at startup
time) is Slice 10 and is out of scope here.

---

## 1. What the audit found

Every item below was read off disk or out of the code, not inferred.

**Render has never worked in this app.** `avg/media/vendor/avg.py:2228`
resolves `render/index.json` and `sys.exit(1)`s when it is absent. Nothing in
this codebase writes that file — Visuals writes `images/manifest.json` instead
(`avg/pipeline/phases/visuals.py:248`), and `avg/core/takes.py:67` says so out
loud: "no phase in this codebase writes an `index.json` at all yet". Two cards
in the library read `Failed · render`.

**Both compositing passes are missing.** The vendored engine ships
`cmd_overlay_render` (`avg.py:3096`) and `cmd_caption_burn` (`avg.py:3329`).
Nothing under `avg/media/` calls either. `MediaEngine.render()` runs
`render_project` — legacy Phase 7 Part 1 — and stops there. So
`render/card_plan.json` is a plan nothing draws and `render/captions.ass` is a
subtitle file nothing burns. That is why finished videos carry neither.

**Voice cannot run on this machine.** `avg/pipeline/phases/voice.py:148`
imports `pydub` for assembly and `:229` calls `remove_silences_core`, which
imports it too (`avg.py:217,333`). `pydub` is installed in neither
`.venv-build` nor the packaged app. The suite passes only because
`tests/pipeline/test_phases_media.py` stubs both calls. `avg/core/health.py:101`
calls the package optional and "only refines silence handling" — false, there
is no path that skips it. One library card reads `Failed · voice`.

**Resuming a project clones a take.** `web/src/Studio.tsx:527` answers
`Continue · X` with `createTake(reroll_from: X)`.
`20260729_162527_why_lighthouses_were_painted_in_stripes` holds takes 2 through
6, each containing the same four script files and nothing else — the same
resume pressed five times.

**The big button renders whatever you are aiming at.** `Studio.tsx:504-526`:
with every phase done the label is `Run again` and the handler calls
`onStartRender()`, ignoring the rail selection entirely.

**Almost nothing reaches the error store.** Two call sites exist: the HTTP
middleware for unhandled request exceptions (`avg/api/app.py:365`) and the UI
reporting itself (`avg/api/routes_settings.py:192`). Phase failures,
render-child exits, and provider errors never arrive. A real failure sitting
unlogged on disk right now: `brief failed 'NoneType' object has no attribute
'phases'` in `20260726_224316_uh/project.json`.

**No project has a log.** Legacy wrote a per-project `session.log`
(`D:\AI_Video_Gen\info.txt:727-732`). No project created by this app has any
log file at all.

---

## 2. Parity audit: legacy pipeline versus this app

The comparison asked for, phase by phase, from `info.txt:211-334` against the
current `avg/pipeline/`. "Missing" means no code in this repo produces it.

| Legacy | Here | Verdict |
|---|---|---|
| Phase 0 Brief: two parts — research call cached to `topic_research.txt`, then the brief | Brief writes `script/brief.json` only | **Missing: research.** In scope |
| Phase 1 Script: two AI calls, provisional `[MM:SS]` timestamps, review loop | Two-pass script present; substate derived from disk | Parity |
| Phase 1: `[pause]` / `[[focus]]` / `((question))` markers dropped inline by the writer | Only `[pause]` is honoured, in Voice | **Missing: the other two.** In scope |
| Phase 2 Director I: `video_map.json`, then `timeline.json`, `beat_map.json`, `dynamic_pauses.json`, `text_markers.json` | `video_map.json` only | **Missing: four files.** In scope |
| Phase 3 Visuals: one image per scene, `render/index.json`, then `safezones` → `scene_safezones.json` | Images + `images/manifest.json` | **Missing: index and safezones.** In scope |
| Phase 4 Voice: flat/per-scene TTS then `remove_silences` | Both present | Parity, but blocked by `pydub` |
| Phase 5 Director II part 1: `direction_plan.json` + `scene_timings.json` | `direction_plan.json` only | **Missing: timings.** In scope |
| Phase 5 part 2: overlay/card PNG assets → `_final_layers.json`, `card_plan.json` | `card_plan.json` only, no assets | **Missing: the assets.** In scope |
| Phase 6 Captions & Takeover Text: transcribe to `.ass`, burn nothing | Present, own AssemblyAI path plus an estimate fallback | Parity |
| Phase 7 Render part 1: base video | Present | Parity |
| Phase 7 part 2: overlays, cards and captions composited | Absent | **Missing.** In scope |
| Phase 7 tail: alpha ProRes 4444 filter clips | Absent | Out of scope, deliberate |
| Re-usable image recycling pool | Absent | Out of scope, deliberate |
| Smart-render cache / prebuild knobs (`render_opts` exists but no setting fills it) | Field present, always empty | Out of scope, deliberate |
| Persistent render server, one Python process per session | Child per render, by design | Not a gap |

---

## 3. Decisions

Each was chosen from stated alternatives during brainstorming.

1. **Render stays one phase with two sub-parts, one child process per part.**
   Not one child running everything (a crash would lose the base encode), not
   two registry phases (the phase count is in `phases.ts`, the costs table,
   `PHASE_ARTIFACTS`, the artifacts display table, `legacy_import`'s aliases,
   and every project record on disk).
2. **`Continue · X` resumes the take being viewed, in place.** A new take is
   for re-running something that already finished, which is what takes are
   for.
3. **The big button names the rail's selection** and opens the new-take panel
   with it pre-picked. Re-running a finished phase goes to a new take, because
   the alternative overwrites the artifacts being compared against with no
   undo.
4. **`take.log` carries events, one line per provider call, and child stderr,**
   with full prompt/response bodies behind a `verbose_log` setting. Keys are
   scrubbed on the way in.
5. **The error store records failures *and* recovered degradations,** the
   latter at `info` severity. A run that quietly produced a worse video is
   exactly the class of event that went unseen.
6. **`captions` → `screen_text` including the key,** with a permanent load-time
   alias so the thirteen projects on disk keep their records.
7. **`pydub` becomes a declared requirement.** No second audio implementation
   over ffmpeg; the fallback would be exercised almost never and would rot.

---

## 4. Architecture

### 4.1 File contracts

The files below gain owners. Each is the owning phase's own artifact: listed in
`PHASE_ARTIFACTS` so a clone copies it, and in `avg/core/artifacts.py` where it
is worth showing to the user. Two exceptions, stated so nobody adds them later
by symmetry: `take.log` is a run record, not an artifact (§4.3), and the
transcription cache is a cache — a clone that *keeps* Screen Text copies
`captions.ass` and never re-transcribes, and a clone that re-runs it should pay
for a fresh transcription of its own audio.

| File | Owner | Contract |
|---|---|---|
| `render/index.json` | Visuals | Flat list of image paths relative to the project folder, ordered so position *n* is scene *n+1* — the mapping `avg.py:2236` performs. A scene whose image failed takes **the nearest previous successful image**; its narration still plays, and omitting the entry would shift every later scene against the audio. The first scene failing takes the nearest *following* image. All scenes failing is already a hard failure in Visuals. |
| `script/timeline.json` | Director I | Scene and takeover nodes plus chapter parts, in the legacy shape `compute_scene_timings` and `cmd_overlay_render` read (`t["type"] in ("scene", "takeover")`, `t["scene"]`, and `headline`/`sub` on a takeover). Without takeover nodes, takeover cards cannot exist. |
| `script/text_markers.json`, `script/dynamic_pauses.json`, `script/beat_map.json` | Director I | Extracted from the writer's inline markers: `((question))` → a Focused card source, `[[focus]]` → an emphasis window, `[pause]` → the dynamic-pause list Voice already honours implicitly. |
| `render/scene_safezones.json` | Visuals | The vendored `safezones` subcommand, run right after images land, on the source images rather than rendered frames (`avg.py:223-234`). Bad images fail before voice and render time is spent, and the overlay pass learns where text may sit. |
| `render/scene_timings.json` | Director II | Exact per-scene windows. `render_project` reuses a valid file and only recomputes blind when it is absent (`avg.py` Stage 0), and `cmd_overlay_render` requires it outright — it exits 2 without one (`avg.py:3128-3131`). |
| `render/edit_plan.json` | Director II | The overlay list `cmd_overlay_render` reads as `plan["overlays"]`, each entry `{element, scene, duration, transition_in, transition_out, ride_motion}` plus element-specific text. **Derived from `card_plan.json`, never a second card table** — `card_plan.json` remains the one place a card's text and accent are decided. |
| `render/_final_layers.json` + `overlays/*.png` | Director II part 2 | Real PNGs from the vendored `overlay_design.render_overlay(spec, W, H, brand)`, plus takeover cards as `{"element": "chapters_takeover_cards", "headline", "sub"}` per takeover node (`avg.py:3233`). PNGs live in `<project>/overlays/`, which is the directory the vendor itself uses (`avg.py:3115`); `_final_layers.json` names each one by path and the burn pass drops any entry whose file is missing. Their existence is what lets the caption pass bake overlays and captions in **one** encode (`avg.py:3399-3408`). Brand comes from `overlay_design.DEFAULT_BRAND`: `_load_brand` tolerates a missing `dynamic_options.txt` and returns defaults (`avg.py:2750-2753`), so no legacy config file is shipped. |
| `render/_cache/transcript_<key>.json` | Screen Text | The vendored transcription cache, keyed by `_seg_cache_key("transcript", 0, provider, audio_file)` (`avg.py:3557`). Screen Text already has a word list; writing it here means the burn stage reads a cache hit and never re-uploads or re-pays (`avg.py:3563-3568`). The `provider` string is whatever produced the words — `"assemblyai"` or `"estimated"` — and the same string is passed to the burn, so the key matches. On a miss with no key the vendor exits 2, skipping captions but still baking overlays (`_bake_deferred_overlays`). |

### 4.2 The two-part render

`RenderPhase.run` becomes two stages, each a spawned child:

- **Part 1 · Base video.** Today's path: `MediaEngine.render()` →
  `render_project` → `master_output.mp4`. Unchanged except that
  `render/index.json` now exists for it to read.
- **Part 2 · Overlays & captions.** A new `MediaEngine.composite()` and a new
  `avg/media/child.py::run_composite_child`. Two routes, exactly as the vendor
  intends:
  - `render/_final_layers.json` present (the normal case, because Director II
    part 2 pre-rendered the assets) → **`cmd_caption_burn` alone**, which bakes
    those layers and the captions in one encode (`avg.py:3399-3408`). Calling
    `cmd_overlay_render` first here would encode the overlays a second time.
  - absent (Director II part 2 failed, or produced nothing to draw) →
    `cmd_overlay_render` to composite onto the finished base, then
    `cmd_caption_burn`.

  Both are `cmd_*` wrappers that `sys.exit`, so the child wraps each in
  `try/except SystemExit` and reports the code per call. The vendor's three-way
  contract is preserved verbatim: **0 applied, 2 skipped (non-fatal), 1 fatal**.
  A 2 becomes a recorded degradation and a `take.log` line, never a failed
  render — a video with overlays but no captions is a real, honest outcome, and
  calling the whole render failed would throw away a finished encode. The
  vendor's own no-loss path already agrees: a caption skip still bakes the
  deferred overlays before exiting 2 (`_bake_deferred_overlays`).

Overall progress is stage-mapped by the parent: Part 1 spans 0–70%, Part 2
70–100%. Neither stage invents a percentage for work it cannot measure.

**Sub-part state is derived from disk, never stored** — the precedent is
`script_substate`, derived at read time in `avg/api/app.py:509`:

- no `master_output.mp4` → pending
- `master_output.mp4` but no `render/_composite.json` → part 1 of 2
- both → done

`render/_composite.json` is ours, written by the composite child, and records
what was actually baked: `{"overlays": <count>, "captions": "burned" |
"skipped: <reason>", "at": <iso>}`. It is the answer to "why has my video no
captions", available without reading a log.

The rail renders the two parts with the sub-row mechanism Script already uses
(`PHASE_DEFS[].sub`, and the `1/2` partial state that exists in
`web/src/Studio.tsx:647`).

### 4.3 `take.log`

New `avg/core/take_log.py`. One log per take: the project folder for a
`storage="root"` take, `takes/<id>/` otherwise — resolved through
`avg.core.takes.take_root`, never `take_folder`.

- Levels `INFO` / `WARN` / `ERROR` / `DEBUG`, one timestamped line each.
- Consecutive identical lines collapse to a single `(xN)` entry, as legacy did
  for retry-loop noise (`info.txt:730`).
- Writers: `avg/pipeline/runner.py` (every typed event), `transport.py` (one
  line per HTTP call — method, host, model, status, milliseconds, and the
  retry or fallback decision taken), both render children (stderr verbatim),
  and every caught exception with its traceback.
- Secrets are scrubbed through the scrubber `transport.py` already applies to
  recorded fixtures. There is exactly one scrubber.
- A new `verbose_log` setting (`scope="app"`, `invalidates=None`) adds full
  prompt and response bodies.
- **Not in `PHASE_ARTIFACTS`.** A clone must not inherit another take's run
  record. It is reachable through its own route instead, so the UI can show
  and copy it.

Nothing below `avg/api/` prints: the log is a file write, which is not printing
and not prompting, and the API layer still decides what reaches the screen.

### 4.4 Error-store coverage

`avg/core/error_store.py` keeps its shape; what changes is who calls it.

Recorded as failures: every `PhaseFailed` the runner sees, every non-zero
render-child exit (with the child's stderr as detail), and every exception
escaping a job worker thread in `avg/api/app.py`.

Recorded at `info` severity as degradations: ElevenLabs → Edge, a text backend
falling through to the next, AssemblyAI → estimated timings, a caption skip
(vendor exit 2), an overlay skip (vendor exit 2), and a Visuals shortfall where
some scenes have no image.

Every entry carries `project_id`, `take_id` and `phase` in its context, so the
panel can link to the take and its log. Retry noise inside a single call stays
in `take.log` and does not become an entry.

### 4.5 Resume, re-run, rename

- **New route** `POST /api/projects/{id}/resume` with `{"from_phase": key}`,
  running the pipeline against the take being viewed, in place. It takes the
  same single `render_thread` slot and answers 409 when a job is running.
  `current_take_id` advances only when a take finishes, as now.
- **`Studio.tsx`**: `Continue · X` calls the new route. With every phase done
  the label becomes `Run again · <selected phase>` and opens the new-take panel
  pre-picked with the rail's selection. `Start render` appears only when render
  is genuinely the next unfinished phase. There is no path from the header CTA
  to a render the user did not ask for.
- **Rename**: key `captions` → `screen_text` in `REGISTRY`, `PHASE_ARTIFACTS`,
  `avg/core/artifacts.py`, `avg/core/costs.py`, every `SettingSpec.invalidates`
  that names it, `avg/config/legacy_import.py`, and `web/src/phases.ts`.
  Display name "Screen Text", because the phase owns both caption text and
  takeover-card text — legacy called it "Captions & Takeover Text"
  (`info.txt:225`). A load-time alias `{"captions": "screen_text"}` applied
  where a `ProjectDoc` is read keeps existing projects intact; it is permanent
  and documented as such.

### 4.6 `pydub`

Added to `.venv-build` and to the PyInstaller build, `health.py`'s spec flipped
to `required=True`, and its docstring corrected — the claim that a missing
`pydub` only costs silence refinement is what let a hard requirement sit red on
the health screen while the voice phase failed.

---

## 5. Testing

Fixture replay only; `tests/conftest.py`'s `AVG_REPLAY_FIXTURES=1` stays. No
live call, no real render in the suite.

Load-bearing tests, one per invariant that has already cost something:

- `index.json` under a failed scene: position *n* still means scene *n+1*, and
  the gap is filled with the nearest previous image. This is the A/V sync
  invariant.
- The composite child driven through all three vendor exit codes: 0 records
  applied, 2 records a degradation and leaves the render successful, 1 fails
  the phase.
- `render_substate` derived from disk for all three states.
- `take.log`: a key passed to a provider never appears in the file; identical
  consecutive lines collapse; a clone does not copy the log.
- The alias: a `project.json` whose phases are keyed `captions` loads with a
  `screen_text` record and reports the phase done.
- Resume creates no take, and returns 409 while a job runs.
- `edit_plan.json` derives from `card_plan.json` and never restates a card's
  text.

Suite is at 672 passing. Expected range afterwards, ~715.

---

## 6. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 672.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Create a video end to end. It finishes, and the finished mp4 **has caption
  text and at least one card on screen**. This is the acceptance test for the
  whole slice.
- `render/_composite.json` states what was baked.
- Open `take.log` for that run: every phase, every provider call, no key
  anywhere in it.
- Kill an image provider mid-run: the failed scene shows the previous image and
  the narration stays in sync.
- Press `Continue · <phase>` on an unfinished project: no new take appears.
- With every phase done, select Script in the rail: the button reads `Run
  again · Script` and opens the new-take panel, not a render.
- The dependency card shows `pydub` green.
- Recent errors lists the run's degradations, and each entry names its project,
  take and phase.
- `"Build Installer.bat"` — the packaged app does all of the above.

---

## 7. Out of scope, on purpose

Slice 10 (the visible-UI half): video library redesign, script preview
redesign, settings UI, status-pill circular animation with the label sliding
into the ring, the duplicated slider that flashes on screen change, Live List
search, "Test Providers" replacing "Build model cache" and grouping its
survivors under Working, dependency auto-install, and another attempt at
startup time (already 4.33s → 2.25s in Slice 7).

Not planned at all: alpha ProRes 4444 filter overlays, the re-usable image
recycling pool, smart-render cache and prebuild settings, and a persistent
render server.
