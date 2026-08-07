# Slice 19 — Settings, scope, and the small fixes: Design

**Goal:** make Settings navigable, let a channel own only the settings it cares
about and inherit the rest, and clear the four small defects that have been
irritating in the meantime.

---

## 0. Where this sits

Last of the three slices agreed on 2026-08-07. Slice 17 owns the status pill;
Slice 18 owns the Quick panel and **takes the Live List opener and the Preview
panels out of Settings**. This slice cleans up what is left and must not put
either back.

**This slice changes a documented invariant.** That is unusual enough to state
at the top, and §2 is where it is argued.

---

## 1. Categories, and the page's shape

The catalogue declares ten groups today (`app`, `creative`, `video`, `captions`,
`overlay`, `voice`, `images`, `render`, `models`, `keys`), rendered as one long
scrolling page with a nav that scrolls to each. That is a flat list pretending
to be structure, and with the Live List and the Preview panels gone it is
mostly form fields.

Group them into **categories**, and rename the tabs to the category names.
Categories are a presentation layer over `SettingSpec.group`: a category owns an
ordered list of groups, and a group belongs to exactly one category. No setting
moves, no `SettingSpec` changes, no migration.

The category table is **one constant in one file**, beside `GROUP_LABELS`. A
group missing from the table is a failure a test catches, not a group silently
dropped from the page — settings that the UI never shows have been deleted
before for looking unused.

## 2. Channel setting scope — the invariant that changes

### 2.1 What was decided, and by whom

`CLAUDE.md` currently says, of `scope="channel"` settings:

> Never both: no field is ever resolved by looking in one and falling back to
> the other, and there is no absent-means-inherit. Three bugs in the legacy app
> came from per-field implicit inheritance, which this shape has no room for.

The user was shown that Tuning item 3 — "when a channel setting is left blank,
take the value from the global settings preset" — *is* that mechanism, together
with an explicit-ownership alternative that reaches the same goal without it.
**They chose per-field inheritance, knowingly.** It is built as asked.

### 2.2 The mitigation that makes it survivable

**"Blank" means the key is absent from the channel document. Never
`value == ""`.**

The legacy bugs came from an empty string being indistinguishable from "not
set": a user who deliberately cleared a field got the global value back and had
no way to express "no, actually empty". Keeping *absence* as the only
inheritance signal removes that ambiguity entirely while delivering exactly what
was asked for. An empty string stored against a key the channel owns is a real,
respected value.

### 2.3 One resolver, and why that is not optional

Resolution becomes: **the channel's own document if it has the key, otherwise
the app-wide defaults document, otherwise `SettingSpec.default`.**

That must live in exactly one function, and **every reader must go through it**.
This is the whole risk of the change: a fallback chain is only safe while there
is one copy of it. Two copies diverge, and a setting then means different things
depending on which route asked — which is not a hypothetical, as §5.2 shows.

### 2.4 What Addition 5 becomes

The user also asked to choose which settings a channel owns. Under §2.3 that is
not a second feature — it is **the UI for producing blankness**. Taking
ownership writes the key; releasing it deletes the key. One mechanism, two
verbs, and the ownership state is derivable from the document rather than stored
alongside it, so the two can never disagree.

Settings shows an owned setting as editable, and an inherited one showing the
value it inherits, greyed, with a one-press "take ownership".

### 2.5 `settings_source` — a Slice 12 concept this collides with

Slice 12 built `settings_source` (`"channel"` | `"global"`), meaning *read this
channel's whole document, or read the app-wide defaults document whole*. Per-key
inheritance is a second mechanism for the same outcome, and two mechanisms for
one outcome is how the resolution order becomes something you have to reason
about at every read.

**Proposed: `settings_source` goes away.** A channel that owns no settings
inherits everything, which is what `"global"` meant. `POST
/api/settings/port-to-global` stays — snapshotting a channel's values into the
defaults is still a real action, and it is now also how a user seeds the
defaults before releasing ownership across the board.

**This removes a concept the user approved eleven days ago, so it is flagged
rather than assumed.** If `settings_source` stays, §2.3's resolver has to
special-case it, and the "no field resolved by looking in two places" sentence
becomes untrue in two different ways instead of one.

### 2.6 The obligation

`CLAUDE.md`'s "Config scopes (no merge layer)" section is rewritten **in the
same commit as the code that changes it**, and says plainly what the new rule
is, that absence is the only inheritance signal, and that one resolver serves
every reader. This repo has twice paid for a standing rule that disagreed with
its implementation. The rule file plus `git log` is what makes a resumed session
possible.

## 3. The Global / Channel toggle

A two-state toggle beside the "Settings" title, switching which document the
page edits: the **app-wide defaults** or the **active channel**. Built from the
existing `.switch` vocabulary — never a native control, never a dropdown.

In channel mode the page shows ownership state per §2.4. In global mode it edits
the defaults document directly, and ownership does not apply because there is
nothing above it to inherit from.

The toggle is disabled, with a reason, when no channel is active — the same
condition that already makes channel-scoped saves a 400.

## 4. Polish

Scoped deliberately, because "clean it up" is unfalsifiable: consistent field
row rhythm, one heading treatment per level, group descriptions where a group's
purpose is not obvious from its name, and the disabled/inherited treatments from
§2.4 reusing the existing disabled tokens rather than new ones.

## 5. The small fixes

### 5.1 Remove "Run again" from the phase-error surfaces

`web/src/Studio.tsx` has two buttons inside `artifact-failed__actions` that call
`onStartRender()` directly. Both start a render regardless of which phase failed,
skipping everything in between — exactly the complaint. "Continue" already does
the correct, phase-aware thing.

**Both go.** What stays: the take strip's "New take", the per-phase "Run again"
that opens the new-take panel, and the header CTA, which was already made
phase-aware and reads `Run again · <phase>`.

### 5.2 The aspect ratio follows the video form

This was originally written up as "wizard settings are ignored, the channel
wins". **That diagnosis was wrong**, and the user corrected it: the override
mechanism works. The real defect is narrower and worse.

`video_form` and `aspect_ratio` are two independent settings that nothing ties
together:

- `video_form` (`shortform` | `longform`) is read in exactly one place —
  `avg/pipeline/phases/_common.py`, which expands it into the brief and script
  prompts. **It shapes the writing and nothing else.**
- `aspect_ratio` (`9:16` | `16:9` | `1:1`) is read by
  `avg/pipeline/create.py`, which sets the project's actual frame.

So choosing long-form produces a long-form *script* rendered into a 9:16
*frame*, and the only way to get a wide video is to know that a second,
separate setting also has to be changed. The form's own help text says
"Short-form (9:16 vertical) or long-form (16:9 horizontal)" — it already claims
to decide the ratio, and does not.

**The fix: remove `aspect_ratio` from the catalogue and derive it from
`video_form`.** `shortform → 9:16`, `longform → 16:9`. One setting, one
meaning, and the help text becomes true.

#### What this costs, deliberately

**`1:1` becomes unreachable.** It is a current choice with no corresponding
form, so deriving from the form drops it. That is a real capability removal, not
an oversight — flagged here so it is a decision rather than a surprise. If
square output matters, the honest shape is a third form, not a second setting.

#### Three consequences to build, not discover

1. **Invalidation is already correct, by luck — confirm it rather than assume
   it.** `aspect_ratio` invalidates `visuals`; `video_form` invalidates `brief`.
   Since `brief` is phase 0, changing the form already sweeps every later phase
   including `visuals`, so images are not reused at the wrong ratio. A test must
   pin this, because it is currently a coincidence of ordering rather than
   anything the code states.

2. **Old takes carry the dead key.** `avg/api/routes_takes.py` both reads
   `take.settings_snapshot["aspect_ratio"]` when restoring a take and writes it
   when creating one. Snapshots taken before this change will still contain it,
   and a take must keep opening. Read it if present, stop writing it, derive it
   when absent.

3. **Existing channel documents keep an inert `aspect_ratio` key.** Harmless —
   `_channel_values` iterates the catalogue, so a key with no spec is simply
   never read — but worth stating so nobody removes it in a panic later.

#### One observation, not a diagnosis

While investigating, it emerged that `create_project` folds overrides onto
`channel.settings` while the Settings screen resolves through `_channel_values`,
which reads `channel_defaults.json` whole when a channel follows global. Those
two can disagree about what the user is even looking at. **No evidence links
this to any reported symptom**, and it is recorded only because §2.3's single
resolver removes the divergence as a side effect.

### 5.3 "Research topic first" toggle

A boolean on the New Video page. It needs a reader before it is a setting rather
than a switch that does nothing — three settings were deleted on 2026-07-28 for
exactly that. **If nothing consumes it yet, it belongs to the slice that builds
the research step, not this one.** Flagged: the batch did not say what it does.

### 5.4 The "A" logo becomes the main menu

`web/src/Rail.tsx` renders the logo as `<div className="rail__logo"
aria-hidden="true">A</div>` and the first rail item as a separate "Your videos"
button. The logo becomes that entry, and the separate button goes.

**It must become a real `<button>` with an accessible name.** It is
`aria-hidden` today because it was decoration; a decoration that is also the
app's primary navigation is unreachable by keyboard and invisible to a screen
reader.

## 6. Testing

**Backend, and mandatory:**

- The resolver: key present in the channel wins; key absent falls to defaults;
  absent from both falls to `SettingSpec.default`; **an empty string stored
  against an owned key is returned as an empty string, not inherited** — §2.2's
  whole point, and the one test that would have caught the legacy bugs.
- Taking and releasing ownership writes and deletes the key, and touches no
  other key.
- **Every reader goes through the one resolver.** A drift test, in the spirit of
  the `PHASE_ARTIFACTS` / `artifacts.py` test that already exists, because §2.3
  is safe only while that is true.
- The category table covers every group in the catalogue.
- Per §5.2: a long-form channel produces a `16:9` project and a short-form one
  a `9:16` project, straight from `new_project`; changing `video_form`
  invalidates `visuals` and not merely `brief`; and a take snapshot still
  carrying an `aspect_ratio` key still opens.

**Frontend**: `npm run build`, `npm run lint` at zero warnings, DOM and
computed-style measurement — no native control, one `.btn--primary`, the
inherited treatment reusing existing disabled tokens, the rail logo exposing an
accessible name.

**A visual pass is owed** across Slices 17-19 and has not been possible recently;
the Browser pane does not composite on this machine. Recorded as pending rather
than claimed.

## 7. Risks

**The resolver is the whole slice's risk.** A fallback chain with one
implementation is a feature; with two it is the defect `CLAUDE.md` warned about,
and the second copy will not announce itself. The drift test is not optional.

**Ownership is a new idea for the user to hold.** "Why is this field grey" has to
be answerable from the screen itself, without documentation.

**`settings_source`'s removal (§2.5) is unresolved** and blocks nothing until
implementation, but the resolver's shape depends on the answer.
