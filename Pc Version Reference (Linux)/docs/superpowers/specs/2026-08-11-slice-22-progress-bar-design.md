# Slice 22 — Progress bar phase-transition bug: Design

**Goal:** the transport bar (`web/src/Transport.tsx`, mounted once in
`App.tsx`, visible on every screen) stops showing the previous phase's
last percentage after the pipeline has already moved on to the next
phase.

## 0. Root cause (traced, confirmed against the actual component)

`Transport.tsx` reads `Object.values(events.byProject)` for its lead job -
`byProject` is `web/src/useEvents.ts`'s per-project progress map,
populated by `foldByProject`. That function's own docstring already
states its rule plainly: a `progress` event overwrites a project's entry;
a terminal event (`render_finished`, `pipeline_finished`, etc.) deletes
it; "anything else (a phase transition, ...) leaves the map untouched" -
explicitly, on purpose, as originally written.

That's the bug. Phase N's last `progress` event (percent 100) sits in
`byProject[id]`. `phase_started` fires for phase N+1. `foldByProject` does
nothing with it. The stale 100% entry sits there, visible in the one bar
that's on every screen, until phase N+1 happens to emit its own first
`progress` tick - which can take a while, depending on what that phase
does before its first `ctx.emit(ProgressEvent(...))` call.

The *other* progress-tracking path in the same file - the single
last-event `progress`/`phaseStarted` fields the main reducer maintains -
does not have this bug: `case "phase_started"` there already spreads
`...INITIAL_STATE` first, wiping the old `progress` back to `null`. This
slice brings `foldByProject` in line with that, rather than inventing a
new mechanism.

There's a direct precedent for exactly this shape of fix already in the
file, in the same function: the comment explaining why terminal events
delete the entry describes fixing the identical class of bug for job
*completion* ("which is why the Transport bar used to stick at
'Rendering... 100%' forever after every successful run"). This slice is
the same fix, for phase *transitions* instead of job completion.

## 1. The fix

`foldByProject` gains a `phase_started` branch: instead of leaving the
map untouched, it replaces that project's entry with a fresh
zero-percent one for the new phase (`{ type: "progress", project_id,
percent: 0, message: "Starting <phase label>…" }`, phase label from
`phases.ts`'s `PHASE_DEFS`).

**Why replace rather than delete:** checked against every consumer.
`Home.tsx`'s `runningRows` explicitly requires a truthy `byProject` entry
to include a job in the hero/stack at all (`if (!progress || !project)
continue`) - a queued job is never in `byProject` and that's correct, but
an *actively running* job briefly losing its entry would make it vanish
from the hero and the stack for that gap, worse than the stale-percent
bug this slice fixes. Replacing with a real (zero-percent) entry keeps
the job visible and correct throughout. `useRenderClock` is unaffected
either way - it keys elapsed time off a module-level map by project id,
outside `byProject` entirely, not off percent continuity.

**Side effect, and it's a good one:** a job's very first phase currently
doesn't appear in the hero until its first `progress` tick (no entry
exists before that). With this fix it appears the moment `phase_started`
fires, at 0%. More responsive, not a behavior change anyone would want
reverted.

## 2. Testing

No frontend test runner exists in this repo at all (`web/package.json`
has no `vitest`/`jest`, not even as a devDependency) - adding one is out
of scope for a bug this contained. Verification is `tsc -b` (build) +
`oxlint` clean, plus a documented manual check: start a pipeline run,
watch the transport bar cross a phase boundary, confirm it shows the new
phase's own low percentage rather than the previous phase's 100%.

## 3. Out of scope

- Adding `vitest` or any frontend test runner.
- The single last-event `progress`/`phaseStarted` reducer path - already
  correct, not touched.
- `foldPhases` (the per-phase status map behind the phase rail/checklist)
  - separate function, separate consumer, not implicated in this bug;
  not touched.
