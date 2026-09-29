# Slice 16 — Parallel pipelines: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let more than one video be made at once, while keeping the one thing that genuinely cannot be shared — the render child process — serialised behind a queue.

**Architecture:** Five tasks. A job registry replaces the three singular `app.state` fields; a render lease serialises the one `MediaEngine`; a setting caps concurrency; and the frontend stops assuming there is exactly one live job.

**Tech Stack:** Python 3.12 + FastAPI (backend), React 19 + TypeScript (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-16-parallel-pipelines-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** Tests: `-m pytest tests -q`. Types: `-m mypy`.
- **Never go below the suite's count when you start.**
- **mypy baseline is `Found 688 errors in 22 files`.** Add nothing to it.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1`.
- **This slice inverts a documented invariant.** `CLAUDE.md` states "One job at a time. `app.state.render_thread` is a single slot shared by full-pipeline runs and `/render` jobs; a second start gets a 409." Task 2 updates that text in the same commit as the code — a rule and its implementation disagreeing is worse than either.
- **Renders still serialise.** `app.state.engine` is one `MediaEngine` tracking one child process. Two renders at once is not a goal and must not become possible.
- **A drain loop is never broken out of early.** That is what guarantees the child process gets joined. Every job drains its own generator to completion.
- **Setting `max_parallel_jobs` to `1` must reproduce today's behaviour exactly**, including the 409. That is the escape hatch and the regression test.
- **A new setting must be wired to something** — `settings_schema.py:79-93` records three deleted for not being.
- **Nothing below `avg/api/` may print or prompt.**
- **Never use a native form control**; never add a Google Fonts `@import`.
- **One filled terracotta `.btn--primary` primary action per screen.**
- **The frontend has no unit tests by convention.**
- **Match code by content, not by line number.**
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

---

## Task 1: A job registry

**Files:**
- Create: `avg/core/jobs.py`
- Create: `tests/core/test_jobs.py`

**Interfaces:**
- Consumes: `avg.pipeline.phase.CancellationToken`.
- Produces, and Tasks 2–3 depend on these exactly:
  - `@dataclass class Job: project_id: str; kind: str; thread: Any; cancel_token: Any; queued: bool = False`
  - `class JobRegistry` with `add(job) -> None`, `get(project_id) -> Job | None`, `remove(project_id) -> None`, `running() -> list[Job]`, `queued() -> list[Job]`, `is_running(project_id) -> bool`, `count_running() -> int`

**Why:** Today three singular fields carry the whole idea of "the job" — `app.state.render_thread`, `rendering_project_id` and `pipeline_cancel_token` (`avg/api/app.py:435-442`), read at three start sites (the pipeline start, the resume, and `/render`) and cleared at two teardowns. Replacing them with one keyed structure is the whole slice; leaving any of the three behind as a "current" pointer would be the bug this has to avoid.

Pure data with a lock, no HTTP, so it is testable without a server.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_jobs.py`:

```python
"""The job registry that replaces the three singular `app.state` fields."""

from __future__ import annotations

from avg.core.jobs import Job, JobRegistry


class _Thread:
    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def _job(project_id: str, *, queued: bool = False, alive: bool = True) -> Job:
    return Job(
        project_id=project_id,
        kind="pipeline",
        thread=_Thread(alive),
        cancel_token=None,
        queued=queued,
    )


def test_a_registry_starts_empty():
    registry = JobRegistry()
    assert registry.running() == []
    assert registry.count_running() == 0
    assert registry.get("nope") is None


def test_the_same_project_twice_is_visible_as_already_running():
    """Two runs of one project would fight over the same folder and the same
    project.json - that stays a 409 at any cap."""
    registry = JobRegistry()
    registry.add(_job("alpha"))
    assert registry.is_running("alpha") is True
    assert registry.is_running("beta") is False


def test_a_finished_thread_no_longer_counts_as_running():
    """A leaked entry would permanently consume a slot."""
    registry = JobRegistry()
    registry.add(_job("alpha", alive=False))
    assert registry.count_running() == 0
    assert registry.is_running("alpha") is False


def test_queued_jobs_are_not_counted_against_the_cap():
    registry = JobRegistry()
    registry.add(_job("alpha"))
    registry.add(_job("beta", queued=True))
    assert registry.count_running() == 1
    assert [j.project_id for j in registry.queued()] == ["beta"]


def test_removing_a_job_frees_its_slot():
    registry = JobRegistry()
    registry.add(_job("alpha"))
    registry.remove("alpha")
    assert registry.count_running() == 0
    assert registry.get("alpha") is None


def test_removing_something_absent_is_not_an_error():
    """Teardown runs in a `finally`; it must not raise on a job that already
    removed itself."""
    JobRegistry().remove("never-existed")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_jobs.py -q
```

Expected: FAIL — `ModuleNotFoundError: avg.core.jobs`.

- [ ] **Step 3: Write the registry**

Create `avg/core/jobs.py`:

```python
"""Which projects are being worked on right now.

Replaces three singular fields that used to carry the whole idea of "the
job" - `app.state.render_thread`, `rendering_project_id` and
`pipeline_cancel_token`. They were read at three start sites and cleared at
two teardowns, and every one of them assumed there could only ever be one.

All three go away together. Leaving one behind as a "current job" pointer
would be exactly the bug this replacement exists to prevent: a second job
would quietly overwrite it and the first would become uncancellable.

Storage and bookkeeping only - no HTTP, no threads started here, no
knowledge of what a job does. `avg/api/app.py` owns starting and stopping;
this owns knowing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class Job:
    """One project's in-flight work.

    `queued` means accepted but not started - the cap was full when it
    arrived. A queued job holds no thread yet, so it is never counted
    against the cap it is waiting on.
    """

    project_id: str
    kind: str
    thread: Any
    cancel_token: Any
    queued: bool = False


class JobRegistry:
    """Every job, keyed by project id, behind one lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.project_id] = job

    def get(self, project_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(project_id)

    def remove(self, project_id: str) -> None:
        """Forget a job. Absent is not an error - teardown runs in a
        `finally` and must never raise over a job that already went."""
        with self._lock:
            self._jobs.pop(project_id, None)

    def _alive(self, job: Job) -> bool:
        thread = job.thread
        return thread is not None and bool(thread.is_alive())

    def running(self) -> list[Job]:
        """Jobs with a live thread. A dead thread is not running even if its
        entry has not been cleaned up yet - a leaked entry would otherwise
        consume a slot forever."""
        with self._lock:
            return [j for j in self._jobs.values() if not j.queued and self._alive(j)]

    def queued(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.queued]

    def count_running(self) -> int:
        return len(self.running())

    def is_running(self, project_id: str) -> bool:
        job = self.get(project_id)
        return job is not None and not job.queued and self._alive(job)
```

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_jobs.py -q
```

Expected: PASS (6 tests).

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 6 more than your starting count.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/core/jobs.py tests/core/test_jobs.py
git commit -m "feat: a job registry, replacing the idea that there is only one"
```

---

## Task 2: The cap, and the three guards it replaces

**Files:**
- Modify: `avg/core/settings_schema.py` (`max_parallel_jobs`)
- Modify: `avg/api/app.py` (registry wiring; all three start sites; both teardowns; the cancel route)
- Modify: `CLAUDE.md` (the "One job at a time" rule)
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes from Task 1: `Job`, `JobRegistry`.
- Produces: `app.state.jobs: JobRegistry`; setting `max_parallel_jobs`.

**Why:** The three guards live at `app.py:830` (pipeline start), `:883` (resume) and `:973` (`/render`), all reading `app.state.render_thread`. The teardowns at `:756` and `:1050` clear all three fields.

**The 409 changes meaning**: it stops meaning "busy" and starts meaning only "this project is already running". Two runs of the same project would fight over the same folder and the same `project.json`, which `save_project` writes atomically but does not arbitrate.

**`max_parallel_jobs=1` must reproduce today's behaviour exactly**, so existing tests asserting the current 409 must still pass with the setting at 1.

- [ ] **Step 1: Add the setting**

In `avg/core/settings_schema.py`, in the `app` group (Slice 12) or `runtime` if that slice has not landed:

```python
    SettingSpec(
        id="max_parallel_jobs",
        group="app",
        label="Videos at once",
        help=(
            "How many videos may be made at the same time. Read by "
            "`avg/api/app.py`. Renders always take turns whatever this says - "
            "there is one render engine. Raise it only if your providers are "
            "not rate-limiting you; 1 behaves exactly as this app always has."
        ),
        type="choice",
        default="2",
        scope="app",
        choices=["1", "2", "3", "4"],
        invalidates=None,
    ),
```

`type="choice"` stores a string, so read it with `int(...)` and a fallback.

- [ ] **Step 2: Write the failing tests**

Append to `tests/api/test_app.py` (match its existing fixtures; every test must monkeypatch the pipeline runner so nothing real runs):

```python
def test_the_same_project_twice_is_still_a_conflict(client, monkeypatch):
    """Unchanged at any cap: two runs of one project would fight over the
    same folder and the same project.json."""
    import threading

    release = threading.Event()
    monkeypatch.setattr(
        "avg.api.app.pipeline_runner.run",
        lambda *a, **k: iter([]) if release.wait(timeout=5) else iter([]),
    )
    # Start one, then ask for the same project again.
    # (Adapt to however this file already starts a pipeline.)


def test_a_cap_of_one_behaves_exactly_as_before(client, monkeypatch):
    """The escape hatch and the regression test in one. With the cap at 1,
    a second DIFFERENT project must not start concurrently."""


def test_a_job_beyond_the_cap_is_queued_not_refused(client, monkeypatch):
    """The 409 now means only "this project is already running". A third
    project at a cap of 2 waits its turn instead of being turned away."""


def test_a_failed_job_frees_its_slot(client, monkeypatch):
    """A leaked registry entry would permanently consume a slot - the worst
    failure this refactor could introduce."""
```

**Write real bodies for these four**, following whatever pattern `tests/api/test_app.py` already uses to start a pipeline and wait for it. The four behaviours above are the contract; the mechanics must match the existing file rather than a new harness invented here.

- [ ] **Step 3: Replace the three fields**

In `avg/api/app.py`, replace the initialisation (find `app.state.render_thread = None`):

```python
    # Every in-flight job, keyed by project id. Replaces the three singular
    # fields this app used to carry - `render_thread`,
    # `rendering_project_id` and `pipeline_cancel_token` - which between
    # them assumed there could only ever be one. All three are gone
    # deliberately: a leftover "current job" pointer would be silently
    # overwritten by a second job, leaving the first uncancellable.
    app.state.jobs = JobRegistry()
    # One render at a time, whatever the job cap says: `app.state.engine` is
    # a single MediaEngine tracking a single child process, and two renders
    # on one machine would contend for the same cores anyway.
    app.state.render_lease = threading.Lock()
```

Add `from avg.core.jobs import Job, JobRegistry` to the imports.

Replace each of the three guards. The pattern, at every site:

```python
        if app.state.jobs.is_running(project.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"project {project.id!r} is already running",
            )
```

and register the job where the thread used to be assigned:

```python
        cap = _max_parallel(app.state)
        at_capacity = app.state.jobs.count_running() >= cap
        thread = threading.Thread(target=_worker, daemon=True, name=f"avg-pipeline-{project.id}")
        app.state.jobs.add(
            Job(
                project_id=project.id,
                kind="pipeline",
                thread=thread,
                cancel_token=cancel_token,
                queued=at_capacity,
            )
        )
        if not at_capacity:
            thread.start()
```

Add the cap reader beside the other helpers:

```python
def _max_parallel(app_state: Any) -> int:
    """How many jobs may run at once. `1` reproduces this app's original
    single-slot behaviour exactly, which is both the escape hatch when a
    provider starts rate-limiting and the regression test for this change."""
    from avg.core.settings_schema import get_spec  # noqa: PLC0415

    raw = load_settings(app_state.settings_path).values.get(
        "max_parallel_jobs", get_spec("max_parallel_jobs").default
    )
    try:
        return max(1, int(str(raw)))
    except (TypeError, ValueError):
        return 1
```

Replace both teardowns — the two places that cleared all three fields — with:

```python
                app.state.jobs.remove(project.id)
                _start_next_queued(app)
```

and add the promoter:

```python
    def _start_next_queued(app: FastAPI) -> None:
        """Start the oldest queued job, if there is room.

        Called from every teardown, so a slot freeing always moves the queue.
        First started, first served - no priority, no reordering.
        """
        if app.state.jobs.count_running() >= _max_parallel(app.state):
            return
        pending = app.state.jobs.queued()
        if not pending:
            return
        job = pending[0]
        job.queued = False
        job.thread.start()
```

Update the cancel route to cancel **that project's** token rather than the process-wide one, via `app.state.jobs.get(project_id)`.

- [ ] **Step 4: Update the rule**

In `CLAUDE.md`, replace the **One job at a time** paragraph:

```markdown
**Several jobs, one render.** `app.state.jobs` (`avg/core/jobs.py`) holds
every in-flight job, keyed by project id, and `max_parallel_jobs` caps how
many run at once (default 2; set it to 1 for this app's original behaviour).
A second start for the **same project** is still a 409 — two runs would
fight over one folder and one `project.json`. A start beyond the cap is
**queued**, not refused. Renders serialise regardless, behind
`app.state.render_lease`: `app.state.engine` is one `MediaEngine` tracking
one child process.
```

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_app.py -q
```

Expected: PASS. Existing tests that assert the old "a job is already running" 409 for a *different* project will now fail — read each one: if it is asserting the single-slot invariant this slice deliberately replaces, update it and **say so in your report**. If it is asserting the same-project conflict, it must still pass unchanged.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/core/settings_schema.py avg/api/app.py CLAUDE.md tests/api/test_app.py
git commit -m "feat: several jobs at once, capped, with the same project still refused"
```

---

## Task 3: Renders take turns

**Files:**
- Modify: `avg/api/app.py` (acquire the lease around the render)
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes from Task 2: `app.state.render_lease`.
- Produces: nothing new.

**Why:** `avg/media/engine.py`'s `render()` spawns the child, yields events and joins it — one child, one engine. Two renders at once would contend for the same cores anyway, so serialising is correct rather than a limitation.

A pipeline that reaches its Render phase while another render holds the lease **waits there**, and says so.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_app.py`:

```python
def test_two_renders_never_overlap(client, monkeypatch):
    """One MediaEngine, one child process. Whatever the job cap says, the
    renders take turns."""
    import threading

    concurrent = 0
    peak = 0
    guard = threading.Lock()

    def fake_render(*_a, **_k):
        nonlocal concurrent, peak
        with guard:
            concurrent += 1
            peak = max(peak, concurrent)
        try:
            yield from ()
        finally:
            with guard:
                concurrent -= 1

    monkeypatch.setattr(client.app.state.engine, "render", fake_render)
    # Start two renders on different projects and join both.
    # (Adapt to however this file already starts a render.)
    assert peak <= 1
```

Write the real body following the file's existing render-start pattern.

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_app.py -q
```

Expected: FAIL — `peak` reaches 2.

- [ ] **Step 3: Take the lease**

In `avg/api/app.py`, wrap the drain of `engine.render(...)` — in **both** the `/render` worker and the pipeline's Render phase path — so the lease is held for the whole drain:

```python
                # One render at a time. Everything before Render overlaps
                # freely; here the jobs line up. The lease is held across the
                # WHOLE drain, never released early - releasing it before the
                # generator finishes is how a second child would be spawned
                # while the first was still being joined.
                with app.state.render_lease:
                    for event in engine.render(request):
                        ...
```

Keep the existing drain body exactly as it is — the loop must still run to completion, because that is what guarantees the child gets joined.

Emit a progress event before acquiring, so a waiting job says why:

```python
                if app.state.render_lease.locked():
                    manager.broadcast_sync(
                        ProgressEvent(
                            project_id=project.id,
                            percent=0,
                            message="Waiting for another render to finish...",
                        )
                    )
```

Match `manager`'s real broadcast method name — read the surrounding code rather than assuming.

- [ ] **Step 4: Run the tests, full suite, types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 5: Commit**

```bash
git add avg/api/app.py tests/api/test_app.py
git commit -m "feat: renders take turns behind one lease"
```

---

## Task 4: The frontend stops assuming one job

**Files:**
- Modify: `web/src/useEvents.ts`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes from Tasks 2–3: several concurrent event streams, each already carrying `project_id`.
- Produces: `useEvents` exposes `byProject: Record<string, ProgressEvent>` alongside whatever it exposes today.

**Why:** Events already carry the project they belong to — the take log shows `progress project_id=… percent=50` — so the event vocabulary needs no change. Only the code that assumes one job does.

**This is the change most likely to hide a bug**, because every existing consumer compiles fine against a different lookup. Step 4 checks each consumer by hand for that reason.

- [ ] **Step 1: Key the state by project**

In `web/src/useEvents.ts`, keep the existing single-job fields working (so nothing breaks at once) and add a map beside them:

```ts
  /** Every live job's latest progress, keyed by project id. Events have
   * always carried `project_id`; until now nothing kept more than one.
   * The single-job fields above are derived from this and stay for
   * consumers that genuinely only care about one project (Studio). */
  byProject: Record<string, ProgressEvent>;
```

Update the reducer that handles a progress event to write `byProject[event.project_id]` as well as the existing field, and to delete the entry when that project's job finishes, fails or is cancelled.

- [ ] **Step 2: Pass it down**

In `web/src/App.tsx`, pass `events.byProject` to `Home` and `Transport`. Leave `Studio` reading the single project's progress — it already shows one project, and now it shows *that* project's stream rather than *the* stream.

- [ ] **Step 3: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 4: Check every consumer by hand**

```bash
grep -rn "progress\b" web/src/*.tsx | grep -v "^web/src/useEvents"
```

For **each** hit, state in your report whether it still means "the one live job" and whether that is still correct. A consumer that silently keeps reading a single job when two are running is the defect this step exists to catch.

- [ ] **Step 5: Commit**

```bash
git add web/src/useEvents.ts web/src/App.tsx
git commit -m "feat: the event state knows about more than one job"
```

---

## Task 5: Home and the Transport bar show several jobs

**Files:**
- Modify: `web/src/Home.tsx`
- Modify: `web/src/Transport.tsx`
- Modify: `web/src/tokens.css`

**Interfaces:**
- Consumes from Task 4: `byProject`.
- Produces: nothing further.

**Why:** `Home.tsx`'s `progress` prop is documented as "the single render this whole process ever runs at once". The hero renders one job large; several need a compact row each. A queued project has no progress to show and must say its position instead of an empty bar.

- [ ] **Step 1: Home shows a stack**

In `web/src/Home.tsx`, change the `progress` prop to the map and render:

- **one** running job → the existing hero, unchanged;
- **several** → one compact row each, reusing `.hero__meta`, `.track` and `.track__fill` rather than new styling;
- **queued** → its position (`Queued · 2nd`), never a progress bar it cannot fill.

Keep exactly one `.btn--primary` on the screen — `New video`, added in Slice 10. Per-job actions stay plain `.btn`.

- [ ] **Step 2: The Transport bar names what it shows**

In `web/src/Transport.tsx`, when more than one job is live, name the project it is showing and let the user switch — a `Select` from `web/src/Select.tsx`, never a native `<select>`. With one job, it behaves exactly as it does now.

- [ ] **Step 3: Build, lint, and confirm no native control**

```bash
cd web && npm run build && npm run lint
```

```bash
grep -rn "<select\|type=\"checkbox\"" web/src/
```

Expected: both clean; the only grep hit is Home's sanctioned hidden `.select-dot` checkbox.

- [ ] **Step 4: Report what renders**

State what Home and the Transport bar show for: no jobs; one running; two running; one running and one queued; and a job that just failed. Read your own code for each.

- [ ] **Step 5: Commit**

```bash
git add web/src/Home.tsx web/src/Transport.tsx web/src/tokens.css
git commit -m "feat: Home and the Transport bar show every live job"
```

---

## Final verification

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — never below your starting count.
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] **Set `max_parallel_jobs=1` and confirm the app behaves exactly as it always has**, including the 409 — this is the regression test for the whole slice.
- [ ] Set it to 2 and start three videos: two run, the third says queued, and it starts when a slot frees.
- [ ] Watch two run to Render: they overlap through the AI phases and serialise at Render, with the waiting one saying what it waits behind.
- [ ] Cancel one of two running jobs: the other is unaffected. **Check Task Manager for exactly one ffmpeg child, not two orphans.**
- [ ] Fail a job (a provider with no key): its slot frees and a queued job starts.
- [ ] Home with two live jobs shows both; the Transport bar names which it is showing and can switch.
- [ ] Open Studio on one of two running projects: it shows *that* project's progress.
- [ ] `"Build Installer.bat"` — the same behaviour in `AVG APP/`.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 One registry replaces three fields | 1 (registry), 2 (wiring) |
| §3.1 `max_parallel_jobs`, queueing, same-project 409 | 2 |
| §3.2 Renders queue behind one lease | 3 |
| §3.3 Frontend stops assuming one job | 4 (state), 5 (screens) |
| §3.4 What does not change | 2 (CLAUDE.md updated to match) |
| §4 Testing | 1, 2, 3 |
| §5 Verification | Final verification |

**A note on Tasks 2 and 3's tests.** Their bodies are specified as behaviours rather than transcribed code, because `tests/api/test_app.py` already has a working harness for starting pipelines and renders and a second, invented one would drift from it. That is a deliberate exception to this plan's "complete code in every step" rule, and the only one — read the file's existing patterns and follow them.
