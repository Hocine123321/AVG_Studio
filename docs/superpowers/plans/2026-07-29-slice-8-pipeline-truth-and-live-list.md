# Slice 8 — Pipeline truth, honest progress, and the Live List

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Studio screen tell the truth — the right phase state, an honest status pill, a render that reports itself from the first second — then give every phase a real output view and add a curated Live List of the models and voices worth picking.

**Architecture:** Three layers, in order. First four defect fixes, of which the load-bearing one is structural: a take exists from project creation and the pipeline mutates *that take's* phase records, so there is one truth instead of a snapshot and a live dict. Then the artifact panel, which reads a take's files through one whitelisted table reused from `avg/core/takes.py`. Then the Live List, a curation layer over advertised catalogues, stored apart from the machine-owned probe cache.

**Tech Stack:** Python 3.12 + FastAPI + pydantic, React 19 + Vite + TypeScript in WebView2. No new runtime dependencies.

Spec: `docs/superpowers/specs/2026-07-29-slice-8-pipeline-truth-and-live-list-design.md`

## Global Constraints

- `.venv-build\Scripts\python.exe` is the interpreter for everything. System `python` lacks the dependencies.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1` session-wide; never delete it. Image and voice providers cost real money.
- **`avg/media/vendor/` is protected.** Byte-for-byte copies of the old render engine. Never edit.
- **Nothing below `avg/api/` may print or prompt.** Lower layers emit typed events.
- **Every `SettingSpec` must declare `invalidates`** — required, no default. `None` is a valid declared value.
- Tests must not write to the real `%APPDATA%/AVG Studio` — pass the `create_app` path overrides.
- **One filled terracotta (`#e08a4e`) primary action per screen.** `web/src/tokens.css`'s header is the authority.
- Never add a Google Fonts `@import`. Caprasimo and Figtree are self-hosted.
- Phase order lives only in `avg/pipeline/registry.py`; `web/src/phases.ts` is its one hand-kept mirror.
- Work is **local**. Commit to `main`. No PRs, no pushing.
- Run after every task: `.venv-build/Scripts/python.exe -m pytest tests -q` (**613 passing** at the start of this slice), plus `npm run build && npm run lint` in `web/` for any frontend change.

---

## Corrections to the spec, found while planning

Verified against the code on 2026-07-29. **These override the spec where they differ** — the spec's artifact table was written from a legacy `D:\AI_Video_Gen` project folder, and this app writes different filenames.

| Spec said | Reality | Consequence |
|---|---|---|
| Brief writes `brief.json` + `topic_research.txt` | `avg/pipeline/phases/writing.py:126` writes `script/brief.json` only | No research file to show |
| Script writes `script_draft.txt`, `timeline.json`, `markers.json` | `writing.py:239-241` writes `script/narration.txt` and `script/scenes.json` | The Script body shows narration text and the scene list |
| Voice writes `audio/*.mp3`, one per scene | `voice.py:222,241` writes exactly two files: `audio/raw_voice.mp3` and `audio/optimized_voice.mp3` | Two players, raw vs optimized — **not** per-scene |
| Captions writes `card_plan.json` | `captions.py:438,455` rewrites `render/card_plan.json` **in place, adding timings** and writes `render/captions.ass` | Captions shows the timed cards; Director II shows the untimed plan |
| A new `avg/core/artifacts.py` holds the phase→path table | `avg/core/takes.py:84` **already has `PHASE_ARTIFACTS`**, and `clone_take` depends on it | `artifacts.py` imports that table and adds display metadata. Never a second copy — a divergence would make a cloned take miss a file the panel shows |

One more, which Task 1 must handle: `clone_take` copies from `take_folder(project, source_id)`. A take-1-at-creation project has **no** `takes/1/` folder (phases write in place at the project root), so cloning take 1 would produce an empty take. Task 1 adds the fallback.

---

## File Structure

**New**

| Path | Responsibility |
|---|---|
| `avg/core/artifacts.py` | Display metadata over `takes.PHASE_ARTIFACTS`: artifact key → relative path, label, media type. Resolution of a key against a take. No HTTP, no second path table. |
| `avg/core/model_prefs.py` | `model_prefs.json` read/write and global-vs-channel scope resolution. No network. |
| `avg/providers/catalogues.py` | Listing image models and voices from providers that advertise them. Network, no generation. |
| `web/src/artifacts/PhaseArtifact.tsx` | The panel shell (header strip) and the per-phase body switch. |
| `web/src/artifacts/bodies.tsx` | The seven small body components. |
| `web/src/LiveList.tsx` | The screen: three tabs, filters, tick/favourite/note. |
| `tests/core/test_artifacts.py`, `tests/core/test_model_prefs.py`, `tests/api/test_artifact_routes.py`, `tests/api/test_live_list_routes.py`, `tests/providers/test_catalogues.py` | Their tests. |

**Modified**

| Path | Change |
|---|---|
| `avg/pipeline/create.py` | `new_project` registers take 1 |
| `avg/core/takes.py` | `clone_take` falls back to the project root for a take with no folder |
| `avg/api/app.py` | Pipeline runs against the current take's records; `JobStarted`/`JobStep`; workspace not kept |
| `avg/api/routes_takes.py` | Artifact routes |
| `avg/api/routes_settings.py` | Live List routes |
| `avg/media/environment.py` | Hardlink-then-copy workspace; `keep` from the environment |
| `avg/core/events.py` | `JobStarted`, `JobStep` |
| `avg/core/settings_schema.py` | `live_list_source`; `source` on `image_model`, `voice_id`, `voice_model_id` |
| `web/src/activity.ts`, `StatusBar.tsx`, `useEvents.ts`, `Studio.tsx`, `ModelPicker.tsx`, `AppHealth.tsx`, `App.tsx`, `api.ts`, `tokens.css` | As each task describes |

---

## Task 1: Take 1 exists from creation, and the run writes into it

The load-bearing fix. Everything else in Part 1 is cosmetic next to this.

**Files:**
- Modify: `avg/pipeline/create.py` (`new_project`)
- Modify: `avg/core/takes.py` (`clone_take` source-folder fallback)
- Modify: `avg/api/app.py` (`POST /api/projects` worker)
- Test: `tests/pipeline/test_create.py`, `tests/core/test_takes.py`, `tests/api/test_app.py`

**Interfaces:**
- Consumes: `avg.core.takes.Take`, `take_folder`, `clone_take`; `avg.pipeline.runner.run`.
- Produces: `new_project()` returns a `ProjectDoc` with `takes == [Take(id=1, ...)]` and `current_take_id == 1`, where `doc.takes[0].phases is doc.phases` (the same dict object, shared by reference).

- [ ] **Step 1: Write the failing tests**

Add to `tests/pipeline/test_create.py`:

```python
def test_a_new_project_has_take_1_from_the_start(tmp_path):
    """The take is not created lazily on first read.

    It used to be: `GET /takes` called `migrate_to_takes`, which COPIED
    `project.phases` into the take as a snapshot. The runner then went on
    mutating `project.phases` - a different dict - so a first run's rail
    froze at whatever was true when the screen was opened.
    """
    from avg.config.channels import Channel

    channel = Channel(id="c", label="C", settings={})
    doc = new_project("A topic", channel, tmp_path)

    assert [t.id for t in doc.takes] == [1]
    assert doc.current_take_id == 1


def test_take_1_shares_the_phases_dict_rather_than_copying_it(tmp_path):
    """Same object, not an equal one. This is what makes the runner's
    in-place mutation visible through the take the UI reads."""
    from avg.config.channels import Channel

    doc = new_project("A topic", Channel(id="c", label="C", settings={}), tmp_path)
    doc.phases["brief"] = PhaseRecord(status="done", duration_ms=10)

    assert doc.takes[0].phases["brief"].status == "done"
```

Add `from avg.core.models import PhaseRecord` to that file's imports if it is not already there.

Add to `tests/core/test_takes.py`:

```python
def test_cloning_take_1_reads_the_project_root_when_it_has_no_folder(tmp_path):
    """Take 1's artifacts live at the project root.

    Phases write in place (`ctx.project.folder`), so a project created with
    take 1 has no `takes/1/` folder at all. Cloning it must still find the
    files - otherwise every 'new take' from a first run starts empty, which
    is the exact failure `migrate_to_takes`' double-storage was kept to
    avoid.
    """
    folder = tmp_path / "proj"
    (folder / "script").mkdir(parents=True)
    (folder / "script" / "brief.json").write_text('{"a": 1}', encoding="utf-8")

    project = ProjectDoc(
        id="p", title="P", folder=folder, created=datetime.now(timezone.utc),
        phases={"brief": PhaseRecord(status="done")},
        takes=[Take(id=1, created=datetime.now(timezone.utc), phases={"brief": PhaseRecord(status="done")})],
        current_take_id=1,
    )

    take2 = clone_take(project, 1, ["brief"])

    assert (take_folder(project, take2.id) / "script" / "brief.json").is_file()
```

Add to `tests/api/test_app.py`:

```python
def test_a_pipeline_run_advances_the_current_takes_records(tmp_path, monkeypatch):
    """The regression test for the frozen phase rail.

    Runs one fake phase and asserts the CURRENT TAKE - what Studio reads -
    saw it finish, not just `project.phases`.
    """
    import avg.api.app as app_module

    def fake_run(project, **kwargs):
        project.phases["brief"] = PhaseRecord(status="done", duration_ms=5)
        kwargs["on_phase_change"]()
        return iter(())

    monkeypatch.setattr(app_module, "run_pipeline", fake_run)

    client = TestClient(create_app(token="t0k", output_root=tmp_path, thumb_cache_dir=tmp_path / "t"))
    created = client.post("/api/projects", headers={"X-AVG-Token": "t0k"}, json={"topic": "T"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    thread = client.app.state.render_thread
    if thread is not None:
        thread.join(timeout=5)

    takes = client.get(f"/api/projects/{project_id}/takes", headers={"X-AVG-Token": "t0k"}).json()
    current = next(t for t in takes["takes"] if t["id"] == takes["current_take_id"])
    assert current["phases"]["brief"]["status"] == "done"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_create.py tests/core/test_takes.py tests/api/test_app.py -q -k "take_1 or current_takes or no_folder"`

Expected: FAIL — `assert [] == [1]` on the first, and a missing `brief.json` on the clone test.

- [ ] **Step 3: Register take 1 in `new_project`**

In `avg/pipeline/create.py`, replace the `doc = ProjectDoc(...)` / `save_project(doc)` block:

```python
    # `phases` is created once and handed to BOTH the document and take 1,
    # by reference. The runner mutates `project.phases[key]` in place, and
    # the UI reads the CURRENT TAKE's records - if these were two dicts,
    # a first run would advance one of them and display the other. That was
    # the frozen phase rail: `migrate_to_takes` snapshotted `project.phases`
    # into take 1 lazily, on the first `GET /takes`, and the run carried on
    # mutating the original.
    phases: dict[str, PhaseRecord] = {}
    doc = ProjectDoc(
        id=folder_name,
        title=topic,
        folder=folder,
        aspect_ratio=aspect_ratio,
        created=now,
        phases=phases,
        takes=[Take(id=1, created=now, phases=phases)],
        current_take_id=1,
    )
    save_project(doc)
    return doc
```

Add to that file's imports:

```python
from avg.core.models import PhaseRecord, Take
```

(`ProjectDoc` is already imported; extend the existing line rather than adding a second one.)

- [ ] **Step 4: Give `clone_take` the project-root fallback**

In `avg/core/takes.py`, inside `clone_take`, replace the `source_folder = take_folder(project, source_take_id)` line:

```python
    # Take 1 of a project created after Slice 8 has NO folder of its own:
    # every phase writes in place, at the project root (see
    # `avg.pipeline.phases._common.script_dir`), and nothing copies those
    # files into `takes/1/`. Falling back to the root is what keeps a
    # "new take" from that first run non-empty - the same failure mode
    # `migrate_to_takes`' deliberate double-storage exists to avoid for
    # legacy projects.
    source_folder = take_folder(project, source_take_id)
    if not source_folder.is_dir():
        source_folder = Path(project.folder)
```

- [ ] **Step 5: Run the pipeline against the current take**

In `avg/api/app.py`'s `POST /api/projects` worker, `project` is already the object whose `phases` take 1 shares, so **no shadow project is needed** — but the take's `master_output` and status must be kept in step when the run ends. After the `for event in run_pipeline(...)` loop, inside the same `finally`, add:

```python
            finally:
                # Take 1 shares `project.phases` by reference, so its phase
                # records are already current. `master_output` is not shared
                # (it is a value, not a dict), so it is copied across here -
                # a take that finished a render must own its output path or
                # the take strip shows a finished take with nothing to play.
                take = next((t for t in project.takes if t.id == project.current_take_id), None)
                if take is not None:
                    take.master_output = project.master_output
                save_project(project)
                app.state.render_thread = None
                app.state.rendering_project_id = None
                app.state.pipeline_cancel_token = None
```

- [ ] **Step 6: Run the three tests, then the whole suite**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q`

Expected: all pass. Tests that asserted a fresh project has no takes will fail here — **read each one before changing it**. A test named for take migration is asserting something worth keeping; a test that merely happened to build a project and check `takes == []` is asserting the old lazy behaviour and should now expect take 1.

- [ ] **Step 7: Commit**

```bash
git add avg/pipeline/create.py avg/core/takes.py avg/api/app.py tests/
git commit -m "Give a project take 1 at creation, sharing its phase records

The phase rail froze mid-run on every new project. GET /takes migrated a
project into take 1 lazily, copying project.phases as a SNAPSHOT, and the
runner then carried on mutating the original dict. Studio reads the take,
so it rendered whatever was true when the screen was opened - Script stuck
unfinished while Director II and Captions were done and the render was at
35%.

Take 1 now exists from new_project and shares the phases dict by
reference, so there is one set of records rather than a live one and a
displayed one. migrate_to_takes is untouched: it is the path for legacy
imports and has to stay lazy and idempotent for them.

clone_take gains a fallback to the project root, because take 1 of a
new-style project has no folder of its own - phases write in place - and
cloning it would otherwise produce an empty take."
```

---

## Task 2: Live phase state accumulates on the client

Belt-and-braces with Task 1: correct between refetches, and across a socket reconnect.

**Files:**
- Modify: `web/src/useEvents.ts`
- Modify: `web/src/Studio.tsx`

**Interfaces:**
- Produces: `EventsState.phases: Record<string, Record<string, LivePhase>>`, keyed project id then phase key.

- [ ] **Step 1: Add the map to the reducer**

In `web/src/useEvents.ts`, extend `EventsState`:

```ts
  /**
   * Every phase transition seen for each project this session, folded
   * forward.
   *
   * The single-event fields above are deliberately last-write-wins - they
   * answer "what just happened". This answers "where has this run got to",
   * which they cannot: every branch of `reduce` returns
   * `{...INITIAL_STATE, <one field>}`, so a `phase_finished` is erased by
   * the next `phase_started` and finished phases never accumulated
   * anywhere on the client.
   */
  phases: Record<string, Record<string, LivePhase>>;
```

Add `phases: {}` to `INITIAL_STATE`.

Then, in `reduce`, fold the map before the switch and carry it through every branch:

```ts
function foldPhases(
  previous: EventsState["phases"],
  payload: { type: string } & Record<string, unknown>,
): EventsState["phases"] {
  const id = typeof payload.project_id === "string" ? payload.project_id : null;
  const phase = typeof payload.phase === "string" ? payload.phase : null;
  if (id === null) return previous;

  const forProject = { ...(previous[id] ?? {}) };
  switch (payload.type) {
    case "phase_started":
      if (phase) forProject[phase] = { status: "running" };
      break;
    case "phase_finished":
      if (phase) {
        forProject[phase] = {
          status: "done",
          duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        };
      }
      break;
    case "phase_failed":
      if (phase) {
        forProject[phase] = {
          status: "failed",
          error: typeof payload.message === "string" ? payload.message : null,
        };
      }
      break;
    case "pipeline_cancelled":
      // A cancel reverts to pending on both sides - the backend never
      // invents a "cancelled" phase status either.
      if (phase) forProject[phase] = { status: "pending" };
      break;
    case "render_finished":
      forProject.render = { status: "done" };
      break;
    case "render_cancelled":
      forProject.render = { status: "pending" };
      break;
    default:
      return previous;
  }
  return { ...previous, [id]: forProject };
}
```

and in `reduce`, compute it once and spread it into every returned state:

```ts
  const phases = foldPhases(previous.phases, payload);
  switch (payload.type) {
    case "phase_started":
      return { ...INITIAL_STATE, phases, connected: true, phaseStarted: payload as unknown as PhaseStartedEvent };
```

…and the same `phases,` addition on every other branch, including `default`.

- [ ] **Step 2: Have the rail read it**

In `web/src/Studio.tsx`, `Studio` gains a prop:

```ts
  /** Live phase state for THIS project, folded from the event socket.
   * Overlays the persisted records so the rail is right between refetches
   * and across a reconnect. */
  livePhases?: Record<string, LivePhase>;
```

and merges it where `phasesForSelected` is computed, but **only for the current take** — a historical take must never inherit the running take's live state:

```ts
  const persisted: Record<string, PhaseRecord> = selectedTake?.phases ?? project.phases;
  const phasesForSelected: Record<string, PhaseRecord> = isCurrentTake
    ? { ...persisted, ...(livePhases ?? {}) }
    : persisted;
```

Import `LivePhase` from `./useEvents`.

In `web/src/App.tsx`, pass it:

```tsx
          livePhases={events.phases[project.id]}
```

- [ ] **Step 3: Build, lint, and watch a real run**

Run: `npm --prefix web run build && npm --prefix web run lint`

Then restart `tools/_dev_serve.py`, create a video, and watch the rail fill in as each phase finishes — without a refresh, and with the row for the finished phase staying done when the next one starts.

- [ ] **Step 4: Commit**

```bash
git add web/src/useEvents.ts web/src/Studio.tsx web/src/App.tsx
git commit -m "Accumulate live phase state instead of keeping one event

Every branch of useEvents' reducer returned {...INITIAL_STATE, <one
field>}, so a phase_finished was erased by the next phase_started and
finished phases never accumulated on the client at all. The rail was
correct only for whichever transition happened most recently.

Events now fold forward into a per-project phase map, which the rail
overlays on the persisted records - and only for the current take, since
a historical take must not inherit the running one's state."
```

---

## Task 3: A job reports itself from the first second

**Files:**
- Modify: `avg/core/events.py`
- Modify: `avg/media/environment.py` (`render_workspace`)
- Modify: `avg/api/app.py` (both job workers)
- Test: `tests/api/test_app.py`, `tests/media/test_environment.py`

**Interfaces:**
- Produces: `JobStarted(project_id: str, kind: Literal["render", "pipeline"])`, `JobStep(project_id: str, step: str, detail: str = "")`, both `Event` subclasses with `type` literals `"job_started"` / `"job_step"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_app.py`:

```python
def test_job_started_is_the_first_event_a_render_emits(tmp_path, monkeypatch):
    """A render was silent until the encoder's first progress line.

    The workspace copy happens first and takes as long as the project is
    big, so the app looked hung for minutes. The job now announces itself
    before a single file is touched.
    """
    import avg.api.app as app_module

    project_id = "p-jobstart"
    output_root = tmp_path / "output"
    _make_legacy_project_with_video(output_root, project_id)

    seen: list[str] = []
    client = TestClient(create_app(token="t0k", output_root=output_root, thumb_cache_dir=tmp_path / "t"))

    with client.websocket_connect("/ws/events?token=t0k") as ws:
        assert ws.receive_json()["type"] == "hello"
        assert client.post(f"/api/projects/{project_id}/render", headers={"X-AVG-Token": "t0k"}).status_code == 200
        for _ in range(2):
            seen.append(ws.receive_json()["type"])

    assert seen[0] == "job_started"
```

Add to `tests/media/test_environment.py` (create the file if it does not exist, with `from avg.media.environment import render_workspace`):

```python
def test_the_workspace_is_removed_by_default(tmp_path):
    """Every render used to leave a full copy of the project in %TEMP%,
    forever - render_workspace was called with keep=True."""
    project = tmp_path / "proj"
    (project / "images").mkdir(parents=True)
    (project / "images" / "a.png").write_bytes(b"x")

    with render_workspace(project) as workspace:
        assert (workspace / "images" / "a.png").is_file()
        held = workspace

    assert not held.exists()


def test_inputs_are_hardlinked_when_the_filesystem_allows_it(tmp_path):
    """A 'disposable copy' that copies gigabytes is why a render is silent
    for minutes. A hardlink keeps the property that matters - the workspace
    starts with no master_output.mp4 - at no copy cost."""
    project = tmp_path / "proj"
    (project / "images").mkdir(parents=True)
    source = project / "images" / "a.png"
    source.write_bytes(b"x")

    with render_workspace(project) as workspace:
        linked = workspace / "images" / "a.png"
        assert linked.stat().st_nlink >= 2 or linked.stat().st_ino == source.stat().st_ino
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv-build/Scripts/python.exe -m pytest tests/api/test_app.py tests/media/test_environment.py -q -k "job_started or workspace or hardlink"`

Expected: FAIL — the first event is `progress`, and the workspace still exists after the block.

- [ ] **Step 3: Add the events**

In `avg/core/events.py`, alongside the existing models:

```python
class JobStarted(BaseModel):
    """Emitted the instant a job's thread starts, before any work.

    Without it the first thing a client hears about a render is the
    encoder's first progress line - which is minutes in on a big project,
    because the workspace has to be prepared first. The status pill's
    "a job is running" state begins here.
    """

    type: Literal["job_started"] = "job_started"
    project_id: str
    kind: Literal["render", "pipeline"]


class JobStep(BaseModel):
    """A coarse named step for work that cannot report a percentage.

    Preparing the render workspace is the only one today. Deliberately not
    a progress event: a fake percentage on work with no measurable
    denominator is worse than an honest name.
    """

    type: Literal["job_step"] = "job_step"
    project_id: str
    step: str
    detail: str = ""
```

Add both to the `Event` union in that file.

- [ ] **Step 4: Hardlink the workspace and stop keeping it**

In `avg/media/environment.py`, replace `render_workspace`'s body:

```python
@contextmanager
def render_workspace(project_folder: Path, keep: bool | None = None) -> Iterator[Path]:
    """Yield a disposable copy of `project_folder` for a render to write into.

    The copy carries only what the render reads. `master_output.mp4` is
    deliberately NOT copied: the workspace starts without one, so a file
    appearing there is unambiguously this render's output rather than a
    leftover that would make a failed render look successful.

    Inputs are HARDLINKED where the filesystem allows it and copied only
    where it does not. A real project is images and audio measured in
    hundreds of megabytes, and copying them was the reason a render sat
    silent for minutes before the child process even started. A hardlink
    is the same bytes under a second name, so the no-master_output
    property above is untouched - nothing in the render writes over an
    input in place.

    `keep=None` reads `AVG_KEEP_RENDER_WORKSPACE`. It used to be passed
    `True` unconditionally by the render route, which left a full copy of
    every project ever rendered in %TEMP% permanently.
    """
    if keep is None:
        keep = os.environ.get("AVG_KEEP_RENDER_WORKSPACE", "").strip().lower() in ("1", "true", "yes", "on")
    workspace = Path(tempfile.mkdtemp(prefix="avg-render-"))
    try:
        for name in _INPUT_DIRS:
            source = project_folder / name
            if source.is_dir():
                _link_tree(source, workspace / name)
        for name in _INPUT_FILES:
            source = project_folder / name
            if source.is_file():
                _link_file(source, workspace / name)
        yield workspace
    finally:
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)


def _link_file(source: Path, dest: Path) -> None:
    """Hardlink `source` to `dest`, copying if the link is refused.

    A link fails across volumes, on FAT32, and on some network shares -
    all ordinary situations, none of them an error worth surfacing.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def _link_tree(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        target = dest / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            _link_file(item, target)
```

Add `import os` at the top of that file if it is not already imported.

- [ ] **Step 5: Emit the events from both workers**

In `avg/api/app.py`'s `start_render`, replace the `_worker` body's opening:

```python
        def _worker() -> None:
            try:
                _emit(JobStarted(project_id=project_id, kind="render"))
                _emit(JobStep(project_id=project_id, step="preparing", detail="Preparing the render workspace"))
                # Renders run against a disposable COPY of the project, never
                # the folder itself. render_project writes master_output.mp4
                # into the folder it is given, so rendering in place would
                # overwrite the finished video the user already has.
                with render_workspace(req.project_folder) as workspace:
                    _run_render(req.model_copy(update={"project_folder": workspace}))
            finally:
                app.state.render_thread = None
                app.state.rendering_project_id = None
```

with `_emit` defined just above `_worker`, reusing the existing dispatch path rather than a second mechanism:

```python
        def _emit(event: Any) -> None:
            # Broadcasts only - deliberately NOT through `_dispatch`, which
            # also calls `_apply_terminal_event`. That function creates a
            # `render` PhaseRecord for any event that is not a
            # ProgressEvent (`avg/api/app.py:259-265`), so routing a
            # JobStarted through it would invent a render record on a
            # project that has not rendered anything.
            with suppress(Exception):
                fut = asyncio.run_coroutine_threadsafe(
                    app.state.manager.broadcast(event.model_dump(mode="json")), loop
                )
                fut.result()
```

Do the same in the `POST /api/projects` worker, with `kind="pipeline"` and no `preparing` step (a pipeline starts working immediately).

Import both events at the top of `avg/api/app.py`:

```python
from avg.core.events import JobStarted, JobStep, PhaseFailed, ProgressEvent, RenderCancelled, RenderFinished
```

- [ ] **Step 6: Run the suite**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q`

Expected: all pass. If a test counted the events on the socket, it now sees two more; update the count, do not weaken the assertion.

- [ ] **Step 7: Check what the old behaviour left behind**

Run: `ls "$TEMP" | grep avg-render | head`

Report the count in the commit message — this is how much disk the `keep=True` bug had already consumed. Delete them.

- [ ] **Step 8: Commit**

```bash
git add avg/core/events.py avg/media/environment.py avg/api/app.py tests/
git commit -m "Announce a job before it starts, and stop copying the project

A render was silent until the encoder's first progress line, which on a
real project is minutes: the workspace copy runs first, and nothing told
anyone it had started. Both job kinds now emit JobStarted the instant the
thread starts, and the render emits a named 'preparing' step for the
workspace - a coarse name, not a fabricated percentage on work with no
denominator.

The workspace itself now hardlinks its inputs and only copies where the
filesystem refuses a link, so the disposable copy is free while keeping
the property it exists for: no master_output.mp4 in the workspace, so a
file appearing there is unambiguously this render's output.

It also stops being kept. render_workspace was called with keep=True, so
every render ever run left a full copy of the project in %TEMP%
permanently. Debugging keeps its escape hatch through
AVG_KEEP_RENDER_WORKSPACE."
```

---

## Task 4: The status bar gets priorities

**Files:**
- Modify: `web/src/activity.ts`
- Modify: `web/src/StatusBar.tsx`
- Modify: `web/src/App.tsx` (start/end the job activity from events)

**Interfaces:**
- Produces: `beginActivity(label: string, priority?: Priority)` where `Priority = "high" | "medium" | "low"`, defaulting to `"low"`; `Activity` gains `priority`.

- [ ] **Step 1: Add priorities to the store**

In `web/src/activity.ts`:

```ts
/**
 * How much the bar should care.
 *
 * `high` is a running job - a render or a pipeline. It is registered from
 * the event socket, not from an HTTP call, because the call that STARTS a
 * job returns 202 in milliseconds and its activity ends before anyone can
 * read it: that is why the pill said "Ready!" through an entire render.
 *
 * The pill shows the highest priority present, oldest first within it. A
 * lower priority can never replace a higher one, which is what stops the
 * flicker - two concurrent reads used to swap the label as each resolved.
 */
export type Priority = "high" | "medium" | "low";

export interface Activity {
  id: number;
  label: string;
  startedAt: number;
  priority: Priority;
}

export function beginActivity(label: string, priority: Priority = "low"): () => void {
  const id = nextId++;
  active.set(id, { id, label, startedAt: Date.now(), priority });
  emit();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    active.delete(id);
    emit();
  };
}

const RANK: Record<Priority, number> = { high: 0, medium: 1, low: 2 };

/** The activity the bar should name: highest priority, oldest within it. */
export function leadActivity(activities: Activity[]): Activity | null {
  return activities.reduce<Activity | null>((lead, a) => {
    if (lead === null) return a;
    if (RANK[a.priority] !== RANK[lead.priority]) return RANK[a.priority] < RANK[lead.priority] ? a : lead;
    return a.startedAt < lead.startedAt ? a : lead;
  }, null);
}
```

and give writes their own priority in `labelForRequest`'s caller — in `web/src/api.ts`:

```ts
  const endActivity = beginActivity(labelForRequest(method, path), method === "GET" ? "low" : "medium");
```

- [ ] **Step 2: Render the lead, and keep the job label short**

In `web/src/StatusBar.tsx`, replace the `oldest` computation and the label:

```tsx
  const lead = leadActivity(activities);
  const kind = !connected ? "error" : lead !== null ? "busy" : "ready";
  const label = kind === "error" ? "Reconnecting" : lead !== null ? lead.label : "Ready!";
```

`lead.label` for a job is the word `Working` and nothing else — percent and phase belong to the Transport bar, and the same number rendered twice at two update rates is how a UI starts contradicting itself.

Replace every other use of `oldest` in that file with `lead`.

- [ ] **Step 3: Start and end the job activity from events**

In `web/src/App.tsx`, inside the component:

```tsx
  // The job's own activity: begins on job_started, ends on any terminal
  // event. Registered here rather than in api.ts because a job is not a
  // request - `POST /render` is over in milliseconds while the job it
  // started runs for minutes.
  const jobActivity = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (events.jobStarted && jobActivity.current === null) {
      jobActivity.current = beginActivity("Working", "high");
    }
    if ((events.finished || events.cancelled || events.failure || events.pipelineCancelled) && jobActivity.current) {
      jobActivity.current();
      jobActivity.current = null;
    }
  }, [events.jobStarted, events.finished, events.cancelled, events.failure, events.pipelineCancelled]);
```

This needs `useEvents` to surface the new event: add `jobStarted: JobStartedEvent | null` and `jobStep: JobStepEvent | null` to `EventsState`, their interfaces, and reducer branches, exactly like the existing ones (`{...INITIAL_STATE, phases, connected: true, jobStarted: payload}`).

- [ ] **Step 4: Build, lint, watch it**

Run: `npm --prefix web run build && npm --prefix web run lint`

Then, against the dev server: press render and confirm the pill reads `Working` with a clock inside a second, keeps reading it while other screens load in the background, and returns to `Ready!` when the render ends.

- [ ] **Step 5: Commit**

```bash
git add web/src/activity.ts web/src/StatusBar.tsx web/src/App.tsx web/src/api.ts web/src/useEvents.ts
git commit -m "Give the status pill priorities and let it see a job

The pill tracked HTTP call lifetimes, and POST /render returns 202 in
milliseconds - so the activity ended before it could be read and the pill
said 'Ready!' through an entire render. It now registers a high-priority
activity from the job's own JobStarted event and ends it on the job's
terminal event.

Priorities also kill the flicker: the bar shows the highest priority
present, oldest first within it, so a background read can no longer
replace the label of something more important that is still running.

The job label stays the single word Working plus its clock. Percent and
phase belong to the Transport bar - the same number in two places at two
update rates is how a UI starts contradicting itself."
```

---

## Task 5: "Re-build model cache"

**Files:**
- Modify: `web/src/ModelCacheCard.tsx`

- [ ] **Step 1: Make the label conditional**

```tsx
        <button type="button" className="btn" onClick={() => void build()} disabled={building}>
          {building ? "Probing…" : cache?.updated ? "Re-build model cache" : "Build model cache"}
        </button>
```

- [ ] **Step 2: Build, lint, commit**

```bash
npm --prefix web run build && npm --prefix web run lint
git add web/src/ModelCacheCard.tsx
git commit -m "Say Re-build once the model cache has been built

A button that says Build next to a timestamp reading 'Last checked
14:02' is asking whether the first build ever happened."
```

---

## Task 6: Serving a take's artifacts

**Files:**
- Create: `avg/core/artifacts.py`
- Modify: `avg/api/routes_takes.py`
- Test: `tests/core/test_artifacts.py`, `tests/api/test_artifact_routes.py`

**Interfaces:**
- Produces:
  - `ARTIFACTS: dict[str, tuple[ArtifactSpec, ...]]` keyed by phase key
  - `@dataclass(frozen=True) ArtifactSpec(key: str, rel_path: str, label: str, media_type: str, kind: Literal["json", "text", "audio", "image_dir", "video"])`
  - `resolve(project: ProjectDoc, take_id: int, key: str) -> Path | None`
  - `available(project: ProjectDoc, take_id: int) -> dict[str, int]` — artifact key → size in bytes, for what exists
  - Routes `GET /api/projects/{id}/takes/{take_id}/artifacts` and `GET /api/projects/{id}/takes/{take_id}/artifact/{key}` (plus `/{key}/{name}` for the image directory)

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_artifacts.py`:

```python
"""Tests for artifact resolution.

Two properties are load-bearing. A key outside the table must never reach
the filesystem - this is the surface that serves files from a user's disk
to a browser. And the table must not drift from `takes.PHASE_ARTIFACTS`,
because that is what `clone_take` copies: an artifact the panel shows but
the clone does not copy is a take that silently loses a file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from avg.core.artifacts import ARTIFACTS, available, resolve
from avg.core.models import PhaseRecord, ProjectDoc, Take
from avg.core.takes import PHASE_ARTIFACTS


def _project(folder) -> ProjectDoc:
    return ProjectDoc(
        id="p", title="P", folder=folder, created=datetime.now(timezone.utc),
        phases={"brief": PhaseRecord(status="done")},
        takes=[Take(id=1, created=datetime.now(timezone.utc), phases={})],
        current_take_id=1,
    )


def test_an_unknown_key_never_touches_the_filesystem(tmp_path):
    project = _project(tmp_path)
    assert resolve(project, 1, "../../etc/passwd") is None
    assert resolve(project, 1, "script/brief.json") is None  # a path is not a key
    assert resolve(project, 1, "nonsense") is None


def test_a_known_key_resolves_under_the_project_root_for_take_1(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text("{}", encoding="utf-8")

    path = resolve(project, 1, "brief")
    assert path is not None and path.is_file()


def test_a_take_with_its_own_folder_wins(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text('{"root": true}', encoding="utf-8")
    take_dir = tmp_path / "takes" / "2" / "script"
    take_dir.mkdir(parents=True)
    (take_dir / "brief.json").write_text('{"take": true}', encoding="utf-8")
    project.takes.append(Take(id=2, created=datetime.now(timezone.utc), phases={}))

    path = resolve(project, 2, "brief")
    assert path is not None and "take" in path.read_text(encoding="utf-8")


def test_available_reports_only_what_is_on_disk(tmp_path):
    project = _project(tmp_path)
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "brief.json").write_text("{}", encoding="utf-8")

    found = available(project, 1)
    assert "brief" in found
    assert "narration" not in found


def test_every_artifact_path_is_one_clone_take_copies():
    """Drift here is a take that loses a file on clone.

    `clone_take` copies `PHASE_ARTIFACTS`; this table is what the UI
    offers. An entry here with no counterpart there would show a file in
    take 1 that vanishes in take 2.
    """
    for phase, specs in ARTIFACTS.items():
        copied = set(PHASE_ARTIFACTS.get(phase, ()))
        for spec in specs:
            assert spec.rel_path in copied or spec.rel_path.split("/")[0] in copied, (
                f"{phase}/{spec.key} ({spec.rel_path}) is not copied by clone_take"
            )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_artifacts.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'avg.core.artifacts'`.

- [ ] **Step 3: Write the module**

Create `avg/core/artifacts.py`:

```python
"""What each phase leaves on disk, and how to hand one file to the UI.

The Studio panel showed the word "Done." for six of the eight phases while
every one of them had written a real file. This is the table that fixes
that: artifact key -> relative path, label, and how to render it.

**Paths come from `avg.core.takes.PHASE_ARTIFACTS`, not from a second
table.** That map is what `clone_take` copies into a new take; a display
table that drifted from it would show a file in one take that silently
vanishes in the next. A test asserts every entry here is one that gets
copied.

**A key is never a path.** The route takes `key`, looks it up here, and a
miss is a 404 before any filesystem call. This is the surface that serves
files off a user's disk to a browser, so traversal is prevented by there
being nothing to traverse rather than by sanitising a caller's string.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from avg.core.models import ProjectDoc
from avg.core.takes import take_folder


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    key: str
    rel_path: str
    label: str
    media_type: str
    #: How the panel renders it. `image_dir` is a directory listing rather
    #: than a single file; everything else is one file.
    kind: Literal["json", "text", "audio", "image_dir", "video"]


ARTIFACTS: dict[str, tuple[ArtifactSpec, ...]] = {
    "brief": (
        ArtifactSpec("brief", "script/brief.json", "Brief", "application/json", "json"),
    ),
    "script": (
        ArtifactSpec("narration", "script/narration.txt", "Narration", "text/plain", "text"),
        ArtifactSpec("scenes", "script/scenes.json", "Scenes", "application/json", "json"),
    ),
    "director1": (
        ArtifactSpec("video_map", "script/video_map.json", "Image prompts", "application/json", "json"),
    ),
    "visuals": (
        ArtifactSpec("images", "images", "Images", "image/png", "image_dir"),
    ),
    "voice": (
        ArtifactSpec("raw_voice", "audio/raw_voice.mp3", "Raw narration", "audio/mpeg", "audio"),
        ArtifactSpec("optimized_voice", "audio/optimized_voice.mp3", "Optimized narration", "audio/mpeg", "audio"),
    ),
    "director2": (
        ArtifactSpec("direction_plan", "render/direction_plan.json", "Motion plan", "application/json", "json"),
        ArtifactSpec("card_plan", "render/card_plan.json", "Caption cards", "application/json", "json"),
    ),
    "captions": (
        ArtifactSpec("captions", "render/captions.ass", "Subtitle file", "text/plain", "text"),
    ),
}

_BY_KEY: dict[str, ArtifactSpec] = {
    spec.key: spec for specs in ARTIFACTS.values() for spec in specs
}


def spec_for(key: str) -> ArtifactSpec | None:
    return _BY_KEY.get(key)


def _roots(project: ProjectDoc, take_id: int) -> list[Path]:
    """Where a take's files may be, most specific first.

    A cloned take owns `takes/<id>/`. Take 1 of a project created after
    Slice 8 has no folder at all - phases write in place at the project
    root - so the root is the fallback, exactly as `clone_take` treats it.
    """
    own = take_folder(project, take_id)
    roots = [own] if own.is_dir() else []
    roots.append(Path(project.folder))
    return roots


def resolve(project: ProjectDoc, take_id: int, key: str) -> Path | None:
    """The file for `key` in `take_id`, or `None`.

    `None` covers every failure the same way: unknown key, no such take,
    nothing on disk. The caller turns it into one 404 - which of the three
    it was is not something a file-serving route should tell a client.
    """
    spec = _BY_KEY.get(key)
    if spec is None:
        return None
    if not any(t.id == take_id for t in project.takes):
        return None
    for root in _roots(project, take_id):
        candidate = root / spec.rel_path
        if candidate.exists():
            return candidate
    return None


def available(project: ProjectDoc, take_id: int) -> dict[str, int]:
    """Artifact key -> size in bytes, for the ones that exist.

    A directory reports the number of files it holds rather than a byte
    count; the panel uses it for "11 images", and summing a folder here
    would cost a stat per file for a number nothing displays.
    """
    found: dict[str, int] = {}
    for specs in ARTIFACTS.values():
        for spec in specs:
            path = resolve(project, take_id, spec.key)
            if path is None:
                continue
            if path.is_dir():
                found[spec.key] = sum(1 for f in path.iterdir() if f.is_file())
            else:
                found[spec.key] = path.stat().st_size
    return found
```

- [ ] **Step 4: Run the module tests**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_artifacts.py -q`

Expected: `5 passed`. If the drift test fails, **fix the table, not the test** — a path that `clone_take` does not copy is a real gap in one of the two.

- [ ] **Step 5: Write the failing route tests**

Create `tests/api/test_artifact_routes.py`:

```python
"""Tests for the take-scoped artifact routes.

These serve files from a user's disk, so the test that matters most is the
one proving a caller cannot ask for a file the table does not name.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from avg.api.app import create_app
from avg.pipeline.create import save_project
from avg.core.models import PhaseRecord, ProjectDoc, Take

TOKEN = "t0k"
AUTH = {"X-AVG-Token": TOKEN}


@pytest.fixture
def client_with_project(tmp_path):
    output_root = tmp_path / "output"
    folder = output_root / "proj-artifacts"
    (folder / "script").mkdir(parents=True)
    (folder / "audio").mkdir(parents=True)
    (folder / "script" / "brief.json").write_text(json.dumps({"angle": "x"}), encoding="utf-8")
    (folder / "audio" / "raw_voice.mp3").write_bytes(b"0123456789" * 50)

    now = datetime.now(timezone.utc)
    project = ProjectDoc(
        id="proj-artifacts", title="P", folder=folder, created=now,
        phases={"brief": PhaseRecord(status="done")},
        takes=[Take(id=1, created=now, phases={"brief": PhaseRecord(status="done")})],
        current_take_id=1,
    )
    save_project(project)

    client = TestClient(create_app(token=TOKEN, output_root=output_root, thumb_cache_dir=tmp_path / "t"))
    return client


def test_artifacts_listing_reports_what_exists(client_with_project):
    body = client_with_project.get("/api/projects/proj-artifacts/takes/1/artifacts", headers=AUTH).json()
    assert "brief" in body["files"]
    assert "scenes" not in body["files"]


def test_a_known_artifact_is_served(client_with_project):
    res = client_with_project.get("/api/projects/proj-artifacts/takes/1/artifact/brief", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["angle"] == "x"


def test_an_unknown_key_is_404(client_with_project):
    assert client_with_project.get(
        "/api/projects/proj-artifacts/takes/1/artifact/nope", headers=AUTH
    ).status_code == 404


def test_a_path_cannot_be_smuggled_in_as_a_key(client_with_project):
    """The key space is a table, so there is nothing to traverse."""
    for attempt in ("script%2Fbrief.json", "..%2F..%2Fproject.json"):
        res = client_with_project.get(
            f"/api/projects/proj-artifacts/takes/1/artifact/{attempt}", headers=AUTH
        )
        assert res.status_code == 404


def test_audio_honours_range(client_with_project):
    res = client_with_project.get(
        "/api/projects/proj-artifacts/takes/1/artifact/raw_voice",
        headers={**AUTH, "Range": "bytes=0-9"},
    )
    assert res.status_code == 206
    assert res.headers["content-range"].startswith("bytes 0-9/")
    assert len(res.content) == 10


def test_artifacts_require_a_token(client_with_project):
    assert client_with_project.get("/api/projects/proj-artifacts/takes/1/artifacts").status_code == 401
```

- [ ] **Step 6: Add the routes**

In `avg/api/routes_takes.py`, alongside the existing take routes:

```python
@router.get("/api/projects/{project_id}/takes/{take_id}/artifacts")
async def list_take_artifacts(project_id: str, take_id: int, request: Request) -> dict:
    """What this take actually has on disk, by artifact key.

    Drives the panel's header strip and lets it skip a body whose file was
    never written - a phase that failed halfway leaves some of its
    artifacts and not others, and "the file is not there" is a fact worth
    stating rather than an empty view.
    """
    project = _project_or_404(request, project_id)
    return {"files": available(project, take_id)}


@router.get("/api/projects/{project_id}/takes/{take_id}/artifact/{key}")
def get_take_artifact(project_id: str, take_id: int, key: str, request: Request) -> FileResponse:
    # A plain `def`: FileResponse reads the file on a worker thread, and a
    # ranged audio request must not stall the event loop carrying render
    # progress. Same reasoning as `project_video` in `avg/api/app.py`,
    # which is also what handles Range - it answers 206 + Content-Range
    # straight off the ASGI scope.
    project = _project_or_404(request, project_id)
    path = resolve(project, take_id, key)
    if path is None or not path.is_file():
        # One 404 for unknown key, unknown take and missing file alike:
        # which of the three it was is not something a file-serving route
        # should tell a client.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such artifact")
    spec = spec_for(key)
    return FileResponse(path, media_type=spec.media_type if spec else "application/octet-stream")


@router.get("/api/projects/{project_id}/takes/{take_id}/artifact/{key}/{name}")
def get_take_artifact_file(project_id: str, take_id: int, key: str, name: str, request: Request) -> FileResponse:
    """One file out of a directory artifact (`images`).

    `name` is validated against the directory's own listing rather than
    sanitised - a name that is not in the listing does not exist, which is
    both the security answer and the honest one.
    """
    project = _project_or_404(request, project_id)
    folder = resolve(project, take_id, key)
    if folder is None or not folder.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such artifact")
    match = next((f for f in folder.iterdir() if f.is_file() and f.name == name), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such file")
    return FileResponse(match)
```

with, at the top of that file:

```python
from fastapi.responses import FileResponse

from avg.core.artifacts import available, resolve, spec_for
```

`_project_or_404` already exists in this module for the other take routes — reuse it rather than adding a second lookup.

- [ ] **Step 7: Run everything**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add avg/core/artifacts.py avg/api/routes_takes.py tests/core/test_artifacts.py tests/api/test_artifact_routes.py
git commit -m "Serve a take's artifacts through a whitelisted table

Every phase writes real files and nothing read them back - the Studio
panel said 'Done.' for six of the eight. These routes hand them to the UI,
take-scoped, which also closes the Slice 5 gap where /images and /video
could only ever serve the CURRENT take.

The key space is a table, not a path: a key that is not in it is a 404
before any filesystem call, so there is structurally nothing to traverse.
Paths come from takes.PHASE_ARTIFACTS rather than a second copy, and a
test asserts they cannot drift - an artifact the panel shows but
clone_take does not copy would be a file that silently vanishes in the
next take."
```

---

## Task 7: The per-phase artifact panel

**Files:**
- Create: `web/src/artifacts/PhaseArtifact.tsx`, `web/src/artifacts/bodies.tsx`
- Modify: `web/src/Studio.tsx`, `web/src/api.ts`, `web/src/tokens.css`

**Interfaces:**
- Consumes: the Task 6 routes.
- Produces: `api.ts` exports `getTakeArtifacts(projectId, takeId)`, `artifactUrl(projectId, takeId, key)`, `artifactFileUrl(projectId, takeId, key, name)`.

- [ ] **Step 1: Add the client functions**

In `web/src/api.ts`:

```ts
// ---- take artifacts ----

/** `GET /api/projects/{id}/takes/{take}/artifacts` — artifact key to size
 * in bytes (or file count, for a directory artifact). Only what exists. */
export function getTakeArtifacts(projectId: string, takeId: number): Promise<{ files: Record<string, number> }> {
  return apiGet<{ files: Record<string, number> }>(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifacts`,
  );
}

/** A URL, not a fetch: `<img>` and `<audio>` cannot send a header, so
 * these go through the same `mediaUrl` helper `videoUrl`/`imageUrl`
 * already use — one token scheme for every media route, not a second. */
export function artifactUrl(projectId: string, takeId: number, key: string): string {
  return mediaUrl(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifact/${encodeURIComponent(key)}`,
  );
}

/** One file out of a directory artifact. `name` is a PATH SEGMENT, matching
 * the route's `/{key}/{name}` shape — not a query parameter. */
export function artifactFileUrl(projectId: string, takeId: number, key: string, name: string): string {
  return mediaUrl(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifact/${encodeURIComponent(key)}/${encodeURIComponent(name)}`,
  );
}
```

- [ ] **Step 2: Write the bodies**

Create `web/src/artifacts/bodies.tsx` with one component per phase. Each takes `{ projectId, takeId, files }` and fetches its own JSON through `artifactUrl`:

```tsx
import { useEffect, useState } from "react";
import { artifactUrl } from "../api";

/** Fetch one JSON artifact. A missing file is `null`, never an error -
 * a phase that failed halfway leaves some artifacts and not others. */
export function useArtifactJson<T>(projectId: string, takeId: number, key: string, enabled: boolean): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!enabled) {
      setData(null);
      return;
    }
    let cancelled = false;
    fetch(artifactUrl(projectId, takeId, key))
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!cancelled) setData(j as T);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, takeId, key, enabled]);
  return data;
}

export function BriefBody({ projectId, takeId }: { projectId: string; takeId: number }) {
  const brief = useArtifactJson<Record<string, unknown>>(projectId, takeId, "brief", true);
  if (!brief) return <p className="artifact-empty">No brief file for this take.</p>;
  return (
    <dl className="artifact-fields">
      {Object.entries(brief).map(([field, value]) => (
        <div key={field} className="artifact-fields__row">
          <dt>{field.replace(/_/g, " ")}</dt>
          <dd>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</dd>
        </div>
      ))}
    </dl>
  );
}
```

**The JSON shapes, read from the phase modules — do not guess these:**

| Artifact | Shape |
|---|---|
| `brief` | Whatever the brief phase wrote; render as key/value rows (`writing.py:126`) |
| `scenes` | `[{"index": int, "text": str, "image_prompt": str, "label": str}]`, 1-indexed (`writing.py:55,245`) |
| `video_map` | `{"version": 1, "image_count": int, "scenes": [{"scene": int, "role": str, "beat": str, "visual_intent": str}], "zoom_words": [...], "notes": str}` (`director.py:232-238`) |
| `direction_plan` | `{"version": 1, "grade": {"look": str}, "scenes": [{"scene": int, "motion": {"type": str, "intensity": float, "ease": str}, "transition_in": str, "grade": {...}}]}` (`director.py:373-380`) |
| `card_plan` | `{"cards": [{"element": "focused", "scene": int, "text": str, "accent": str, "start": float?, "end": float?}]}` — Director II writes it without `start`/`end`; Captions adds them in place (`director.py:305-318`, `captions.py:438`) |

Write the remaining six in the same file and the same shape:

- `ScriptBody` — the narration text (fetched as text, not JSON) above the scene list from `scenes`, each scene numbered with its text.
- `Director1Body` — one card per entry of `video_map`, showing the scene number and its image prompt.
- `VisualsBody` — the existing grid, moved here, reading `files.images` for the count and `artifactFileUrl` for each name (the listing comes from the existing `/images` route until it is take-scoped; use `getTakeArtifacts` for the count and the images listing route for names).
- `VoiceBody` — two `<audio controls>` elements, "Raw" and "Optimized", each `src={artifactUrl(...)}`, with a line saying which one the render uses.
- `Director2Body` — a table from `direction_plan`: scene, direction, zoom, hold.
- `CaptionsBody` — the cards from `card_plan` with their in/out times, and a note when timings are absent (Director II writes the plan; Captions adds the timings).

- [ ] **Step 3: Write the shell**

Create `web/src/artifacts/PhaseArtifact.tsx`: the header strip (status chip, duration, file count, estimated cost) plus a switch on phase key that renders the matching body, falling back to today's "Not started yet." / "Done." for a phase with no artifacts on disk.

- [ ] **Step 4: Mount it in Studio**

In `web/src/Studio.tsx`, replace `ArtifactDetail`'s six-way "Done."/"Running…" tail with `<PhaseArtifact …/>`, keeping the existing render-progress, failure and video branches exactly as they are — those already work and are not what this task is fixing.

- [ ] **Step 5: Style it**

Append to `web/src/tokens.css`: `.artifact-fields`, `.artifact-cards`, `.artifact-table`, `.artifact-audio`. Reuse the existing surface/line/radius tokens and the `18px 22px` padding `.health-stage` and `.errorlog` already share. No new colours.

- [ ] **Step 6: Build, lint, and look at every phase**

Run: `npm --prefix web run build && npm --prefix web run lint`

Then open a finished real project and click all eight rows at 1280×820 and 900×700, in both themes. Every row shows its real content; no row shows a raw JSON dump; long text scrolls inside its own container rather than stretching the page.

- [ ] **Step 7: Commit**

```bash
git add web/src/artifacts web/src/Studio.tsx web/src/api.ts web/src/tokens.css
git commit -m "Show every phase's real output in the artifact panel

Six of the eight phases rendered the word 'Done.' while each had written a
real file. The panel now shows the brief's fields, the narration and its
scenes, the image prompts, the images, both narration audio tracks, the
per-scene motion plan and the timed caption cards.

Voice shows two players, raw and optimized - not one per scene. The
pipeline writes exactly two files (avg/pipeline/phases/voice.py), which is
a correction to the spec's own artifact table: that was written from a
legacy D:\\AI_Video_Gen project, and this app uses different filenames."
```

---

## Task 8: Live List storage and catalogues

**Files:**
- Create: `avg/core/model_prefs.py`, `avg/providers/catalogues.py`
- Modify: `avg/api/app.py` (`app.state.model_prefs`, `model_prefs_path` override), `avg/api/routes_settings.py`, `avg/core/settings_schema.py`
- Test: `tests/core/test_model_prefs.py`, `tests/providers/test_catalogues.py`, `tests/api/test_live_list_routes.py`

**Interfaces:**
- Produces:
  - `model_prefs.Entry(selected: bool = False, favourite: bool = False, note: str = "")`
  - `model_prefs.ModelPrefs(path: Path)` with `all(kind: str) -> dict[str, Entry]`, `set(kind: str, entry_id: str, **fields) -> Entry`, `remove(kind: str, entry_id: str) -> None`
  - `catalogues.list_image_models(provider: str, *, transport=None) -> list[str]`
  - `catalogues.list_voices(provider: str, *, transport=None) -> list[dict[str, str]]`
  - `GET /api/live-list`, `POST /api/live-list/refresh`, `PATCH /api/live-list/{kind}/{entry_id}`, `POST /api/live-list/{kind}`, `DELETE /api/live-list/{kind}/{entry_id}`

- [ ] **Step 1: Write the failing prefs tests**

Create `tests/core/test_model_prefs.py`:

```python
"""Tests for the Live List's own data.

The rule that matters: a catalogue refresh replaces machine-owned data
wholesale, and must never take a user's note with it.
"""

from __future__ import annotations

from avg.core.model_prefs import ModelPrefs


def test_a_note_survives_a_reload(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "groq/llama-3.3-70b-versatile", selected=True, note="best for briefs")

    reopened = ModelPrefs(tmp_path / "model_prefs.json")
    entry = reopened.all("text")["groq/llama-3.3-70b-versatile"]
    assert entry.selected is True
    assert entry.note == "best for briefs"


def test_setting_one_field_leaves_the_others_alone(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("voice", "edge/en-US-AriaNeural", selected=True, note="narration default")
    prefs.set("voice", "edge/en-US-AriaNeural", favourite=True)

    entry = prefs.all("voice")["edge/en-US-AriaNeural"]
    assert (entry.selected, entry.favourite, entry.note) == (True, True, "narration default")


def test_kinds_do_not_collide(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("text", "a/b", selected=True)
    assert prefs.all("image") == {}


def test_a_corrupt_file_is_empty_not_an_error(tmp_path):
    path = tmp_path / "model_prefs.json"
    path.write_text("{not json", encoding="utf-8")
    assert ModelPrefs(path).all("text") == {}


def test_remove_deletes_one_entry(tmp_path):
    prefs = ModelPrefs(tmp_path / "model_prefs.json")
    prefs.set("image", "pollinations/flux", selected=True)
    prefs.remove("image", "pollinations/flux")
    assert prefs.all("image") == {}
```

- [ ] **Step 2: Run it, watch it fail, then write the module**

Run: `.venv-build/Scripts/python.exe -m pytest tests/core/test_model_prefs.py -q` → `ModuleNotFoundError`.

Create `avg/core/model_prefs.py` following `avg/core/model_cache.py`'s shape exactly — same load-or-empty tolerance, same atomic-ish write, a `dict[kind][entry_id] -> {selected, favourite, note}` document with `schema_version: 1`. Module docstring must say why it is a separate file from `working_models.json`: that document is replaced wholesale on every sweep, and merging user data into it is the mechanism that kept stale entries alive in the legacy app.

- [ ] **Step 3: Write the failing catalogue tests**

Create `tests/providers/test_catalogues.py`, injecting a `Transport` backed by `httpx.MockTransport` exactly as `tests/providers/test_probe.py` does:

```python
def test_pollinations_image_models_come_from_its_own_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "image.pollinations.ai" in str(request.url)
        return httpx.Response(200, json=["flux", "kontext"])

    assert list_image_models("pollinations", transport=_mock(handler)) == ["flux", "kontext"]


def test_a_provider_with_no_catalogue_returns_empty_not_a_guess():
    """OpenAI and Gemini advertise no image-model list. An invented one is
    exactly what avg/providers/models.py's standing rule forbids: the only
    fallback is a previous fetch, never a literal in source."""
    assert list_image_models("openai", transport=None) == []


def test_an_unreachable_catalogue_is_empty_not_an_exception():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    assert list_image_models("pollinations", transport=_mock(handler)) == []
```

- [ ] **Step 4: Write `avg/providers/catalogues.py`**

Listing only, never generation. `list_image_models` handles `pollinations` (`GET https://image.pollinations.ai/models`) and `stablehorde` (`GET https://stablehorde.net/api/v2/status/models?type=image`), returning `[]` for any other provider. `list_voices` delegates to the existing `VoiceBackend.list_voices` for `edge` and `elevenlabs` — both plain GETs, no synthesis, no spend. Every failure returns empty; the module docstring records that images and voice are **listed, never probed**, and why (an image probe costs an image, an ElevenLabs probe costs characters).

- [ ] **Step 5: Add the setting**

In `avg/core/settings_schema.py`:

```python
    SettingSpec(
        id="live_list_source",
        group="models",
        label="Live List source",
        help="Use the app-wide Live List, or keep a shortlist that belongs to this channel alone.",
        type="choice",
        default="global",
        scope="channel",
        choices=["global", "channel"],
        # Nothing is invalidated: this changes which models are OFFERED,
        # never which one produced an existing artifact.
        invalidates=None,
    ),
```

- [ ] **Step 6: Write the failing route tests, then the routes**

Create `tests/api/test_live_list_routes.py` covering: the merged `GET` includes probed text entries and prefs; `PATCH` sets a note and returns it; a `manual` entry can be added and deleted; a `catalogue` entry cannot be deleted (400); `refresh` never calls a generation endpoint (monkeypatch `catalogues.list_image_models` and assert the probe module is untouched); and every route 401s without a token.

Plus the scope test, which is the one that would silently rot:

```python
def test_a_channel_on_its_own_scope_does_not_read_the_global_list(client, paths):
    """No merge layer, exactly like every other channel-scoped setting.

    A channel set to `channel` uses its own document or nothing - never a
    blend, which is the mechanism three legacy config bugs came from.
    """
    client.post("/api/live-list/text", headers=AUTH, json={"entry_id": "groq/global-only"})
    client.post("/api/channels", headers=AUTH, json={"label": "Shorts"})
    channel_id = client.get("/api/channels", headers=AUTH).json()["channels"][-1]["id"]
    client.post(f"/api/channels/{channel_id}/activate", headers=AUTH)
    client.post("/api/settings/values", headers=AUTH, json={"values": {"live_list_source": "channel"}})

    entries = client.get("/api/live-list", headers=AUTH).json()["text"]
    assert all(e["id"] != "groq/global-only" for e in entries)
```

Then add the five routes to `avg/api/routes_settings.py`, and `app.state.model_prefs` plus a `model_prefs_path` override to `create_app`, following the `model_cache` wiring added in Slice 7 exactly.

- [ ] **Step 7: Run everything and commit**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
git add avg/core/model_prefs.py avg/providers/catalogues.py avg/api tests/ avg/core/settings_schema.py
git commit -m "Add Live List storage and the catalogues it lists from

Images and voice are LISTED, never probed: probing an image model means
paying for an image, and probing an ElevenLabs voice costs characters. So
the Live List curates what a provider advertises rather than proving it,
and text keeps the Slice 7 probe, which is the one kind where the verdict
is cheap.

Selections, favourites and notes live in their own document. Folding them
into working_models.json would mean merging on every sweep instead of
replacing - the exact mechanism that kept a stale entry alive in the
legacy app. A channel can opt into its own shortlist through the new
live_list_source setting; there is no merge layer, the channel either uses
the global document or its own."
```

---

## Task 9: The Live List screen

**Files:**
- Create: `web/src/LiveList.tsx`
- Modify: `web/src/App.tsx` (screen union), `web/src/AppHealth.tsx` (entry point), `web/src/api.ts`, `web/src/tokens.css`

- [ ] **Step 1: Add the client functions**

`getLiveList()`, `refreshLiveList()`, `patchLiveEntry(kind, id, body)`, `addLiveEntry(kind, id)`, `removeLiveEntry(kind, id)` in `web/src/api.ts`, each with the same doc-comment discipline as the Slice 7 model-cache functions.

- [ ] **Step 2: Write the screen**

Three tabs — Text, Images, Voice — each a table of `provider/id`, a source chip (`probed` / `catalogue` / `manual`), a tick, a star, and an editable note. Voice rows carry language and gender with filter controls above the table, because Edge advertises several hundred voices and the shortlist is the point of the feature.

One filled terracotta action on the screen: **Refresh catalogues**. The tick, star and note are inline controls, not primary actions. Notes commit on blur through the same `DeferredInput` the settings fields use, so the screen does not fire a request per keystroke.

- [ ] **Step 3: Route to it**

Add `{ name: "livelist" }` to `App.tsx`'s screen union and a plain button on App Health — next to the model-cache card, which is what the Live List supersedes for picking.

- [ ] **Step 4: Build, lint, use it**

Run: `npm --prefix web run build && npm --prefix web run lint`

Then: refresh the catalogues, tick four voices, favourite one, write a note. Reload the app and confirm all four survive.

- [ ] **Step 5: Commit**

---

## Task 10: The pickers read the Live List

**Files:**
- Modify: `avg/core/settings_schema.py`, `web/src/ModelPicker.tsx`, `web/src/fields/index.tsx`

- [ ] **Step 1: Point the image and voice settings at it**

In `avg/core/settings_schema.py`, add `source="live_list:image"` to `image_model`, and `source="live_list:voice"` to `voice_id` and `voice_model_id`. Leave every `type` as it is: an empty Live List must still allow a typed value, which is the rule Slice 7 established when the model cache was empty.

- [ ] **Step 2: Teach `ModelPicker` the three sources**

It currently reads `GET /api/models/cache`. It now reads `GET /api/live-list`, filters to `selected` entries of the kind named by `spec.source`, sorts favourites first, and shows the entry's note as the field's help text — so what you wrote about a model is in front of you at the moment you pick it. The empty-cache fallback to a free-text input stays exactly as it is.

In `web/src/fields/index.tsx`, the dispatch becomes a prefix test:

```tsx
  // `source` outranks `type`: a field whose options only exist at runtime
  // cannot be described by the catalogue. Three sources today, one
  // component - the kind is the part after the colon.
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
```

- [ ] **Step 3: Run everything**

Run: `.venv-build/Scripts/python.exe -m pytest tests -q && npm --prefix web run build && npm --prefix web run lint`

- [ ] **Step 4: Verify against the real account**

Open Settings → Models. The text model fields offer the ticked text models, favourites first. The image model field offers the ticked Pollinations/Stable Horde models. The voice field offers exactly the voices ticked in Task 9, with their notes underneath.

- [ ] **Step 5: Commit**

---

## Verification for the whole slice

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 613 passing.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Create a video from scratch: every phase turns sage as it finishes, live, with no refresh. **This is the acceptance test for Task 1 and the whole reason for the slice.**
- [ ] Press render: the pill says a job is running within a second, the Transport bar carries the percent, neither flickers while other requests come and go.
- [ ] After a render finishes, `%TEMP%` holds no `avg-render-*` folder.
- [ ] Click each of the eight phases on a finished project and see its real output.
- [ ] Open the Live List, refresh, tick four voices, favourite one, note it; the voice picker then offers exactly those four, favourite first, note under the field.
- [ ] `"Build Installer.bat"` — produces `AVG APP/` and the installer, and the packaged app does all of the above.
