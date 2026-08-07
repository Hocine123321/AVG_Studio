# Slice 17 — The status surface: Design

**Goal:** make the Live Status pill the one place that answers "what is this app
doing, and what happened that I need to know about" — expandable on demand,
and silent about nothing the user has not acknowledged.

---

## 0. Where this sits

The user asked for thirteen things across five subsystems. They are split into
three slices **by surface**, agreed 2026-08-07:

| Slice | Surface |
|---|---|
| **17 (this)** | The status pill, the activity feed behind it, and the error read path |
| **18** | The floating "Quick" panel — Live List moved into it, Quick Settings, Preview rehomed, Recent Errors, the disabled "In work" tab — plus the Live List tag filter |
| **19** | Settings — categories, the global/channel toggle, channel setting scope and inheritance, polish — plus the small fixes: the research-topic toggle, removing the phase-error "Run again", making wizard settings actually override the channel, and the "A" logo becoming the main menu |

### A boundary call inside this split

**Recent Errors' tab lands in Slice 18, not here.** A tab needs the panel it
lives in, and the panel is Slice 18's whole subject. Slice 17 delivers the data
path that tab consumes. That path is mostly already built: `GET /api/errors`
exists today and returns recent failures newest-first, with `POST` to record
one and `DELETE` to clear the log (`avg/api/routes_settings.py`). Nothing new
is stored.

### A decision recorded here for Slice 19, so it is not lost

The user was shown that Tuning item 3 — "when a channel setting is left blank,
take the value from the global settings preset" — is the per-field implicit
inheritance `CLAUDE.md` bans, having named it the cause of three legacy bugs.
They chose it anyway, knowingly, over an explicit-ownership alternative.

**It will be built as asked, with one mitigation and one obligation.**

- *Mitigation:* "blank" means **the key is absent from the channel document**,
  never `value == ""`. The specific ambiguity that generated the legacy bugs
  was an empty string being indistinguishable from "not set"; keeping absence
  as the only signal removes it while delivering exactly what was asked for.
- *Obligation:* `CLAUDE.md`'s "Config scopes (no merge layer)" section is
  rewritten **in the same commit as the code that changes it**. This repo has
  already paid twice for a standing rule that disagrees with its
  implementation, and the rule file is what makes a resumed session possible.

Slice 19 owns both. They are written down here because this is the spec that
exists at the moment the decision was taken.

---

## 1. What is wrong today

The pill has three states — `error`, `busy`, `ready` — derived from a single
`activeLead` (`web/src/StatusBar.tsx`). It has no memory: anything that happens
and then stops happening leaves no trace on it. A render that failed, a video
that finished, a provider key that was rejected — each is visible only while it
is happening, and only if the user is looking.

It also has no detail. The pill can say "Rendering", but not what phase, not
which project among several, and nothing at all about the operations that are
not renders: a Live List refresh, a provider probe, a model-suggestion run.

## 2. Architecture: one typed activity event

**A new `ActivityEvent` in `avg/core/events.py`**, carrying:

- `activity_id` — stable for the life of one operation, so updates fold rather
  than accumulate
- `label` — what to show the user, written by the emitter
- `percent` — `0-100`, or `None` for work with no measurable progress
- `state` — `running` | `done` | `failed` | `cancelled`
- `detail` — optional; the one line the panel shows under the label

**Every long-running operation emits it** over the existing `/ws/events`
stream: the pipeline runner, `MediaEngine.render`, the Live List refresh, the
provider probe, and the model-suggestion run.

This is deliberately an extension of a rule the repo already has. `CLAUDE.md`
says wiring in a new long job means writing another generator rather than a new
mechanism; this extends that from *doing* the work to *reporting* it. **A
future long-running operation appears in the panel by emitting, with no change
to the pill at all.** That is the requirement "any other long-running operation
the app is currently doing" actually asks for, and it is the reason this is not
built by stitching together the sources the client already happens to have.

**The client folds them once** into an `activity` map keyed by `activity_id`,
beside the existing `byProject` map in `web/src/useEvents.ts`. A terminal state
(`done`, `failed`, `cancelled`) removes the entry from `activity` and, when the
event is sticky, appends it to the sticky queue.

### What this does not do

It does not replace `byProject`, the `/api/jobs` poll, or `JobRegistry`. Those
answer "which projects have work in flight", which is a different question from
"what is happening right now", and Slice 16 built them deliberately. The
activity map is additive.

## 3. The sticky queue

Sticky events are held in an ordered list, **newest first**. The pill renders
`queue[0]`, and shows `1 of 3` when others wait behind it. Pressing `x`
acknowledges the head and reveals the next. **The pill returns to idle only
when the queue empties.**

**Sticky** — the pill waits for acknowledgement:

- a pipeline or phase error
- a render failure
- a video completed successfully
- a job that was queued behind another and has now started
- a job cancelled
- a provider or API-key failure: auth, quota, or rate limit
- an export finished (CapCut or mp4)

**Not sticky** — auto-returns to idle:

- routine progress
- a Live List refresh
- provider probes where everything passed

The queue is **client state keyed by event id**, not a socket subscription. A
dropped connection therefore cannot silently eat a completion the user needed
to see, and a reconnect does not re-fire acknowledged events.

## 4. The pill: states and motion

**Docked (idle).** A thin sage sliver flush with the right edge — no glyph, no
text, wide enough to be an obvious click target. `IconCheck` leaves the pill
entirely, which is the "remove the ✓ from the screen" request; the icon itself
stays in `Icons.tsx` for other callers.

**Active.** The pill slides out from the edge. A chevron (`>` / `<`) toggles a
detail panel that **anchors to the pill and overlays the page** rather than
pushing it, so expanding never reflows the content underneath.

**Finishing.** A grey overlay marks the pill inactive, then it slides back to
docked — unless a sticky state is waiting, in which case it stays out.

**Motion discipline.** Every transition multiplies its duration by
`var(--motion-scale)` and is disabled in the existing `prefers-reduced-motion`
block, which stays authoritative. A frozen element must remain **visible**:
Slices 13 and 14 each shipped a bug where a paused element sat outside its own
`overflow: hidden` clip, and `theme.ts` stamps `data-motion="none"` precisely
so the frozen state is expressed by an attribute rather than by arithmetic on a
zero scale.

**Design language.** The pill is status, never an action: no `.btn--primary`,
and `pointer-events` stay off everywhere except the chevron and the `x`. Sage
means settled; the pulsing halo, never colour alone, marks live work. All
values come from `web/src/tokens.css`.

## 5. Detail panel content

One row per entry in the `activity` map, each showing `label`, `percent` when
present, and `detail`. Above them, when the sticky queue is non-empty, the head
event with its `x` and its `1 of 3` counter.

The panel composes nothing itself — it renders what the fold produced. That is
what keeps a future operation from needing to touch it.

## 6. Testing

**Backend, and mandatory:**

- Each emitter produces a well-formed `ActivityEvent` with a terminal state.
- **A failed or cancelled operation still emits its terminal event.** An
  operation that dies without one would leave a row in the panel forever — the
  same class of defect as Slice 16's Transport bar sticking at 100%, which
  happened because no terminal event existed.
- `activity_id` is stable across an operation's updates.

**Frontend** has no unit tests by convention: verified by `npm run build`,
`npm run lint` at zero warnings, DOM and computed-style measurement against the
dev server, and stated reasoning. The queue's ordering and acknowledgement are
measured, not assumed.

**A visual pass is owed** and has not been possible in any recent session — the
Browser pane does not composite on this machine. It is recorded as pending
rather than claimed.

## 7. The risk worth stating

The pill becomes the single surface for connection state, live progress,
errors, and acknowledgement. That is a lot of jobs for one control, and
`CLAUDE.md` calls it the most important element in the app.

If it proves overloaded, **the cheapest thing to cut is the detail panel's
breadth** — the sticky queue and its guarantee are what carry the value. The
panel is a convenience over an activity map that would still be correct without
it.
