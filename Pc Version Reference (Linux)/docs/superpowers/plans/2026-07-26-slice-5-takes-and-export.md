# Slice 5 — Takes and export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-run a video with something changed, re-doing only the phases that change actually invalidates, keeping every earlier take intact — then hand the best one to CapCut.

**Architecture:** A project gains **takes**: self-contained folders, each with a settings snapshot, its own phase records and its own output. One new field on each setting declares the first phase its change invalidates; that field drives both the minimal re-run and the "here is what this costs you" preview. Export assembles a CapCut-ready folder from a take.

**Spec:** `docs/superpowers/specs/2026-07-26-slice-5-takes-and-export-design.md`

**Tech Stack:** Python 3.12 · pydantic v2 · FastAPI · React 19 + Vite · pytest

## Execution mode

Carried over from Slices 1–4 at the user's standing request: speed over ceremony.

- No per-step RED/GREEN cycles. Write the code and the tests, run the suite once at the end of a task.
- **One commit per task.**
- **No front-end unit tests.** Verify UI by looking at it.
- Python tests stay — they cover what looking cannot.
- Review checkpoints after Task 2 and at the end.

## Global Constraints

- **Windows only.** Python 3.12. Nothing below `avg/api/` may print or prompt.
- **No live API calls in tests, ever.** Slice 3's recorded fixtures replay; the transport raises on an unmatched request. AssemblyAI, ElevenLabs and image providers cost real money.
- **`avg/media/vendor/` is protected** — byte-for-byte copies of the old render engine. Never edit.
- **Exactly one terracotta (`#e08a4e`) action per screen.** Terracotta means "happening right now" and nothing else. Sage `#aebf92` marks settled state. Three earlier tasks had to fix violations of this.
- **Design tokens are authoritative** (`web/src/tokens.css`): canvas `#1c1a16`, text `#f2ebdd`, Caprasimo display over Figtree body, both self-hosted — **never add a Google Fonts import**, it was removed so the app works offline. Radii 12/20/28. Both `night` and `day` themes must work. Sentence case everywhere.
- **A key value never appears** in a log, an exception, a repr, or an API response.
- `D:\AI_Video_Gen` is read-only reference.
- Build venv: `D:\AVG_Studio\.venv-build\Scripts\python.exe`. **442 tests currently pass and must stay green.**

---

## Existing code this slice builds on

Exact signatures, verified today — do not re-derive them:

```python
# avg/core/models.py
ProjectDoc(schema_version, id, title, folder, aspect_ratio, short_edge, created, phases, master_output)
PhaseRecord(name, status, started, finished, duration_ms, error)   # status: pending|running|done|failed

# avg/core/settings_schema.py
SettingSpec(id, group, label, help, type, default, scope, choices)  # 39 entries in SETTINGS

# avg/config/channels.py
Channel(schema_version, id, label, note, settings)

# avg/pipeline/runner.py
run(project, *, channel, router=None, start_from=0, only=None,
    cancel_token=None, phases=REGISTRY) -> Iterator[Any]

# avg/pipeline/create.py
new_project(topic: str, channel: Channel, output_root: Path) -> ProjectDoc

# avg/pipeline/registry.py
REGISTRY  # the single source of phase order, 8 real phases, ids 0-7
```

## File structure

| Path | Responsibility |
|---|---|
| `avg/core/takes.py` | `Take` model, take folder layout, clone/delete |
| `avg/core/invalidation.py` | `first_invalid_phase(changed_ids)`, `phases_to_rerun`, `estimate_seconds` |
| `avg/core/settings_schema.py` | **modify** — add `invalidates` to `SettingSpec` and to all 39 entries |
| `avg/core/models.py` | **modify** — `ProjectDoc.takes`, `current_take_id` |
| `avg/api/routes_takes.py` | take routes: list, create, delete, select, export |
| `avg/export/capcut.py` | assemble the CapCut folder |
| `web/src/TakeStrip.tsx` | the horizontal take selector |
| `web/src/NewTakePanel.tsx` | slide-over: settings, re-roll, consequence preview |
| `tests/core/test_invalidation.py` | the map, including completeness |
| `tests/core/test_takes.py` | clone semantics, failure isolation |
| `tests/api/test_routes_takes.py` | routes |
| `tests/export/test_capcut.py` | folder contents, timing.csv, srt |

---

### Task 1: The invalidation map

The heart of the slice, and pure data — build it first so everything else can lean on it.

**Files:**
- Modify: `avg/core/settings_schema.py`
- Create: `avg/core/invalidation.py`
- Test: `tests/core/test_invalidation.py`

**Interfaces:**
- Produces:
  - `SettingSpec.invalidates: str | None` — a phase **key** (`"brief"`, `"script"`, `"director1"`, `"visuals"`, `"voice"`, `"director2"`, `"captions"`, `"render"`) or `None` for settings that are not creative inputs.
  - `first_invalid_phase(changed_ids: Iterable[str]) -> int | None` — the lowest phase **id** any of those settings invalidates; `None` if none of them invalidate anything.
  - `phases_to_rerun(changed_ids: Iterable[str]) -> list[str]` — phase keys from that id onward, in `REGISTRY` order.

**`estimate_seconds` is deliberately NOT in this task.** It reads take
history, and `ProjectDoc.takes` does not exist until Task 2 — so it lives
there, alongside the model it depends on. This task stays pure: it maps
setting ids to phases and nothing else, with no `ProjectDoc` import at all.

**The map — assign exactly this, per the spec:**

| `invalidates` | Setting ids |
|---|---|
| `brief` | `content_shape`, `video_form` |
| `script` | `length_preset`, `dynamic_pauses_enabled` |
| `director1` | `visual_tone`, `style_lock`, `auto_style_lock` |
| `visuals` | `aspect_ratio`, `image_provider`, `image_model`, `image_quality` |
| `voice` | `voice_provider`, `voice_id`, `voice_model_id`, `voice_stability`, `voice_speed` |
| `director2` | `ken_burns`, `brand_preset` |
| `captions` | `captions_enabled`, `captions_provider`, `captions_position`, `captions_animation_style`, `captions_font_size`, `font_family` |
| `render` | `fps`, `render_codec`, `render_engine` |
| `None` | `python_exe`, `theme`, `performance_mode`, `run_as_admin`, and all 8 key settings |

**`dynamic_pauses_enabled` maps to `script`, not `voice`.** The `[pause]` markers are written during script generation; the voice phase only reads them. This is the one row nobody guesses right, and getting it wrong means re-running voice, getting identical audio, and having no idea why.

- [ ] **Write the completeness test first — it is the most valuable test in this slice:**

```python
# tests/core/test_invalidation.py
from avg.core.settings_schema import SETTINGS
from avg.pipeline.registry import BY_KEY

def test_every_setting_declares_what_it_invalidates():
    """A setting added later must not silently default to invalidating nothing.

    The dangerous failure is not a wrong row today - it is someone adding a
    setting in six months, forgetting this field, and re-runs quietly doing
    the wrong thing from then on. `invalidates` has no default for exactly
    this reason; this test is what makes forgetting it unmergeable.
    """
    missing = [s.id for s in SETTINGS if not hasattr(s, "invalidates")]
    assert missing == [], f"settings with no invalidates field: {missing}"

def test_every_invalidates_value_is_a_real_phase_key():
    bad = [(s.id, s.invalidates) for s in SETTINGS
           if s.invalidates is not None and s.invalidates not in BY_KEY]
    assert bad == [], f"settings pointing at phases that do not exist: {bad}"

def test_dynamic_pauses_invalidates_the_script_not_voice():
    spec = next(s for s in SETTINGS if s.id == "dynamic_pauses_enabled")
    assert spec.invalidates == "script"
```

- [ ] Add `invalidates: str | None` to `SettingSpec` **with no default**, so a new entry cannot omit it, and fill in all 39 rows from the table above.
- [ ] Implement `first_invalid_phase` and `phases_to_rerun` in `avg/core/invalidation.py`. Order comes from `REGISTRY` — never hardcode a phase list. This module must not import `ProjectDoc`; it maps setting ids to phases and nothing more.
- [ ] Add tests: a change set spanning two phases picks the **earlier** one; a change set of only non-creative settings returns `None`; an unknown setting id is ignored rather than raising; `phases_to_rerun` returns keys in `REGISTRY` order and ends at `render`.
- [ ] Run the suite once, commit.

---

### Task 2: The take model

**Files:**
- Create: `avg/core/takes.py`
- Modify: `avg/core/models.py`
- Test: `tests/core/test_takes.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `Take(schema_version: int, id: int, label: str, created: datetime, settings_snapshot: dict, changed_from: int | None, changed_ids: list[str], rerolled_from: str | None, phases: dict[str, PhaseRecord], master_output: Path | None)`
  - `ProjectDoc.takes: list[Take]`, `ProjectDoc.current_take_id: int | None`
  - `take_folder(project: ProjectDoc, take_id: int) -> Path` → `<project.folder>/takes/<id>/`
  - `clone_take(project, source_take_id, keep_phase_keys) -> Take` — creates the folder, copies only the artifacts of `keep_phase_keys`, registers the take **after** the copy succeeds
  - `delete_take(project, take_id) -> None` — refuses the last remaining take
  - `migrate_to_takes(project) -> None` — an existing single-output project becomes take 1, current
  - `estimate_seconds(project: ProjectDoc, phase_keys: Sequence[str]) -> tuple[int | None, list[str]]` — moved here from Task 1, which cannot own it: it reads take history. Returns `(seconds, unknown_phase_keys)`. For each key, the `duration_ms` from the most recent take in which that phase **completed**. Returns `(None, keys)` when no key has any history — a fabricated estimate is worse than none.

**Rules that must hold:**

- **A new take starts with no `master_output`.** A failed render must not inherit the previous take's video and look successful. `MediaEngine` already carries this guard for the same reason.
- **Register after copying.** If the copy fails part-way (disk full), delete the folder and raise — `project.json` never holds a half-created take.
- **Current vs selected.** `current_take_id` advances to each take that finishes successfully; the user can pin another. Viewing a take in the UI does not change it.
- Phase→artifact mapping for the copy: `brief`→`script/brief.json`, `script`→`script/`, `director1`→`render/index.json`, `visuals`→`images/`, `voice`→`audio/`, `director2`→`render/direction_plan.json`+`render/card_plan.json`, `captions`→`render/captions.*`. Read each phase module to confirm before writing this — they are the authority, not this table.

- [ ] Implement, then test: `estimate_seconds` sums the most recent completed run per phase, reports phases with no history as unknown, and returns `None` rather than `0` when nothing has ever run; clone copies only kept artifacts; a cloned take has no `master_output`; a mid-copy failure leaves no take registered and no folder behind; deleting the last take is refused; deleting the current take moves `current_take_id` to another; `migrate_to_takes` on a legacy project yields exactly one take marked current.
- [ ] Run the suite once, commit.

**CHECKPOINT REVIEW after this task.**

---

### Task 3: Take routes and the re-run

**Files:**
- Create: `avg/api/routes_takes.py`
- Modify: `avg/api/app.py` (mount the router)
- Test: `tests/api/test_routes_takes.py`

**Interfaces:**
- Consumes: Task 1's `first_invalid_phase`/`phases_to_rerun`; Task 2's `clone_take`/`delete_take`/`estimate_seconds`.
- Produces:
  - `GET  /api/projects/{pid}/takes` → `{"takes": [...], "current_take_id": N}`
  - `POST /api/projects/{pid}/takes/preview` → body `{"overrides": {...}, "reroll_from": "script"|null}` (request field `reroll_from`; the stored `Take.rerolled_from` records what actually happened - the asymmetry is intentional) → `{"rerun": ["director1", ...], "keeps": ["brief","script"], "estimate_seconds": 252|null, "unknown": []}` — **computes only, creates nothing**
  - `POST /api/projects/{pid}/takes` → same body → `201 Take`, clones and starts the pipeline
  - `DELETE /api/projects/{pid}/takes/{tid}`
  - `POST /api/projects/{pid}/takes/{tid}/select` → pin as current
  - `PATCH /api/projects/{pid}/takes/{tid}` → body `{"label": "..."}`, rename only

**Auto-labelling** (spec section 5, otherwise unassigned): a take names itself
from what produced it, using the setting's `label` from the schema rather than
its id - `"Take 2 · visual tone"`, not `"Take 2 · visual_tone"`. A re-roll
reads `"Take 3 · re-roll script"` using the phase's `name` from `REGISTRY`.
Several changed settings at once: the label names the earliest-invalidating one
and appends `+2 more`. A renamed take keeps its user label forever - never
regenerate over it.

**Rules:**

- Follow the **existing background-job pattern** in `avg/api/app.py` — one job at a time, `409` naming the in-flight project, events broadcast over `/ws/events`. Do not invent a second mechanism.
- `reroll_from` and `overrides` may be combined; the start phase is the **earlier** of the two.
- **A request with no overrides and no `reroll_from` is `400`.** It would spend tokens reproducing what already exists.
- The pipeline runs with `start_from=<computed id>` against the **new take's folder**.

- [ ] Implement, then test: preview computes without creating; an empty request is 400; a `voice_id` change reruns voice onward and keeps images; a combined change picks the earlier phase; a second concurrent take is 409; delete and select behave.
- [ ] Run the suite once, commit.

---

### Task 3.5: Persist the project document

**Added mid-slice, 2026-07-26, after Task 3 exposed the gap.** `project.json`
is written exactly once, by `avg/pipeline/create.py` at project creation, and
never again. Phase completions, take creation, renames, deletions and
`current_take_id` all live only in the API process's memory.

Before takes this was survivable: the pipeline wrote its artifacts to disk and
`avg/config/legacy_import.py` re-derived phase state by inspecting the folder on
the next launch. **A take list cannot be re-derived that way** - which take
changed which setting, what it was labelled, which one is current, and how long
each phase took are facts about history, not about files on disk. Restart the
app and every take is gone.

Building Task 4's UI over data that evaporates would be building on sand.

**Files:**
- Modify: `avg/core/models.py` or `avg/pipeline/create.py` - one `save_project(doc)` writing `<folder>/project.json` atomically
- Modify: `avg/pipeline/runner.py` - persist after each phase record changes
- Modify: `avg/api/routes_takes.py` - persist after every take mutation
- Modify: `avg/config/legacy_import.py` - read `takes` and `current_take_id` when present
- Test: `tests/core/test_persistence.py`

**Interfaces:**
- Produces: `save_project(project: ProjectDoc) -> None` - atomic write (temp file then replace), the same discipline `avg/config/settings.py` already uses

**Rules:**
- **Write-through, not write-at-end.** A crash or a kill mid-pipeline must not lose the phases that already finished - that is precisely when the record matters most.
- **Atomic.** A half-written `project.json` would make the project unreadable and there is no backup. Temp file plus replace, never a truncating write in place.
- **The importer must round-trip.** A project written with takes reads back with the same takes, same labels, same `current_take_id`. A legacy project without a `takes` key still imports exactly as it does today - all ten real projects must keep working.
- Persist on: phase status change, take create/delete/rename/select, and `current_take_id` change.

- [ ] Implement, then test: a phase completing writes the file; killing mid-run keeps completed phases; a take survives a simulated restart (write, re-read, compare); a legacy project with no `takes` key still imports; an interrupted write leaves the previous file intact.
- [ ] Run the suite once, commit.

---

### Task 4: The take strip and new-take panel

**Files:**
- Create: `web/src/TakeStrip.tsx`, `web/src/NewTakePanel.tsx`
- Modify: `web/src/Studio.tsx`, `web/src/Home.tsx`

**Design — from the approved spec, not to be re-invented:**

- **Two perpendicular axes**: phases down the left rail, takes across the top, under the project header. No new screen — selecting a take repoints the existing rail, canvas and activity log.
- Each take chip: number, label, status dot, duration. Running take → terracotta dot; done → sage; failed → red.
- **Selection is an elevated surface with a stronger border, never the accent.** Terracotta belongs to the take that is running. Two accented things on screen and the running take stops being findable.
- **New take is a slide-over from the right**, so the take being branched from stays visible. It reuses Slice 2's schema-driven field renderers in `web/src/fields/` **unchanged** — that is why they were built that way.
- The consequence preview updates live from `POST .../takes/preview` as fields change: which phases re-run, what is kept, and the estimate. **When the estimate is `null`, say nothing about time** — do not print "unknown" or a zero.
- One terracotta action in the panel: *Create take*.
- Home: a card gains `3 takes` in its mono meta line; the thumbnail comes from the current take.
- **Each project shows its total size on disk** (spec section 8), in the Studio header beside the format chips - takes are 30-75 MB each and there is deliberately no auto-pruning, so the number is how the user knows when to delete one. Compute it server-side; do not walk the folder from the browser.
- Every take chip offers **delete**, with a confirmation. Deleting the last remaining take is refused by the API - surface that refusal, do not hide the control.
- **Single-take projects still show the strip** (`Take 1 · + New take`), or the ten imported projects never reveal the feature exists.

- [ ] Implement, look at it in both themes with a real multi-take project, run the Python suite once, commit.

---

### Task 5: CapCut export

**Files:**
- Create: `avg/export/capcut.py`
- Modify: `avg/api/routes_takes.py` (add the route), `web/src/Studio.tsx` (header button)
- Test: `tests/export/test_capcut.py`

**Interfaces:**
- Produces: `export_take(project: ProjectDoc, take_id: int) -> Path`, and `POST /api/projects/{pid}/takes/{tid}/export` → `{"folder": "<path>"}`

**Output — exactly this shape:**

```
<project>/exports/take-2/
  images/           001.png, 002.png … in order
  narration.mp3
  captions.srt
  timing.csv        image,start,end,duration,text
  master_output.mp4 the reference cut
  how-to-import.txt
```

- `timing.csv` carries one row per image: filename, start and end as `M:SS.mmm`, duration in seconds, and the line of narration spoken over it. **This is the artifact that earns the feature** — CapCut lays images out in filename order with a uniform default duration, so the real work is setting each one, and this sheet puts the number beside the words.
- Source the timings from `render/scene_timings.json`, which the pipeline already writes.
- `how-to-import.txt` is a short plain-English note: import `images/` as media, drop `narration.mp3` on the audio track, import `captions.srt` as subtitles, set durations from `timing.csv`.
- **Exports from the selected take**, not the current one — you export what you are looking at.
- Open Explorer on the folder when it finishes (`os.startfile`). Skip silently when frozen tests run headless.

- [ ] Implement, then test: the folder contains every named file; `timing.csv` has one row per image and its durations sum to within 0.1s of the audio length; the `.srt` parses and its cue count matches the caption count; exporting a take with no render yields a clear error rather than a partial folder.
- [ ] Run the suite once, commit.

---

## Out of scope

Side-by-side take playback (asked and declined), automatic retention/pruning, the learning system, Train, App Chat, trend discovery, topic research, the recycling pool, composite images, and Resolve/Premiere interchange.
