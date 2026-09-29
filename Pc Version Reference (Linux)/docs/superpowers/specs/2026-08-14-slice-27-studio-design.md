# Slice 27 — Studio screen: nav, custom players, phase previews: Design

**Goal:** the Studio side-tab opens the latest opened project, Render and
the voice tracks play through a built-in custom player instead of the
browser's native controls, and Script/Render get a proper phase preview
that accounts for their sub-steps.

Covers backlog items #5, #8, #9, #16 - one slice because all four land in
`Studio.tsx`'s `ArtifactDetail` and its supporting artifact components
(`artifacts/bodies.tsx`, `Player.tsx`), which already branch per phase
and per sub-step in ways the other three items build directly on.

---

## 1. Studio nav → the latest OPENED project (#5)

**Not latest created, not the current take - latest opened**, per the
item's own explicit distinction. `App.tsx`'s `openProject(id)` is the one
function every navigation-to-Studio path already goes through - Home's
card click, Create's `onCreated`, "Run again"/"Resume" - so it's the
single correct place to record "this was opened," rather than
instrumenting each of those call sites separately.

- `openProject` gains one line: persist `id` to `localStorage` (a new
  `avg.studio.lastProjectId` key, the same mechanism Quick Panel already
  uses for its own UI-only preferences - Slice 4's precedent, not a new
  one).
- The Studio rail item (`Rail.tsx`) is currently a `<span aria-hidden=
  "true">` with no click handler at all - not broken, never wired.
  Becomes a real `<button>`, matching `onCreate`/`onChannels`'s existing
  shape exactly, calling a new `onStudio` passed down from `App.tsx`.
- `onStudio` reads the stored id, confirms it's still a real project
  (`projects?.some((p) => p.id === lastId)` - guards a project that no
  longer exists; archived-but-not-deleted projects still count, since
  archiving only hides a project from Home's grid, it doesn't remove it),
  and calls `openProject` with it. No stored id, or a stale one → falls
  back to Home rather than a dead click.

## 2. One custom media player, two thin wrappers (#8, #9)

**Every current native-controls location, checked directly rather than
assumed complete:**
- `Player.tsx` - the Render phase's video, `<video controls>`, kept
  deliberately native by its own docstring ("the browser's own seek bar
  works with no plumbing here") - a real reversal, not a bugfix.
- `artifacts/bodies.tsx`'s `VoiceBody` - **two** `<audio controls>`
  elements (raw narration, optimized narration - the render uses the
  optimized one).
- `PreviewPanel.tsx`'s `VoicePreview` (Quick Panel) - one more
  `<audio controls>`, the paid sample.

Three locations, effectively two element types (`<video>`/`<audio>`,
both `HTMLMediaElement`, identical APIs for play/pause/seek/volume/time).
One shared `useMediaPlayer(ref)` hook owns all of that state and every
event listener; two thin presentational components sit on top of it:

- `Player` (`Player.tsx`, name/props unchanged - `{ src }` - existing
  callers need no edit) - adds the visible frame.
- `VoicePlayer` (new) - no frame, just the transport row - used by both
  `VoiceBody`'s two tracks and `PreviewPanel.tsx`'s `VoicePreview`,
  replacing that file's own `<audio controls>` too (found while auditing
  every location, not originally scoped to Slice 24, but the same
  component this slice is building either way - fixing it once here
  rather than leaving a fourth native-controls spot standing).

Both wrappers share one markup shell: play/pause button, a seek bar
(`<input type="range">` styled, not native looks, matching this app's
"never native form controls" rule already applied to `Select`/sliders
elsewhere), elapsed/total time (`0:00 / 1:34` style - reuse
`Studio.tsx`'s existing `formatDuration` if its shape fits a running
player's elapsed/remaining display, write a small local formatter if it
doesn't rather than force a mismatch, but don't end up with two
independent time-formatting helpers for no reason), a mute toggle. `IconPlay`/`IconStop`
already exist (`Icons.tsx`) - reused for play and pause is a semantic
stretch (`IconStop` means "stop," not "pause"), so this needs two new
icons in that same file, same SVG conventions: `IconPause`,
`IconVolume`/`IconMute`. Loading/buffering and empty (no src yet) states
render inline, not as a separate component - the same state machine
already exists in each of the three native elements' own event streams
(`loadstart`/`waiting`/`canplay`/`error`), just not surfaced visually
today.

**Keyboard accessibility:** Space/Enter on the play button (native
`<button>` semantics already give this for free), Left/Right arrow keys
on the seek bar to nudge (native `<input type="range">` semantics, same
free behavior) - nothing bespoke to build for either, just don't
suppress the defaults while styling them away.

## 3. Script and Render get a proper preview (#16)

**Script - a frontend-only fix, the data already flows.** `ScriptBody`
already receives `files: TakeArtifacts` (which artifact files exist for
the selected take) as a prop, and the backend already derives exactly
this three-state split for the project list
(`avg.pipeline.phases.writing.script_substate`: pending/partial/done from
`narration.txt`/`scenes.json` presence). `ScriptBody` just never used the
partial case - today, if only `narration.txt` exists (Pass 1 done, Pass 2
segmenting), the scenes table is silently absent with no explanation.
Fix: an explicit "Draft written — segmenting into scenes…" state between
"nothing yet" and "scenes table," using the exact same `"narration" in
files` / `"scenes" in files` checks already available, matching
`script_substate`'s own three states without needing a new API call - the
frontend already has what it needs.

**Render needs one small backend addition - the same substate, scoped to
a take instead of only a project's current one.** Two facts, both traced
in the actual code, not assumed:

- `avg.pipeline.phases.render.render_substate(folder) -> "none"|"base"|
  "done"` already exists and is already exposed - but only from `GET
  /api/projects` (the list route), computed for each project's OWN
  current take. Studio needs it for whichever take is SELECTED, which
  isn't always the current one.
- During a live render, `RenderPhase.run` already scales the base
  pass into 0-70% and the composite pass into 70-100% overall
  (`_BASE_PERCENT_SPAN = 70`) specifically so two 0-100% passes don't
  read as the render restarting. That threshold, mirrored as a frontend
  constant (the same "backend owns it, frontend mirrors it with a
  cross-reference comment" pattern `phases.ts` already uses for phase
  keys - not a new convention), is enough to label which of the two
  sub-steps a live `progress.percent` belongs to without any new event
  type.

Fix: `GET /api/projects/{id}/takes` gains a new top-level
`substates: {[take_id]: {script: ..., render: ...}}` key, one entry per
take, computed via `take_root(project, take.id)` for each - the exact
same two functions the project-list route already calls, just looped
over every take instead of only the current one. `ArtifactDetail`'s
existing `phaseKey === "render"` branches (pending/progress/done/failed)
gain the base/composite distinction: pending shows what will happen
("Two steps: base video, then overlays & captions"); a live render's
progress bar or message notes which pass is active, from the mirrored
70% threshold; a finished-but-`"base"`-substate render (the composite
pass was skipped or hasn't run) says so explicitly rather than looking
identical to a fully finished one - directly answering the "why has my
video no captions" question `render_substate`'s own docstring already
names as the reason it exists.

## 4. Testing

Backend: `render_substate`/`script_substate` per-take in the takes
response - one test per state (none/base/done, pending/partial/done),
matching the existing project-list route's own test shape.

Frontend: no test runner (confirmed Slice 22) - `tsc -b` + `oxlint`
clean, plus manual checks: Studio nav opens the right project (and falls
back to Home cleanly with no prior project); the video and voice players
render real custom controls, no native chrome, in all three locations;
Script's partial state shows the "segmenting" message; Render's pending
state describes both steps, a live render's progress clearly indicates
which pass is running, and a base-only finished take says captions
weren't baked in.

## 5. Out of scope

- Any change to `remove_silences_core`/the vendored render engine itself
  - both are protected, and this slice only reads their existing outputs.
- A generalized "phase preview" system for the other seven phases -
  `PhaseArtifact`/`BODIES` already covers them; this slice extends
  `ScriptBody` and gives Render its first real preview, it doesn't
  redesign the shared shell.
