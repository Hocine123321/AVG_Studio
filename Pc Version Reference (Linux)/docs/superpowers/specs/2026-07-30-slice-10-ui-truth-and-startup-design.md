# Slice 10 — The app stops fighting you

**Goal:** make the screens honest and quick to use — one primary action per
screen, a settings page you can navigate, a provider test that says exactly
what it proved, dependencies the app can fix where fixing is possible, and a
window that appears at once.

**Date:** 2026-07-30. Every measurement below was taken against the running app
on that date, not estimated.

Companion slice: this is the visible-UI half of the eighteen items raised on
2026-07-29. The correctness half shipped as Slice 9
(`docs/superpowers/specs/2026-07-29-slice-9-render-truth-and-trace-design.md`).

---

## 1. What the audit found

Three of the nine items turned out to be defects with a mechanism, not matters
of taste.

**Home breaks the one-CTA rule thirteen times over.** Counted in the DOM:
thirteen `.btn--primary` elements on one screen — `Resume`, `Open`, `Play`, one
per card. `CLAUDE.md` calls one filled terracotta action per screen
non-negotiable and records that four previous tasks had to fix over-use of it.

**The duplicated scrollbar has an exact cause.** `.screen-enter` runs
`rise-in`, which animates `transform: translateY(6px)`
(`web/src/tokens.css:2783-2792`), and the class sits on
`.app-shell__content` — the scroll container itself
(`tokens.css:408`, `overflow-y: auto`). A transformed box contributes its
shifted position to its ancestor's scrollable overflow, so for the animation's
0.18s the document is 6px taller than the viewport and a second scrollbar
appears next to the real one, then vanishes. Measured live: document
`scrollHeight` 726 against `clientHeight` 720, with the only genuine scroller
being `.app-shell__content` (720 vs 1231).

**Settings is nine viewports of scroll with no navigation.** Measured:
`scrollHeight` 6584 in a 720px viewport, ten groups, 56 settings, and no way to
jump to one.

Two smaller facts, both verified rather than assumed:

- Thumbnails are not broken. `GET /api/projects/{id}/thumbnail` returns 200 for
  every project that has images (4–7 KB PNGs) and an honest 404 for the ones
  that never generated any. What the user sees on those cards is a grey
  placeholder icon where a sentence would be clearer.
- Startup is entirely import-bound. `create_app` takes **42 ms**; importing
  `avg.shell` takes **~1.6 s** cold, of which `fastapi` is 0.42 s, `uvicorn`
  0.27 s and `avg.api.app` 0.72 s cumulative (`python -X importtime`).

---

## 2. Decisions

Each chosen from stated alternatives during brainstorming.

1. **Library: a consistency pass, not a rebuild.** The grid stays; the card's
   action stops being filled; cards follow their project's aspect ratio.
2. **Settings: a sticky group nav plus a search field.** Not one-group-at-a-time,
   not collapsible sections.
3. **Test Providers: three verdicts.** A real completion for ticked text models;
   a free reachability check for image and voice providers; nothing that costs
   money is ever probed.
4. **Auto-install: install where it can, explain where it cannot.** Never a
   private package directory on `sys.path`.
5. **Startup: window first, server second, behind a themed splash.** Not a
   micro-optimisation pass over the import graph.
6. **Script panel: a scene table with the narration behind a disclosure.**

---

## 3. Architecture

Nine independent pieces. Seven are frontend-only; `Test Providers` and
auto-install add backend surface; the startup change is `avg/shell.py` alone.
Nothing here changes the settings catalogue's role, the phase registry, or any
file a phase writes.

### 3.1 Home: one action, honest cards

- **One filled action.** Every card's button becomes `.btn`. `New video` is the
  only `.btn--primary` on Home. The card's state keeps being carried by its
  existing chip — sage for done, amber for running, red for failed — which is
  colour the state has already earned and which no longer competes with a
  button for the same meaning.
- **Aspect-aware cards.** The thumbnail frame takes the project's own
  `aspect_ratio` (already in `GET /api/projects`), so a 16:9 project shows a
  16:9 still instead of being cropped into the same 212×248 portrait box as a
  9:16 one. Rows therefore stop being a uniform height — a deliberate departure
  from the mockup, taken because cropping a widescreen frame into a portrait
  card misrepresents the video the card stands for.
- **A missing thumbnail says so.** Where `thumbnail` 404s, the card shows a
  short sentence ("No images yet") in the muted tone rather than a grey icon
  that reads as a broken image.
- **The status chip stops colliding.** The long `Failed · render` chip and the
  aspect chip no longer share a corner; the status chip owns the top-left, the
  aspect chip the top-right.

### 3.2 Settings: navigable and searchable

- A left column lists the ten groups, sticky while the right side scrolls, and
  marks the group currently in view.
- A search field filters fields by label and help text across every group;
  groups with no match disappear from both the page and the nav while a query
  is active.
- **Derived, never restated.** The nav's entries come from the distinct `group`
  values in `GET /api/settings/schema`, so adding a `SettingSpec` still costs
  exactly one entry and appears in the nav, the search and the page with no
  further edit. The group labels already in `Settings.tsx`'s `GROUP_LABELS` stay
  as display polish over whatever the schema reports, exactly as now.
- No native form control anywhere: `Select.tsx` for choices, `.switch` for
  booleans, as the standing rule requires.

### 3.3 Test Providers: three verdicts, one of which costs money

`POST /api/models/cache/build` and its "Build model cache" button become
**Test Providers**, and the sweep changes shape:

| Kind | What runs | Verdict |
|---|---|---|
| Text | A real minimal completion per **ticked** model (`probe.probe_provider`) | `working` / `failed` |
| Image | The provider's own catalogue fetch (`catalogues.list_image_models`) — no generation | `reachable` / `unreachable` |
| Voice | The provider's own voice list (`catalogues.list_voices`) — no synthesis | `reachable` / `unreachable` |

Three states reach the UI and each is stated in words, never implied:

- **Working** — a real call succeeded. Text only.
- **Reachable** — the provider answered with this key; *this model* is unproven.
- **Untested** — never checked.

Two rules this preserves:

- **Nothing that costs money is probed.** An image probe is a paid image and an
  ElevenLabs probe is paid characters, which is exactly why Slice 8 made images
  and voices *listed, never probed*. Reachability is a catalogue GET, which both
  providers already serve for free.
- **Reachability is a fact about a provider, not a model**, so it is stored once
  per provider in `working_models.json` beside the per-model text verdicts —
  machine-owned, replaced wholesale on each sweep. The user's ticks, favourites
  and notes stay in `model_prefs.json` (Slice 8's separation, unchanged).

**What gets touched, precisely.** Text: every model the user ticked, and only
those — a sweep that tests everything a provider advertises is what the Live
List exists to replace. Image and voice: one catalogue fetch per *provider that
has at least one ticked entry*, since reachability is a provider-level fact and
a provider nobody shortlisted is not worth a call. **Untested** is the absence
of a stored verdict, not a stored value: a provider that has never been swept,
or one whose ticks are all new since the last sweep, reads untested because
`working_models.json` has nothing to say about it.

### 3.4 Dependency auto-install

The dependency card gains an **Install** action per missing row, offered only
when the app runs against a real interpreter — a dev checkout or a venv,
detected by `sys.frozen` being absent. It shells `pip install <package>` for
that one package and streams the output into the card.

In a frozen build the same row explains instead: a required dependency missing
*there* is a packaging bug in the build, not something a user should paper over
at runtime. That is the `pydub` lesson from Slice 9 — where the real fix was to
bundle it and correct `health.py`'s claim that it was optional — and it is why
no private package directory is put on `sys.path`: an app running a mix of
bundled and downloaded code is the hardest kind of "works on my machine" to
debug, and it needs the network at exactly the moment something is already
wrong.

### 3.5 Startup: window first

`avg/shell.py` opens the pywebview window immediately on a small inline page in
the app's own colours, then imports `fastapi`/`uvicorn`, builds the app, starts
the server on its daemon thread, and points the window at the real URL.

The number that improves is **time to window**: today it is behind ~1.6 s of
imports, and afterwards it is roughly the cost of starting Python and
pywebview. The imports still happen — they are what the server needs — but
behind a window that already looks like the app rather than behind nothing.

The splash is inline HTML in `shell.py`, not a file: it must render before
anything else is imported, and a page that needs the server running to load
would defeat its own purpose. It carries the app's canvas colour, its name, and
nothing that moves except a single pulse, so a slow machine does not show a
spinner that outlives its welcome.

### 3.6 The pill, the Live List, the Script panel, the flash

**Status pill.** While a job runs, the static dot becomes an indeterminate
ring — one arc rotating, the shape a user already reads as "working". The word
`Ready!` slides into the ring as it spins up and pops back out when the job
ends. The running label stays the single word `Working`: percent and phase
belong to the Transport bar, and Slice 8's reasoning holds — the same number in
two places at two update rates is how a UI starts contradicting itself.
`prefers-reduced-motion` gets a static ring, matching how `tokens.css` already
disables `rise-in` and the card transitions.

**Live List.** A search field filtering rows by id, provider, note, language
and gender. Edge advertises 322 voices; building a shortlist out of them is the
entire point of the screen, and today it is done by scrolling.

**Script panel.** The scene list becomes the same `.artifact-table` Director II
uses — number, text, label, image prompt — and the full narration moves behind a
`Narration` disclosure. The scene texts already contain every word of the
narration; printing the whole script twice at full height is what makes this one
panel feel unlike its neighbours.

**The flash.** `rise-in` moves off the scroll container. The animated element
becomes a wrapper *inside* `.app-shell__content`, so a transform can no longer
grow the document's scrollable overflow. The animation itself is unchanged, and
`tokens.css`'s reduced-motion block keeps disabling it.

---

## 4. Testing

The frontend has no unit tests by convention — it is verified by looking, and
this slice is mostly frontend, so the looking is the test and it is enumerated
in §5 rather than left to judgement.

Python covers the two pieces that have logic worth asserting:

- **The reachability sweep** reuses `avg/providers/catalogues.py` and stays on
  recorded fixtures. `tests/conftest.py`'s `AVG_REPLAY_FIXTURES=1` stands: no
  live call, and in particular no generation call, can happen in the suite.
  Tests assert that a ticked text model gets a completion, that an image or
  voice provider gets only a catalogue fetch, and that an untouched provider
  reports `untested` rather than `unreachable`.
- **The install action** is tested for what it refuses: it must not offer itself
  when `sys.frozen` is set, and when it does run it must shell the exact
  `pip install <package>` for one named package and nothing else.
- **Startup** gets one test on the seam rather than on the clock: the splash is
  inline HTML built by a pure function in `shell.py`, so the test asserts that
  function needs no server, no `create_app` and no settings on disk to produce
  its page — which is the property that lets the window open first. The timing
  itself is measured by hand and recorded in `.superpowers/sdd/progress.md`,
  because wall-clock assertions in a suite are a flake waiting to happen.

Target: **746 tests still passing** (Slice 9's figure), mypy no worse than
**687**.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 746.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Home at 1280×820 and 900×700, both themes, on a library holding both 9:16 and
  16:9 projects: exactly one filled terracotta button on the screen, widescreen
  thumbnails not cropped into portrait, and a project with no images reading as
  a sentence rather than a grey icon.
- Settings: the nav marks the group in view while scrolling, clicking a group
  jumps to it, and a query filters both page and nav. No native control
  anywhere — `grep -n "<select\|type=\"checkbox\"" web/src/` stays clean.
- Change screens five times: no second scrollbar appears at any point.
- Press render: the pill's ring spins, `Ready!` has slid into it, and it pops
  back when the job ends. With reduced motion on, the ring is static.
- Live List: a query narrows 322 Edge voices to a handful.
- Test Providers: text rows come back `working`/`failed`, image and voice rows
  `reachable`, an untouched provider `untested`; nothing generated an image and
  no ElevenLabs characters were spent (checked against the take log, which
  records every call).
- The dependency card offers Install in the dev checkout, installs one package,
  and turns that row green. In `AVG APP/` the same row explains instead.
- Time to window, measured before and after, recorded in
  `.superpowers/sdd/progress.md`.
- `"Build Installer.bat"` — the packaged app does all of the above.

---

## 6. Out of scope

Deliberately not in this slice:

- The card-background timing defect Slice 9 left open (`avg/media/overlays.py`
  places a layer at scene-start + duration because Director II draws assets
  before Screen Text resolves each card's window). It is a render-correctness
  bug, not a UI one, and belongs with the pipeline.
- Toasts and the device-frame player, the Slice 6C leftovers.
- The codebase-simplification refactor, which has its own plan pending.
- A list view, sorting, or status grouping for the library. The grid stays; if
  50 projects make it unwieldy, that is a later slice with its own evidence.
