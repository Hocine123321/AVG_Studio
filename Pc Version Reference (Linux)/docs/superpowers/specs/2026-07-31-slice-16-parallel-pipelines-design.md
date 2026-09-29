# Slice 16 — Parallel pipelines

**Goal:** let more than one video be made at once, while keeping the one
thing that genuinely cannot be shared — the render child process —
serialised behind a queue.

**Date:** 2026-07-31. Every line reference below was read from the source on
that date.

Sixth and last slice of the 2026-07-31 backlog, deliberately last: it
inverts an invariant that four earlier slices were built on top of, and it
wants a stable base underneath it.

---

## 1. What the evidence shows

### 1.1 One job at a time is wired into three layers

`CLAUDE.md` states it as a rule — "**One job at a time.**
`app.state.render_thread` is a single slot shared by full-pipeline runs and
`/render` jobs; a second start gets a 409." The code matches:

| Location | What is singular |
|---|---|
| `avg/api/app.py:435-442` | `render_thread`, `rendering_project_id`, `pipeline_cancel_token` |
| `avg/api/app.py:830` | the 409 check on a second start |
| `avg/api/app.py:756-762` | the teardown that clears all three |
| `web/src/Home.tsx` | `progress: ProgressEvent \| null`, documented as "the single render this whole process ever runs at once" |
| `web/src/useEvents.ts` | derives *the* live job's state |

So this is not a lock to remove. It is a shape — one job, one cancel token,
one progress stream — that runs from `app.state` through the WebSocket to
the Home hero and the Transport bar.

### 1.2 The render child genuinely cannot be parallelised here

`app.state.engine` is one `MediaEngine`, and `avg/media/engine.py`'s
`render()` "spawns the child, yields events, joins it" — one child process,
tracked by one engine. `CLAUDE.md` also records that a drain loop is never
broken out of early, because that is what guarantees the child gets joined.

Rendering is local CPU and ffmpeg; two at once on one machine would contend
for the same cores anyway. So renders serialising is not a limitation to
work around — it is correct, and it is what the request asks for.

### 1.3 The phases that would overlap are network-bound

Seven of the eight phases are provider calls. `LEAF_COST`
(`avg/core/costs.py:38`) prices `visuals` at 0.180 against 0.004–0.014 for
the text phases, and Visuals already runs its scenes through a
`ThreadPoolExecutor` — so the pipeline is already concurrent *within* a
phase, against one provider.

That is the real constraint on running two pipelines: not CPU, but
requests-per-minute against one key. `CLAUDE.md` records Groq as the only
text provider that currently works.

### 1.4 Events already carry the project they belong to

Every event in the take log names its project —
`progress project_id=20260730_152345_… percent=50`. The event vocabulary
does not need to change to support several jobs; only the code that assumes
one does.

---

## 2. Decisions

1. **A configurable cap, default 2**, in the App group from Slice 12. Not
   unbounded, not a hard-coded constant.
2. **Renders queue**, one at a time, behind the existing single
   `MediaEngine`.
3. **`app.state` grows a job registry**, replacing the three singular fields.

---

## 3. Architecture

### 3.1 One registry replaces three singular fields

`app.state.render_thread`, `rendering_project_id` and
`pipeline_cancel_token` become one structure:

```python
app.state.jobs: dict[str, Job]      # keyed by project_id
```

where `Job` holds the thread, the cancel token, the kind (`pipeline` or
`render`) and the queued/running state. The three old fields go away
together — leaving one behind as a "current" pointer would be the bug this
whole slice has to avoid.

Starting a job:

- **already running for this project** → 409, as today. Two runs of the
  *same* project would fight over the same folder and the same
  `project.json`, which `avg/pipeline/create.py` writes atomically but does
  not arbitrate.
- **at the cap** → the job is *queued*, not refused, and starts when a slot
  frees. The 409 stops meaning "busy" and starts meaning only "this project
  is already running".

`max_parallel_jobs` — a new `SettingSpec` in the `app` group (Slice 12),
`scope="app"`, `type="choice"`, choices `1`–`4`, default `2`. Its `help`
names `avg/api/app.py` as its reader, per Slice 12 §1.2's rule that a new
setting must be wired to something.

Setting it to `1` restores today's behaviour exactly, which is the escape
hatch when a provider starts rate-limiting.

### 3.2 Renders queue behind the one engine

A render — whether the whole of a pipeline's Render phase or a bare
`POST /api/projects/{id}/render` — takes a **render lease** before touching
`app.state.engine`. One lease exists. A pipeline that reaches its Render
phase while another render holds the lease waits there, and its status says
so: *queued behind <title>*, not "running" and not "stalled".

Everything before Render overlaps freely. This is the shape the request
described: the AI phases run together, the renders line up.

The drain loop is untouched. Each job drains its own generator to
completion, which is what still guarantees the child is joined.

### 3.3 The frontend stops assuming one job

`useEvents.ts` keys its state by `project_id` — the events already carry it
(§1.4) — and exposes a map instead of a single live job. Consumers change
with it:

- **Home**: the hero becomes a stack. One running project renders as it does
  now; several render as a compact row each. A queued project shows its
  position rather than a progress bar it cannot fill.
- **Transport bar**: names the job it is showing and lets the user switch
  when more than one is live, rather than silently picking one.
- **Studio**: unchanged. It already shows one project, and now it shows
  *that* project's stream rather than *the* stream.

`ProgressEvent | null` becoming a map is the change most likely to hide a
bug, because every existing consumer compiles fine against a different
lookup. The verification list below checks each consumer by hand for that
reason.

### 3.4 What does not change

The phase registry, the settings catalogue's role, what a phase writes, the
event vocabulary, `save_project`'s atomicity, and the takes model. This slice
changes *how many* of a thing can be in flight, not what the thing is.

---

## 4. Testing

- **The cap**: with `max_parallel_jobs=2`, a third start is queued rather
  than refused, and begins when one finishes. With `1`, behaviour matches
  today's exactly — the same tests that assert the current 409 must still
  pass with the setting at 1.
- **Same project twice** still gets a 409 at any cap.
- **The render lease**: two pipelines reaching Render overlap in every
  earlier phase and serialise at Render; the second reports queued, and
  `MediaEngine` never has two children.
- **Cancellation is per job**: cancelling one leaves the other running and
  joins only its own child.
- **Teardown**: a job that fails, is cancelled, or raises removes itself from
  the registry — asserted for all three, because a leaked entry would
  permanently consume a slot.
- **Events**: two concurrent jobs' events never cross project ids.

Target: **769 tests still passing**, plus the new ones; mypy no worse than
its `Found 688 errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Set `max_parallel_jobs=1` and confirm the app behaves exactly as it does
  today, including the 409.
- Set it to 2, start three videos: two run, the third says queued, and it
  starts when a slot frees.
- Watch two run to Render: they overlap throughout the AI phases and
  serialise at Render, with the waiting one saying what it waits behind.
- Cancel one of two running jobs: the other is unaffected, and Task Manager
  shows one ffmpeg child, not two orphans.
- Kill a job by failing it (a provider with no key): its slot frees.
- Home with two live jobs shows both; the Transport bar names which it is
  showing and can switch.
- Open Studio on one of two running projects: it shows that project's
  progress, not the other's.
- `"Build Installer.bat"` — the same behaviour in `AVG APP/`.

---

## 6. Out of scope

- Parallelism *within* a phase beyond what Visuals already does.
- A cross-project provider rate limiter. The cap is the blunt instrument
  this slice ships; a real limiter is its own design, and §1.3 is the
  evidence that would motivate it.
- Reordering or prioritising the queue. First started, first served.
- Two renders at once. §1.2 is why.
