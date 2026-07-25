# Slice 1 — Re-render an existing project — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an `AVG.exe` that installs its own dependencies on first run, imports a project produced by the old PowerShell app, and re-renders it — with live progress, working cancel, and output matching the original.

**Architecture:** A Python engine (`avg/`) behind a FastAPI app bound to `127.0.0.1`, driven by a React UI running in WebView2 via `pywebview`. Rendering runs in a `multiprocessing` **child process** that calls the existing, unmodified `render_project()` from the old app's `avg.py`. Progress leaves the child on a `Queue` and reaches the browser over a WebSocket.

**Tech Stack:** Python 3.12 · FastAPI · uvicorn · pywebview (WebView2) · multiprocessing · pytest · React 19 + Vite + Tailwind 4 · PyInstaller and Nuitka (spike) · FFmpeg (bundled binary)

## Execution mode — speed over ceremony (set 2026-07-25)

The user has explicitly accepted more risk in exchange for pace. This section
**overrides** the per-step structure written into the tasks below.

- **No per-step RED/GREEN cycle.** Write the code and the tests, then run the
  suite once at the end of the task. Do not run pytest between every step and
  do not capture RED output as evidence.
- **One commit per task**, not per step.
- **No front-end unit tests.** Skip vitest, jsdom and testing-library entirely
  in Tasks 9 and 10 — do not scaffold them. The UI is verified by looking at
  it. The `.test.tsx` files in those tasks are cancelled.
- **Python tests stay.** They are already written out below, they run in
  seconds, and they cover the things you cannot see by looking: exit-code
  classification, the RAM gates, the importer against real data. Transcribe
  them, run them once, move on.
- **Reviews happen at three checkpoints**, not after every task: after Task 6
  (engine complete), after Task 10 (UI complete), and the final whole-branch
  review. Tasks in between go straight from implementation to the next task.
- **Unchanged:** Task 12's acceptance pass in full, including the render
  output comparison and the child-process kill test. That is the proof the
  slice works and it is not a candidate for trimming.

## Global Constraints

- **Windows only.** Windows 11 target; no cross-platform code paths.
- **Python 3.12** for the embedded runtime — numpy/moviepy/Pillow wheels are all available and prebuilt.
- **Nothing below `avg/api/` may print or prompt.** Lower layers emit typed events and log records. A `print()` in `avg/core`, `avg/config`, `avg/media`, `avg/providers` or `avg/pipeline` is a review rejection.
- **The media layer is vendored, not rewritten.** `avg/media/vendor/avg.py` and `overlay_design.py` are copies of the old app's files. The only permitted edit in Slice 1 is the progress sink hook in Task 5.
- **Renders run in a child process**, never a thread — a moviepy OOM must kill the render, not the app.
- **Every persisted document carries `schema_version`** (int, starts at `1`) with forward-only migrations.
- **RAM gates are preserved as data:** worker cap 2 at ≤8 GB, grain dropped at ≤8.5 GB, prebuild skipped below 12 GB.
- **No media files are moved or written by the importer.** It reads legacy folders and produces documents; the legacy project folder is never modified.
- **UI copy is sentence case**, never Title Case.
- Reference app (read-only): `D:\AI_Video_Gen`. Never modify it.

---

## File structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest/ruff/mypy config |
| `avg/core/models.py` | `ProjectDoc`, `PhaseRecord`, `PhaseStatus`, `RenderRequest` — pure data |
| `avg/core/events.py` | `ProgressEvent`, `PhaseFailed`, `RenderFinished` — the event vocabulary |
| `avg/core/errors.py` | `ErrorKind` enum and `AvgError` |
| `avg/config/legacy_import.py` | Legacy `project.json` + folder → `ProjectDoc` |
| `avg/config/settings_import.py` | Legacy `config.json` → `RenderSettings` |
| `avg/media/vendor/avg.py` | Vendored copy of the old render layer |
| `avg/media/vendor/overlay_design.py` | Vendored copy of the overlay renderer |
| `avg/media/engine.py` | `MediaEngine` — child process lifecycle, progress, cancel |
| `avg/media/child.py` | The child entry point that calls `render_project` |
| `avg/api/app.py` | FastAPI app: routes + WebSocket |
| `avg/api/deps.py` | Session token auth |
| `avg/shell.py` | pywebview window, random port, composition root |
| `avg/setup/components.py` | Component manifest + install/verify |
| `web/src/tokens.css` | The design tokens from the UI design |
| `web/src/App.tsx` | Router: home ↔ studio |
| `web/src/Home.tsx` | Project grid |
| `web/src/Studio.tsx` | Phase rail + render view |
| `web/src/Transport.tsx` | The transport bar |
| `tests/fixtures/legacy_project/` | Synthetic minimal legacy project (JSON + 3 tiny PNGs) |
| `tools/build.py` | Packaging entry — PyInstaller and Nuitka variants |
| `tools/compare_render.py` | Acceptance: new output vs original `master_output.mp4` |

---

### Task 1: Repo skeleton and the packaging spike

The riskiest unknown in the whole program is whether Python + WebView2 + numpy + moviepy bundles into a working executable at tolerable size and startup. Answer it before writing an app.

**Files:**
- Create: `pyproject.toml`, `avg/__init__.py`, `tools/build.py`, `tools/spike_window.py`
- Create: `docs/decisions/2026-07-24-packaging.md`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing
- Produces: a working `pytest` run; a recorded packaging decision (`pyinstaller` or `nuitka`) that Task 11 reads

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke.py
def test_package_imports():
    import avg
    assert avg.__version__ == "0.1.0"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg'`

- [ ] **Step 3: Create the package and config**

```toml
# pyproject.toml
[project]
name = "avg-studio"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn[standard]", "pywebview", "pydantic>=2"]

[project.optional-dependencies]
dev = ["pytest", "ruff", "mypy", "httpx"]
media = ["numpy", "Pillow", "pydub", "moviepy"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mypy]
strict = true
files = ["avg"]
```

```python
# avg/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Write the spike window**

```python
# tools/spike_window.py
"""Smallest thing that proves the stack bundles: a WebView2 window that has
already imported the heavy media deps."""
import sys, time
t0 = time.perf_counter()
import numpy, PIL, moviepy          # noqa: F401 — presence is the point
import webview
elapsed = time.perf_counter() - t0
html = f"<h1>AVG spike</h1><p>heavy imports: {elapsed:.2f}s<br>python {sys.version}</p>"
webview.create_window("AVG spike", html=html, width=520, height=320)
webview.start()
```

- [ ] **Step 6: Build it both ways and measure**

```bash
python -m pip install pyinstaller nuitka
python -m PyInstaller --onefile --noconsole --name avg-spike-pyi tools/spike_window.py
python -m nuitka --onefile --windows-console-mode=disable --enable-plugin=no-qt --output-filename=avg-spike-nuitka.exe tools/spike_window.py
```

Record for each: exe size in MB, cold-start seconds to visible window (run three times, take the median), and whether the window renders at all.

- [ ] **Step 7: Answer the crux question — install-then-import**

This is the assumption the entire first-run model rests on, and it is not obvious: **a frozen executable cannot straightforwardly pip-install packages and then import them itself.** PyInstaller and Nuitka both bake the import machinery at build time. If this cannot be made to work, the "small exe plus first-run install" plan collapses back into a ~400 MB bundle.

Test all three resolutions and pick on evidence:

- **(a) Fat bundle.** Freeze numpy/moviepy/Pillow into the exe. Install-then-import problem disappears; the exe becomes ~400 MB, which the user explicitly rejected.
- **(b) Sidecar interpreter.** The frozen shell stays light (FastAPI, pywebview) and the heavy deps install into a separate embedded CPython. The media child then runs under *that* interpreter — which means `multiprocessing.get_context("spawn")` no longer applies, because spawn reuses the parent interpreter. Cross-interpreter IPC would use `multiprocessing.connection.Listener`/`Client` over a loopback socket. Workable, but it re-introduces a process protocol boundary the design deliberately removed.
- **(c) Embedded interpreter, thin launcher (expected winner).** `AVG.exe` is a small launcher that boots a bundled embeddable CPython 3.12 and runs the app inside it. One interpreter throughout, so `multiprocessing` works normally, `pip install` targets the interpreter that will do the importing, and the exe stays ~20–30 MB. This is the arrangement the program spec assumes.

Minimum proof for whichever you pick: from the shipped artifact, install one package that is *not* bundled, restart, and import it successfully.

- [ ] **Step 8: Record the decision**

Write `docs/decisions/2026-07-24-packaging.md` with the measured table, the install-then-import result, and one sentence naming the winner. Decide on the numbers, not on preference. If two options both work, prefer the faster cold start — startup is the thing a user feels every launch.

**If none of the three work, stop and escalate.** Every later task assumes the app can reach its dependencies; that is not a detail to improvise around at Task 11.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml avg tools tests docs
git commit -m "feat: repo skeleton and packaging spike"
```

---

### Task 2: Domain models

**Files:**
- Create: `avg/core/models.py`, `avg/core/errors.py`, `avg/core/events.py`
- Test: `tests/core/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `PhaseStatus` — `Literal["pending", "running", "done", "failed"]`
  - `PhaseRecord(name: str, status: PhaseStatus, started: datetime | None, finished: datetime | None, duration_ms: int | None, error: str | None)`
  - `ProjectDoc(schema_version: int, id: str, title: str, folder: Path, aspect_ratio: str, short_edge: int, created: datetime, phases: dict[str, PhaseRecord], master_output: Path | None)`
  - `RenderRequest(project_folder: Path, fps: int, codec: str, threads: int, aspect_ratio: str, ken_burns: bool, scene_count: int | None, brief_path: str, timeline_path: str, scene_map_path: str, short_edge: int, kb_intensity: int, kb_mode: str, render_engine: str, render_opts: dict)`
  - `ErrorKind` — `auth | quota | billing | rate_limit | model_missing | content_filter | network | timeout | resource | dependency | bug`
  - `ProgressEvent(project_id: str, percent: int, message: str)`, `PhaseFailed(project_id: str, phase: str, kind: ErrorKind, message: str, detail: str)`, `RenderFinished(project_id: str, output: Path, size_bytes: int)`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_models.py
from pathlib import Path
from avg.core.models import ProjectDoc, PhaseRecord, RenderRequest

def test_project_doc_defaults_schema_version():
    p = ProjectDoc(id="x", title="X", folder=Path("."), aspect_ratio="9:16",
                   short_edge=1080, created="2026-07-24T10:00:00")
    assert p.schema_version == 1
    assert p.phases == {}

def test_render_request_mirrors_render_project_signature():
    r = RenderRequest(project_folder=Path("."))
    assert (r.fps, r.codec, r.aspect_ratio, r.short_edge) == (24, "libx264", "9:16", 1080)
    assert r.kb_mode == "dynamic" and r.render_engine == "auto"

def test_phase_record_roundtrips():
    rec = PhaseRecord(name="render", status="failed", error="boom")
    assert PhaseRecord.model_validate(rec.model_dump()).error == "boom"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.core'`

- [ ] **Step 3: Implement the models**

`RenderRequest`'s defaults must mirror `render_project()`'s signature exactly — `fps=24, codec="libx264", aspect_ratio="9:16", ken_burns=False, scene_count=None, short_edge=1080, kb_intensity=100, kb_mode="dynamic", render_engine="auto"`. `threads` defaults to `os.cpu_count() or 4`; `render_opts` defaults to `{}` via `Field(default_factory=dict)`.

```python
# avg/core/models.py
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

PhaseStatus = Literal["pending", "running", "done", "failed"]

class PhaseRecord(BaseModel):
    name: str
    status: PhaseStatus = "pending"
    started: datetime | None = None
    finished: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None

class ProjectDoc(BaseModel):
    schema_version: int = 1
    id: str
    title: str
    folder: Path
    aspect_ratio: str = "9:16"
    short_edge: int = 1080
    created: datetime
    phases: dict[str, PhaseRecord] = Field(default_factory=dict)
    master_output: Path | None = None

class RenderRequest(BaseModel):
    project_folder: Path
    fps: int = 24
    codec: str = "libx264"
    threads: int = Field(default_factory=lambda: os.cpu_count() or 4)
    aspect_ratio: str = "9:16"
    ken_burns: bool = False
    scene_count: int | None = None
    brief_path: str = ""
    timeline_path: str = ""
    scene_map_path: str = ""
    short_edge: int = 1080
    kb_intensity: int = 100
    kb_mode: str = "dynamic"
    render_engine: str = "auto"
    render_opts: dict = Field(default_factory=dict)
```

```python
# avg/core/errors.py
from enum import Enum

class ErrorKind(str, Enum):
    auth = "auth"; quota = "quota"; billing = "billing"; rate_limit = "rate_limit"
    model_missing = "model_missing"; content_filter = "content_filter"
    network = "network"; timeout = "timeout"; resource = "resource"
    dependency = "dependency"; bug = "bug"

class AvgError(Exception):
    def __init__(self, kind: ErrorKind, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.kind, self.message, self.detail = kind, message, detail
```

```python
# avg/core/events.py
from pathlib import Path
from pydantic import BaseModel
from avg.core.errors import ErrorKind

class ProgressEvent(BaseModel):
    type: str = "progress"
    project_id: str
    percent: int
    message: str

class PhaseFailed(BaseModel):
    type: str = "phase_failed"
    project_id: str
    phase: str
    kind: ErrorKind
    message: str
    detail: str = ""

class RenderFinished(BaseModel):
    type: str = "render_finished"
    project_id: str
    output: Path
    size_bytes: int
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/core -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add avg/core tests/core && git commit -m "feat: domain models, error kinds and event vocabulary"
```

---

### Task 3: Legacy project importer

**Files:**
- Create: `avg/config/legacy_import.py`
- Create: `tests/fixtures/legacy_project/project.json`, `.../pipeline_state.json`, `.../images/001.png`
- Test: `tests/config/test_legacy_import.py`

**Interfaces:**
- Consumes: `ProjectDoc`, `PhaseRecord` (Task 2)
- Produces:
  - `import_project(folder: Path) -> ProjectDoc`
  - `ImportReport(imported: list[ProjectDoc], failed: list[tuple[Path, str]])`
  - `import_all(output_root: Path) -> ImportReport`

The real shape, **re-verified against `D:\AI_Video_Gen\output\` on 2026-07-25**: 11 folders, 10 carrying `project.json`, 1 (`_filters_preview`) that is scratch, not a project.

`project.json` has 9 sections — `meta`, `script`, `beat_map`, `visuals`, `audio`, `render`, `graphics`, `captions`, `brief`. `pipeline_state.json` sits beside it with `topic`, `project_path`, `phase`, `created`, `last_updated`. Media lives in `audio/`, `images/`, `render/`; `master_output.mp4` sits at the folder root. This differs from `CLAUDE.md`, which is stale — **follow the disk**.

**`meta`'s real keys are `topic`, `project_path`, `created`, `version`, `form`, `length`, `video_settings`, `phase`, `last_updated`.** There is **no `aspect_ratio` and no `short_edge`** — an earlier draft of this plan assumed both. Derive instead:

- `aspect_ratio` from `meta.form`: `"shortform"` → `"9:16"`, `"longform"` → `"16:9"`. Anything else → `"9:16"`.
- `short_edge` is not recorded per project; default to `1080`.
- `meta.video_settings` is a nested dict (`content_shape`, `visual_tone`, `style_lock`, …). Slice 1 does not read it — leave it alone.

**Observed `phase` values across the real projects are `render`, `images` and `complete`.** `"complete"` means every phase finished; it is not a phase name and must not be looked up in the phase order.

- [ ] **Step 1: Build the fixture**

Create `tests/fixtures/legacy_project/project.json` with the 9 sections and `meta` mirroring the **real** key set:

```json
{"meta": {"topic": "Test topic", "project_path": "C:\\old\\out\\test", "created": "2026-07-01T10:00:00.0000000+01:00", "version": "B2.19.10", "form": "shortform", "length": "60 seconds", "video_settings": {"content_shape": "documentary"}, "phase": "render", "last_updated": "2026-07-01T10:32:00.0000000+01:00"},
 "script": {}, "beat_map": {}, "visuals": {}, "audio": {}, "render": {}, "graphics": {}, "captions": {}, "brief": {}}
```

Create `pipeline_state.json` with `{"topic": "Test topic", "phase": "render", "created": "2026-07-01T10:00:00.000+01:00", "last_updated": "2026-07-01T10:32:00.000+01:00"}`. Create a 1×1 PNG at `images/001.png`. Total fixture size must stay under 5 KB — no real media in the repo.

Create a **second** fixture `tests/fixtures/legacy_complete/` identical except `phase` is `"complete"` in both files and `meta.form` is `"longform"` — it covers the two cases the first fixture cannot.

- [ ] **Step 2: Write the failing test**

```python
# tests/config/test_legacy_import.py
from pathlib import Path
import pytest
from avg.config.legacy_import import import_project, import_all

FIX = Path(__file__).parent.parent / "fixtures" / "legacy_project"

COMPLETE = Path(__file__).parent.parent / "fixtures" / "legacy_complete"

def test_imports_title_and_derives_aspect_from_form():
    doc = import_project(FIX)
    assert doc.title == "Test topic"
    assert doc.aspect_ratio == "9:16"        # meta.form == "shortform"
    assert doc.short_edge == 1080            # never recorded per project
    assert doc.schema_version == 1

def test_longform_maps_to_widescreen():
    assert import_project(COMPLETE).aspect_ratio == "16:9"

def test_marks_reached_phases_done():
    doc = import_project(FIX)
    assert doc.phases["brief"].status == "done"
    assert doc.phases["render"].status == "pending"

def test_complete_marks_every_phase_done():
    doc = import_project(COMPLETE)
    assert {p.status for p in doc.phases.values()} == {"done"}

def test_scratch_folders_are_skipped(tmp_path):
    (tmp_path / "_filters_preview").mkdir()
    report = import_all(tmp_path)
    assert report.imported == [] and report.failed == []   # skipped, not failed

def test_missing_project_json_is_reported_not_raised(tmp_path):
    (tmp_path / "empty_project").mkdir()
    report = import_all(tmp_path)
    assert report.imported == []
    assert len(report.failed) == 1

def test_import_never_writes_to_the_legacy_folder():
    before = {p: p.stat().st_mtime for p in FIX.rglob("*") if p.is_file()}
    import_project(FIX)
    after = {p: p.stat().st_mtime for p in FIX.rglob("*") if p.is_file()}
    assert before == after
```

- [ ] **Step 3: Run it and watch it fail**

Run: `pytest tests/config/test_legacy_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.config'`

- [ ] **Step 4: Implement the importer**

Phase order is `["brief", "script", "director1", "visuals", "voice", "director2", "captions", "render"]`. `pipeline_state.json`'s `phase` names the phase the app would resume *into*, so every phase before it is `done` and that phase onward is `pending`.

- Legacy aliases: `prompts`→`script`, `images`/`visuals`→`visuals`, `audio`→`voice`, `scene_timings`/`smart_editing`→`director2`.
- **`"complete"` is not a phase** — it means all eight are `done`. Handle it before any lookup.
- An unrecognised phase value marks everything `pending` rather than raising.
- Title: `meta.topic`, falling back to `pipeline_state.topic`, then the folder name.
- `import_all` **skips folders whose name starts with `_`** (scratch, e.g. `_filters_preview`) — skipped means absent from both lists, not a failure row.
- Any exception on a real project folder becomes a `failed` row carrying `str(exc)`.

- [ ] **Step 5: Run it and watch it pass**

Run: `pytest tests/config -v`
Expected: 4 passed

- [ ] **Step 6: Run it against real data**

```bash
python -c "from pathlib import Path; from avg.config.legacy_import import import_all; r=import_all(Path(r'D:\AI_Video_Gen\output')); print(len(r.imported),'imported,',len(r.failed),'failed'); [print(' ',p.name,'-',e) for p,e in r.failed]"
```

Expected: **10 imported, 0 failed** (11 folders, `_filters_preview` skipped). Failures are acceptable output — they must be *reported*, not raised — but at this scale every real project should import. If any fail, read the reason before continuing; the fixture is probably missing a real-world shape.

- [ ] **Step 7: Commit**

```bash
git add avg/config tests/config tests/fixtures && git commit -m "feat: legacy project importer"
```

---

### Task 4: Render settings importer

**Files:**
- Create: `avg/config/settings_import.py`
- Test: `tests/config/test_settings_import.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `RenderSettings(fps, codec, aspect_ratio, short_edge, ken_burns, kb_intensity, kb_mode, render_engine, render_opts, worker_cap, drop_grain, skip_prebuild)` and `load_render_settings(config_path: Path, total_ram_gb: float) -> RenderSettings`

Only the render-relevant slice of the old 21-key `config.json` is read: `video.*`, `edit.*`, `video_format.*`, `runtime.*`. Everything else is out of scope for Slice 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_settings_import.py
import json
from avg.config.settings_import import load_render_settings

def _write(tmp_path, cfg):
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg), encoding="utf-8"); return p

def test_ram_gates_at_8gb(tmp_path):
    s = load_render_settings(_write(tmp_path, {}), total_ram_gb=8.0)
    assert s.worker_cap == 2
    assert s.drop_grain is True
    assert s.skip_prebuild is True

def test_ram_gates_on_a_big_machine(tmp_path):
    s = load_render_settings(_write(tmp_path, {}), total_ram_gb=32.0)
    assert s.worker_cap > 2 and s.drop_grain is False and s.skip_prebuild is False

def test_grain_boundary_is_8_5(tmp_path):
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.5).drop_grain is True
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.6).drop_grain is False

def test_missing_keys_fall_back_to_defaults(tmp_path):
    s = load_render_settings(_write(tmp_path, {"video": {}}), total_ram_gb=16.0)
    assert s.fps == 24 and s.codec == "libx264" and s.render_engine == "auto"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/config/test_settings_import.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement it**

Gates, exactly as the old app applies them: `worker_cap = 2 if total_ram_gb <= 8 else 4`; `drop_grain = total_ram_gb <= 8.5`; `skip_prebuild = total_ram_gb < 12`. Every config read goes through a `_get(cfg, "video.fps", 24)` dotted-path helper that never raises on a missing branch.

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/config -v`
Expected: 8 passed

- [ ] **Step 5: Verify against the real config**

```bash
python -c "from pathlib import Path; from avg.config.settings_import import load_render_settings; print(load_render_settings(Path(r'D:\AI_Video_Gen\config\config.json'), 8.0))"
```

Expected: prints a settings object with `worker_cap=2`, `drop_grain=True`, `skip_prebuild=True`.

- [ ] **Step 6: Commit**

```bash
git add avg/config tests/config && git commit -m "feat: render settings importer with RAM gates"
```

---

### Task 5: Vendor the media layer and add the progress sink

**Files:**
- Create: `avg/media/vendor/avg.py` (copy of `D:\AI_Video_Gen\python\avg.py`)
- Create: `avg/media/vendor/overlay_design.py` (copy of `D:\AI_Video_Gen\python\overlay_design.py`)
- Modify: `avg/media/vendor/avg.py` — `_write_progress` only, at its definition around line 79
- Test: `tests/media/test_progress_sink.py`

**Interfaces:**
- Consumes: nothing
- Produces: `avg.media.vendor.avg.render_project(...)` and a module-level `PROGRESS_SINK: Callable[[int, str], None] | None`

`_write_progress(project_path, percent, message)` has **14 call sites**. Do not touch them. Add the sink inside the single function they all funnel through, so the child process can forward progress over a `Queue` while the JSON file keeps working for anything else that reads it.

- [ ] **Step 1: Copy the files verbatim**

```bash
mkdir -p avg/media/vendor
cp /d/AI_Video_Gen/python/avg.py avg/media/vendor/avg.py
cp /d/AI_Video_Gen/python/overlay_design.py avg/media/vendor/overlay_design.py
touch avg/media/__init__.py avg/media/vendor/__init__.py
git add avg/media && git commit -m "chore: vendor media layer verbatim"
```

Commit the untouched copies **before** editing, so the one intentional change shows up as a reviewable diff.

- [ ] **Step 2: Write the failing test**

```python
# tests/media/test_progress_sink.py
import avg.media.vendor.avg as v

def test_sink_receives_progress(tmp_path):
    seen = []
    v.PROGRESS_SINK = lambda pct, msg: seen.append((pct, msg))
    try:
        v._write_progress(str(tmp_path), 40, "encoding")
    finally:
        v.PROGRESS_SINK = None
    assert seen == [(40, "encoding")]

def test_file_is_still_written(tmp_path):
    import json
    v._write_progress(str(tmp_path), 55, "mixing")
    data = json.loads((tmp_path / "render" / "progress.json").read_text())
    assert data == {"percent": 55, "message": "mixing"}

def test_a_broken_sink_never_fails_the_phase(tmp_path):
    def boom(pct, msg): raise RuntimeError("sink exploded")
    v.PROGRESS_SINK = boom
    try:
        v._write_progress(str(tmp_path), 10, "ok")
    finally:
        v.PROGRESS_SINK = None
```

- [ ] **Step 3: Run it and watch it fail**

Run: `pytest tests/media/test_progress_sink.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'PROGRESS_SINK'`

- [ ] **Step 4: Make the edit**

Add `PROGRESS_SINK = None` just above `def _write_progress`, and inside the function — after the existing file write, still inside its `try` — call the sink under its own `try/except Exception: pass`. Progress reporting must never fail the phase; that rule is already written in the function's docstring and it now covers the sink too.

- [ ] **Step 5: Run it and watch it pass**

Run: `pytest tests/media -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add avg/media tests/media && git commit -m "feat: progress sink hook in vendored media layer"
```

---

### Task 6: MediaEngine child process

**Files:**
- Create: `avg/media/child.py`, `avg/media/engine.py`
- Test: `tests/media/test_engine.py`

**Interfaces:**
- Consumes: `RenderRequest`, `ProgressEvent`, `PhaseFailed`, `RenderFinished`, `ErrorKind` (Task 2); `render_project`, `PROGRESS_SINK` (Task 5)
- Produces:
  - `MediaEngine.render(req: RenderRequest, project_id: str) -> Iterator[ProgressEvent | PhaseFailed | RenderFinished]`
  - `MediaEngine.cancel() -> None`
  - `classify_exit(exitcode: int, saw_result: bool) -> ErrorKind | None` — `None` means success

Exit classification, which is the whole reason for the child process: `render_project` may `sys.exit(1)` on hard errors, so exitcode `1` with no result is `ErrorKind.bug`. A negative exitcode means killed by a signal — on Windows an OOM kill or an external terminate — which is `ErrorKind.resource`. Exitcode `0` with no result is also `bug` (the child returned without producing output).

- [ ] **Step 1: Write the failing test**

```python
# tests/media/test_engine.py
import pytest
from avg.core.errors import ErrorKind
from avg.media.engine import classify_exit

def test_sys_exit_one_is_a_bug():
    assert classify_exit(1, saw_result=False) is ErrorKind.bug

def test_signal_kill_is_a_resource_failure():
    assert classify_exit(-9, saw_result=False) is ErrorKind.resource

def test_clean_exit_with_a_result_is_not_a_failure():
    assert classify_exit(0, saw_result=True) is None

def test_clean_exit_without_a_result_is_a_bug():
    assert classify_exit(0, saw_result=False) is ErrorKind.bug
```

```python
# tests/media/test_engine.py (continued)
from avg.media.engine import MediaEngine
from avg.core.models import RenderRequest
from avg.core.events import ProgressEvent, PhaseFailed

def test_a_crashing_child_becomes_an_event_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_CHILD_CRASH", "1")     # child.py honours this in tests
    events = list(MediaEngine().render(RenderRequest(project_folder=tmp_path), "p1"))
    assert isinstance(events[-1], PhaseFailed)
    assert events[-1].kind in (ErrorKind.bug, ErrorKind.resource)

def test_progress_events_reach_the_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("AVG_CHILD_FAKE_PROGRESS", "1")
    events = list(MediaEngine().render(RenderRequest(project_folder=tmp_path), "p1"))
    assert [e.percent for e in events if isinstance(e, ProgressEvent)] == [0, 50, 100]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/media/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.media.engine'`

- [ ] **Step 3: Implement the child**

`avg/media/child.py` runs in the spawned process. It sets `vendor.avg.PROGRESS_SINK = lambda pct, msg: queue.put(("progress", pct, msg))`, calls `render_project(**req_as_kwargs)`, then puts `("result", output_path, size_bytes)`. It honours `AVG_CHILD_CRASH` (raise immediately) and `AVG_CHILD_FAKE_PROGRESS` (emit 0/50/100 and a result without rendering) so the engine is testable without media. Use `multiprocessing.get_context("spawn")` — Windows has no fork, and spawn is what the packaged exe will use.

- [ ] **Step 4: Implement the engine**

`render()` starts the child, then drains the queue with a timeout in a loop, yielding `ProgressEvent` per progress message. When the child exits, it calls `classify_exit(proc.exitcode, saw_result)`; `None` yields `RenderFinished`, anything else yields `PhaseFailed` with that kind. `cancel()` calls `proc.terminate()`, then `proc.join(5)`, then `proc.kill()` if it is still alive.

- [ ] **Step 5: Run it and watch it pass**

Run: `pytest tests/media -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add avg/media tests/media && git commit -m "feat: MediaEngine child process with progress, cancel and exit classification"
```

---

### Task 7: FastAPI bridge

**Files:**
- Create: `avg/api/app.py`, `avg/api/deps.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: `import_all` (Task 3), `load_render_settings` (Task 4), `MediaEngine` (Task 6)
- Produces:
  - `GET  /api/projects` → `{"projects": [ProjectDoc, ...]}`
  - `POST /api/projects/{project_id}/render` → `202 {"started": true}`
  - `POST /api/projects/{project_id}/cancel` → `{"cancelled": true}`
  - `WS   /ws/events` → a stream of the Task 2 event models as JSON
  - `create_app(token: str, output_root: Path) -> FastAPI`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_app.py
import pytest
from fastapi.testclient import TestClient
from avg.api.app import create_app

@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(token="t0k", output_root=tmp_path))

def test_requests_without_a_token_are_rejected(client):
    assert client.get("/api/projects").status_code == 401

def test_requests_with_the_token_succeed(client):
    r = client.get("/api/projects", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200 and r.json() == {"projects": []}

def test_render_of_an_unknown_project_is_404(client):
    r = client.post("/api/projects/nope/render", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 404

def test_websocket_streams_events(client):
    with client.websocket_connect("/ws/events?token=t0k") as ws:
        assert ws.receive_json()["type"] == "hello"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/api -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.api'`

- [ ] **Step 3: Implement it**

Token check is a FastAPI dependency reading `X-AVG-Token`; the WebSocket takes it as a query parameter because browsers cannot set headers on a WebSocket handshake. Render runs on a background task that pushes each engine event to every connected socket. Projects are imported once at startup and cached in app state — the home grid must never trigger a 62-folder walk per request.

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/api -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add avg/api tests/api && git commit -m "feat: FastAPI bridge with token auth and event socket"
```

---

### Task 8: Native shell

**Files:**
- Create: `avg/shell.py`
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `create_app` (Task 7)
- Produces: `pick_port() -> int`, `make_token() -> str`, `main() -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shell.py
from avg.shell import pick_port, make_token

def test_port_is_free_and_ephemeral():
    p = pick_port()
    assert 1024 < p < 65536

def test_token_is_unguessable_and_unique():
    a, b = make_token(), make_token()
    assert a != b and len(a) >= 32
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_shell.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'avg.shell'`

- [ ] **Step 3: Implement it**

`pick_port` binds `127.0.0.1:0`, reads the assigned port, closes. `make_token` is `secrets.token_urlsafe(32)`. `main()` picks both, starts uvicorn on a daemon thread bound to `127.0.0.1` only, waits for the health endpoint to answer, then opens `webview.create_window("AVG Studio", f"http://127.0.0.1:{port}/?token={token}", width=1280, height=820)`.

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/test_shell.py -v`
Expected: 2 passed

- [ ] **Step 5: Verify by hand**

Run: `python -m avg.shell`
Expected: a window opens showing the FastAPI JSON response. Nothing is reachable from another machine — the bind is loopback-only.

- [ ] **Step 6: Commit**

```bash
git add avg/shell.py tests/test_shell.py && git commit -m "feat: pywebview shell with loopback bind and session token"
```

---

### Task 9: Design tokens and the home grid

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/src/tokens.css`, `web/src/api.ts`, `web/src/Home.tsx`, `web/src/App.tsx`, `web/src/main.tsx`
- Test: `web/src/Home.test.tsx`

**Interfaces:**
- Consumes: `GET /api/projects` (Task 7)
- Produces: `<Home projects={ProjectDoc[]} onOpen={(id: string) => void} />`, `apiGet(path)`, `apiPost(path)`

Tokens come from the approved UI design and are not re-invented here: canvas `#0C0E12`, panel `#13171D`, panel-2 `#1A1F27`, line `#252B34`, text `#E5E9EF`, muted `#868F9C`, accent `#E9A63F`, ok `#57B67C`, bad `#DE5B52`. Interface face is `"Segoe UI Variable Text", "Segoe UI", system-ui`; data face is `"Cascadia Code", Consolas, ui-monospace`. **Amber is reserved for "happening right now" and is never decorative.**

- [ ] **Step 1: Scaffold and write the failing test**

```bash
cd web && npm create vite@latest . -- --template react-ts && npm i && npm i -D vitest @testing-library/react jsdom
```

```tsx
// web/src/Home.test.tsx
import { render, screen } from "@testing-library/react";
import Home from "./Home";

const projects = [
  { id: "p1", title: "Why Roman Concrete Still Stands", aspect_ratio: "9:16",
    phases: { render: { status: "done" } }, master_output: "out.mp4" },
  { id: "p2", title: "The Fastest Ship Ever Built", aspect_ratio: "9:16",
    phases: { director2: { status: "failed" } }, master_output: null },
];

test("shows a card per project", () => {
  render(<Home projects={projects} onOpen={() => {}} />);
  expect(screen.getByText(/Roman Concrete/)).toBeDefined();
  expect(screen.getByText(/Fastest Ship/)).toBeDefined();
});

test("a failed project says which phase failed", () => {
  render(<Home projects={projects} onOpen={() => {}} />);
  expect(screen.getByText(/failed/i).textContent).toMatch(/director2|phase/i);
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && npx vitest run`
Expected: FAIL — cannot resolve `./Home`

- [ ] **Step 3: Implement tokens, the API helper and the grid**

`api.ts` reads the token once from `location.search` and attaches `X-AVG-Token` to every request. `Home.tsx` renders the card grid: aspect badge, status chip using the semantic colours, title, and the actions the state permits — `play` and `run again` when done, `resume` and `open` when failed.

- [ ] **Step 4: Run it and watch it pass**

Run: `cd web && npx vitest run`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add web && git commit -m "feat: design tokens and project grid"
```

---

### Task 10: Studio view and transport bar

**Files:**
- Create: `web/src/Studio.tsx`, `web/src/Transport.tsx`, `web/src/useEvents.ts`
- Modify: `web/src/App.tsx`
- Test: `web/src/Studio.test.tsx`

**Interfaces:**
- Consumes: `WS /ws/events` (Task 7), `<Home>` (Task 9)
- Produces: `useEvents()` returning `{ progress, failure, finished }`; `<Studio project={ProjectDoc} progress={ProgressEvent | null} />`; `<Transport />`

The transport bar renders on **every** screen, not just Studio — that is the point of it. It lives in `App.tsx` below the router outlet.

- [ ] **Step 1: Write the failing test**

```tsx
// web/src/Studio.test.tsx
import { render, screen } from "@testing-library/react";
import Studio from "./Studio";

const project = { id: "p1", title: "Roman Concrete", aspect_ratio: "9:16",
  phases: { brief: {status:"done", duration_ms: 12000}, render: {status:"running"} } };

test("draws a row per phase with its duration", () => {
  render(<Studio project={project} progress={{percent: 38, message: "encoding"}} />);
  expect(screen.getByText("Brief")).toBeDefined();
  expect(screen.getByText("0:12")).toBeDefined();
});

test("shows the live progress message", () => {
  render(<Studio project={project} progress={{percent: 38, message: "encoding"}} />);
  expect(screen.getByText(/encoding/)).toBeDefined();
});

test("a failed phase shows the error and a retry", () => {
  const failed = { ...project, phases: { render: { status: "failed", error: "moviepy died" } } };
  render(<Studio project={failed} progress={null} />);
  expect(screen.getByText(/moviepy died/)).toBeDefined();
  expect(screen.getByRole("button", { name: /run again/i })).toBeDefined();
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && npx vitest run Studio`
Expected: FAIL — cannot resolve `./Studio`

- [ ] **Step 3: Implement it**

`useEvents` opens the WebSocket once with the token as a query param and reduces incoming events into state. Durations format as `m:ss` from `duration_ms`. A `failed` phase renders the message, then the raw detail in the mono face, then the actions — matching the design's promise that you never go log-spelunking.

- [ ] **Step 4: Run it and watch it pass**

Run: `cd web && npx vitest run`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add web && git commit -m "feat: studio view, phase rail and transport bar"
```

---

### Task 10.5: Media serving and the content-forward visual pass

Added 2026-07-25 after the first UI was judged to look like a dark admin
dashboard. The diagnosis was not taste: a video app whose cards show grey
rectangles cannot look like a video app. Approved design:
https://claude.ai/code/artifact/b1b03e92-d328-45f0-a729-2529960d95fa

Runs BEFORE Task 11 (installer) and Task 12 (acceptance).

**Files:**
- Create: `avg/media/thumbnails.py`
- Modify: `avg/api/app.py` (two routes)
- Modify: `web/src/Home.tsx`, `web/src/ProjectCard.tsx`, `web/src/Studio.tsx`, `web/src/App.tsx`, `web/src/tokens.css`
- Create: `web/src/Rail.tsx`, `web/src/Player.tsx`
- Test: `tests/media/test_thumbnails.py`

**Interfaces:**
- Produces: `thumbnail_for(project: ProjectDoc, cache_dir: Path) -> Path | None`
- Produces: `GET /api/projects/{project_id}/thumbnail` -> `image/jpeg`
- Produces: `GET /api/projects/{project_id}/video` -> `video/mp4`, **must honour `Range` and answer 206**, or seeking in a `<video>` element will not work

**Thumbnail rules:**
- Source order: `master_output.mp4` at 35% of duration; if absent, the first
  file in `images/`; if neither, no thumbnail and the UI shows the fallback.
  Mid-pipeline projects have no video, so the image fallback is what stops
  them being blank.
- 400px wide, JPEG quality 6, extracted with ffmpeg.
- **Cached outside the user's project folder** — `%LOCALAPPDATA%/AVG Studio/thumbs/<project_id>.jpg`.
  Nothing is ever written into `D:\AI_Video_Gen`.
- Extraction is lazy (on first request) and cached; a missing or broken video
  yields `None`, never an exception.

**Visual pass — apply the approved design:**
- Cards are the frame: metadata overlays the still, no grey block beneath.
- A render in progress becomes a hero at the top of Home, showing its frame,
  large mono percentage, and the message.
- Restore the 58px icon rail with the amber active marker.
- Radii 14px; surfaces carry a 1px top-edge highlight (`rgba(255,255,255,.05)`).
- Play opens an inline `<video>` player fed by the video route.
- **Amber remains reserved for "happening right now"** — progress fill, live
  phase, pulsing dot. Never a border, focus ring, or decoration.

- [ ] Implement, verify by looking at it with real projects, run the suite once, commit.

---

### Task 11: One installer that packages everything

**REPLACES the original "first-run installer" task, 2026-07-25.** The user's
requirement is a single installer they can carry to another machine and run
easily. That is incompatible with installing dependencies at first run: on
someone else's PC that step needs internet, can hit a proxy, and can fail
halfway leaving a broken app. The packaging spike found the fat bundle is
~63 MB, not the ~400 MB that made a runtime install seem necessary. So
**everything ships inside the installer. There is no pip at runtime, no
private venv, and no per-component retry UI.**

**Files:**
- Create: `tools/fetch_ffmpeg.py`, `tools/avg.spec`, `installer/avg.iss`
- Modify: `tools/build.py`
- Modify: `avg/media/thumbnails.py` and anything else invoking `ffmpeg`/`ffprobe` -
  they must resolve the BUNDLED binary when frozen, not whatever is on PATH

**Already done for you (do not redo):**
- `vendor/ffmpeg/` holds a working 128 MB shared LGPL build: `ffmpeg.exe`,
  `ffprobe.exe` and 7 DLLs. `avdevice-63.dll` is required even though nothing
  captures devices - without it both exes die with `0xC0000135`. `ffplay.exe`
  is deliberately absent. `vendor/` is gitignored; `tools/fetch_ffmpeg.py`
  must reproduce it from
  `https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip`
- Inno Setup 6 is installed at
  `C:\Users\HO\AppData\Local\Programs\Inno Setup 6\ISCC.exe`

**Build shape:**
- **PyInstaller `--onedir`, not `--onefile`.** Onefile re-extracts ~250 MB to
  temp on every launch; onedir starts immediately, and Inno Setup is what
  turns the folder into one downloadable file.
- Required flags, learned in Task 1 - without these the exe builds and dies at
  launch with `No package metadata was found for imageio`:
  `--copy-metadata imageio --copy-metadata imageio_ffmpeg --copy-metadata moviepy --copy-metadata numpy --copy-metadata Pillow`
- Bundle `web/dist` and `vendor/ffmpeg` as data.
- Entry point is `avg/shell.py`'s `main()`.

**Installer shape:**
- Output: **one `AVG-Setup.exe`** in `dist/`.
- **Per-user install to `{localappdata}\Programs\AVG Studio`**, with
  `PrivilegesRequired=lowest`. No UAC prompt, and the app can write beside
  itself without admin.
- Desktop shortcut and Start Menu entry, both to `AVG.exe`.
- Proper uninstaller, appearing in Add/Remove Programs.
- Fixed `AppId` so upgrades replace rather than stack.
- Compression `lzma2/max`.

**Acceptance - this is the point of the task:**
1. `AVG-Setup.exe` exists; record its size.
2. **Install and launch it in Windows Sandbox** (ships with Win 11 Pro - a
   genuinely clean machine with no Python and no ffmpeg). The app must open
   its window and list projects.
3. The desktop shortcut works; uninstall removes the app.
4. Record installed footprint and cold-start time on that clean machine.

A build that works only on this dev box has proved nothing - the whole task is
"works on another machine".

### Task 12: Package it and prove the slice

**Files:**
- Modify: `tools/build.py`
- Create: `tools/compare_render.py`
- Create: `docs/slice-1-acceptance.md`

**Interfaces:**
- Consumes: everything above; the packaging decision from Task 1
- Produces: `dist/AVG.exe`; `compare_render(new: Path, old: Path) -> ComparisonReport`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_render.py
from tools.compare_render import within_tolerance

def test_durations_within_two_tenths_pass():
    assert within_tolerance(52.10, 52.25) is True

def test_a_half_second_drift_fails():
    assert within_tolerance(52.10, 52.70) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_compare_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.compare_render'`

- [ ] **Step 3: Implement the comparison**

`compare_render` runs `ffprobe -v quiet -print_format json -show_format -show_streams` against both files and compares duration (±0.2s), width, height and stream count. It prints a table and exits non-zero on mismatch, so it can gate the build.

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/test_compare_render.py -v`
Expected: 2 passed

- [ ] **Step 5: Build the executable**

```bash
python tools/build.py --target dist
```

Expected: `dist/AVG.exe` exists. Record its size.

- [ ] **Step 6: Run the real acceptance pass**

1. Copy `dist/AVG.exe` into **Windows Sandbox** (it ships with Windows 11 Pro — a genuinely clean machine, no VM to build). Launch it. Setup runs; every component ends green; the app opens.
2. On the dev box, launch it, open a completed project, press run again.
3. Watch progress stream; press cancel mid-render and confirm the child dies and the window survives.
4. Re-run to completion, then:

```bash
python tools/compare_render.py --new "<project>/Previews/master_output.mp4" --old "<project>/master_output.mp4"
```

Expected: durations within 0.2s, identical resolution and stream count.

5. Kill the render child from Task Manager mid-run. Expected: a red phase row reading `resource`, and the app still alive.

Record every result in `docs/slice-1-acceptance.md`, including anything that failed. A slice that "works except for cancel" is a slice that is not done.

- [ ] **Step 7: Commit**

```bash
git add tools docs tests && git commit -m "feat: packaging and slice 1 acceptance harness"
```

---

## Out of scope for Slice 1

Named explicitly so nobody drifts into them: AI text and image providers · the create wizard · settings editing · channel documents · DPAPI secrets (render needs no keys — the cross-language read stays a standalone spike) · the seven non-render phases · light theme · trend discovery · app chat.
