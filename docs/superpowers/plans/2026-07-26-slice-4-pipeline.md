# Slice 4 — The pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Turn a topic into a finished video. The eight-phase state machine, the prompts that drive it, and the wizard that starts it.

**Replaces:** `modules/pipeline.ps1` (3,131 lines) and the script/prompt half of `modules/ai.ps1`.

**Architecture:** Extends Slices 1–3. Phases are independent units over a shared `ProjectDoc`; the runner sequences them, streams typed events, and can resume from any phase. Providers come from Slice 3, render from Slice 1.

## Execution mode

Carried over: speed over ceremony. One commit per task, suite run once at the end of a task, no per-step RED/GREEN, no front-end tests. Python tests stay.

## Global Constraints

- **Windows only.** Python 3.12. Nothing below `avg/api/` may print or prompt.
- **No live API calls in tests.** Everything runs against Slice 3's recorded fixtures or local mocks.
- **Every phase is independently runnable.** "Run again" on a single phase must work without re-running the ones before it — that is the app's fast iteration loop and it existed in the old app as Preview-a-Phase.
- **A phase advances `meta.phase` to the NEXT phase on completion**, so resume re-enters correctly. The old app's phase-order map and registry had to stay in sync by hand; here there is **one registry and nothing duplicates it**.
- **New-video start phase defaults to 0 (Brief).** The PowerShell app shipped a bug where it defaulted to 1 and silently skipped the brief.
- Creative settings come from the **active channel** (Slice 2). There is no global creative config.
- Reference app (read-only): `D:\AI_Video_Gen`.

---

## The eight phases

| Id | Key | Produces | Notes |
|---|---|---|---|
| 0 | `brief` | Creative brief: arc, beats, pacing | Drives every later phase |
| 1 | `script` | Narration + scene segmentation | **Two-pass** — see Task 3 |
| 2 | `director1` | Video map: scene → visual intent | |
| 3 | `visuals` | One image per scene | Slice 3 image providers; parallel workers |
| 4 | `voice` | Narration audio, silences removed | Slice 3 voice providers |
| 5 | `director2` | Motion/grade plan, graphics assets | |
| 6 | `captions` | Timed `.ass` subtitles | |
| 7 | `render` | `master_output.mp4` | **Already built in Slice 1** — reuse `MediaEngine` |

---

### Task 1: Phase framework, project creation and the runner

**Files:** Create `avg/pipeline/phase.py`, `avg/pipeline/registry.py`, `avg/pipeline/runner.py`, `avg/pipeline/create.py`; test `tests/pipeline/test_runner.py`

- `Phase` protocol: `id`, `key`, `name`, `run(ctx: PhaseContext) -> None`, `inputs_ready(ctx) -> bool`.
- `PhaseContext`: the `ProjectDoc`, the resolved channel settings, the provider router, an `emit(event)` callback, and a cancellation token.
- `REGISTRY`: the ordered list. **The single source of truth for phase order** — nothing else may hardcode it.
- `runner.run(project, start_from=0, only=None)` — sequences phases, updates `PhaseRecord` status/timing, emits events, honours cancellation between phases, and stops on failure without corrupting the document.
- `create.new_project(topic, channel, output_root) -> ProjectDoc` — creates the folder and the document. **Start phase 0.**

Use stub phases in this task. Real phases arrive in Tasks 3–5.

- [ ] Implement, test, commit. Tests must cover: full sequence over stubs, resume from each phase, `only=` running exactly one phase, a failing phase halting the run and recording the error, and cancellation between phases.

---

### Task 2: Prompt construction

**Files:** Create `avg/pipeline/prompts.py`, `avg/pipeline/personality.py`; test `tests/pipeline/test_prompts.py`

This is where video quality actually comes from, and it is the most easily under-valued task in the slice.

Inputs to a prompt: the topic, the channel's `content_shape`, `visual_tone`, `style_lock` and `brand_preset`, the video format and length, and the personality preset text.

Read `D:\AI_Video_Gen\Assets\personality.txt` (rewritten in B2.19.10 into three prose-first presets) and the prompt-building code in `modules/ai.ps1`. **Mine the wording** — those prompts are tuned, and their phrasing is the product.

**Note the `visual_tone` trap:** it is stored as a catalogue id but every prompt needs the descriptive text. The PowerShell app shipped a bug where the raw id reached the AI. Slice 2's schema stores ids; resolve to text here, once, and never pass an id into a prompt.

- [ ] Implement, test, commit. Tests assert a built prompt contains the tone description rather than its id, and that each content shape produces materially different instructions.

---

### Task 3: Phases 0–2 — brief, script, director

**Files:** Create `avg/pipeline/phases/brief.py`, `script.py`, `director1.py`; test `tests/pipeline/test_phases_text.py`

**Phase 1 is two-pass and this matters.** The old app's B2.19.0 change split it because one call could not both write good prose and segment it reliably:

- **Pass 1** writes the narration as flowing prose, with no scene structure at all.
- **Pass 2** segments that prose **verbatim** into scenes. It may not rewrite a single word — its only job is to decide where the cuts fall.
- The scene count is a **range**, not a fixed number; Pass 2 chooses within it.

Also from the old app: inline `[pause]` markers placed where a dramatic beat lands (B2.13), which phase 4 turns into real silence.

- [ ] Implement, test against recorded fixtures, commit. A test must assert Pass 2's output text is a verbatim partition of Pass 1's — no words added, none lost.

---

### Task 4: Phases 3–4 — visuals and voice

**Files:** Create `avg/pipeline/phases/visuals.py`, `voice.py`; test `tests/pipeline/test_phases_media.py`

- **Visuals:** one image per scene via Slice 3's image providers, with a worker pool. **Cap workers by the RAM profile** — 2 at ≤8 GB, the gate Slice 1 preserved. Emit progress per image; a single image failing must not kill the phase.
- **Voice:** synthesize narration via Slice 3's voice providers, honour `[pause]` markers, then remove silences using the **already-vendored** `avg/media/vendor/avg.py` (`remove_silences_core`). Do not reimplement silence removal.

- [ ] Implement, test, commit. Tests must cover: worker cap respected, one image failing without killing the phase, and `[pause]` markers producing real gaps.

---

### Task 5: Phases 5–6 — director II and captions

**Files:** Create `avg/pipeline/phases/director2.py`, `captions.py`; test `tests/pipeline/test_phases_polish.py`

- **Director II:** the motion/grade plan and graphics assets the render phase consumes. The vendored render engine already knows how to read these — match its expected shapes exactly rather than inventing new ones.
- **Captions:** transcribe to timed `.ass`. AssemblyAI when a key exists; otherwise derive timings from the script and audio duration. Respect the global font setting — captions and cards share it, and the PowerShell and Python resolvers had to stay mirrored.

- [ ] Implement, test, commit.

---

### Task 6: Create wizard

**Files:** Create `web/src/Create.tsx`; modify `web/src/App.tsx`, `avg/api/app.py`

One screen, matching the approved design: **topic → confirm**. The active channel already answers format, length, tone and personality, so do not ask again — show them as chips the user can override for this video only.

- `POST /api/projects` creates the project and starts the pipeline.
- Progress streams to the existing Studio view and transport bar — **they already handle phase events**; do not build a second progress UI.

- [ ] Implement, look at it in both themes, commit.

---

## Out of scope for Slice 4

Trend discovery, the learning system, app chat, batch generation. Those are the old app's remaining features and belong to a later slice.
