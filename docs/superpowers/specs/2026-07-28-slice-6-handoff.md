# Slice 6 — handoff brief

**Written 2026-07-28**, 

Every fact below was checked against the code. That is worth stating
precisely, because the first draft of this document *also* claimed to be
fully verified and a review then found three errors in it — a per-phase
re-run described as missing when it is shipped, a count of dead theme
choices that was off, and `partial` called a fourth status when it would be
a fifth. All three are corrected below and flagged in place. **Check
anything you are about to build on.** This document is a starting point, not
an authority.

---

## 1. What this project is

**AVG Studio** — a Windows desktop app that turns a topic into a short-form
(9:16) or long-form (16:9) video: AI writes a brief and script, generates one
image per scene, synthesises narration, plans motion and captions, and
renders an mp4.

It **replaces** a PowerShell 7 CLI that still lives at `D:\AI_Video_Gen`
(read-only reference, ~20,000 lines). The rewrite is at `D:\AVG_Studio`.

- Python engine, FastAPI on `127.0.0.1` with a per-session token, React 19 UI
  in WebView2 via `pywebview`. One process.
- Rendering runs in a **child process** calling a vendored copy of the old
  app's render engine.
- **537 tests pass.**

See `CLAUDE.md` at the repo root for commands, architecture and the
non-negotiable rules. This document covers only what Slice 6 needs on top.

### Run it

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

```bash
.venv-build/Scripts/python.exe tools/_dev_serve.py
```

Then `http://127.0.0.1:8756/?token=devtoken`. Kill anything on 8756 first,
and run `npm run build` in `web/` first — the dev server serves `web/dist`.

```bash
"Build Installer.bat"
```

**`.venv-build\Scripts\python.exe` is the interpreter for everything.**

---

## 2. Where the code is now (read this before searching for a file)

A file consolidation landed on 2026-07-28 (−17 files). Anything written
before that date — including every Slice 1–5 plan and report under
`docs/superpowers/` and `.superpowers/sdd/` — names module paths that no
longer exist. Those documents are historical records and were deliberately
**not** rewritten. Translate as you read:

| Was | Is now |
|---|---|
| `providers/text/{groq,huggingface,pollinations}.py` | `providers/text/community.py` |
| `providers/images/{huggingface,pollinations,stablehorde}.py` | `providers/images/community.py` |
| `providers/images/{gemini,openai}.py` | `providers/images/premium.py` |
| `providers/voice/{edge,elevenlabs}.py` | `providers/voice/providers.py` |
| `media/{ffmpeg_locate,workspace}.py` | `media/environment.py` |
| `config/settings_import.py` | folded into `config/settings.py` |
| `pipeline/phases/{_parsing,_shared}.py` | `pipeline/phases/_common.py` |
| `pipeline/phases/{brief,script}.py` | `pipeline/phases/writing.py` |
| `pipeline/phases/{director1,director2}.py` | `pipeline/phases/director.py` |
| `web/src/fields/{Bool,Choice,Float,Int,Path,Text}Field.tsx` | `web/src/fields/Fields.tsx` |
| `web/src/fonts.css` | top of `web/src/tokens.css` |

**Phase ids, keys and class names did not move.** `REGISTRY` still reports
`0 brief / 1 script / 2 director1 / 3 visuals / 4 voice / 5 director2 /
6 captions / 7 render`, and the strings `director1`/`director2` in
`settings_schema`'s `invalidates`, `legacy_import`'s phase map and
`takes.py`'s notes are phase **keys**, not module names. Do not "fix" them.

The reasoning behind every merge, and the seven that were rejected (four
would have closed import cycles), is in
`docs/superpowers/plans/2026-07-28-file-consolidation.md`. Read its
**"colliding module-level globals"** section before merging any two modules
again — that hazard is silent, and no test catches it.

---

## 3. What is already built (Slices 1–5)

| | |
|---|---|
| **Installer** | One `AVG-Setup.exe`, per-user, no UAC. Verified to launch with `PATH` stripped of Python and ffmpeg |
| **Config** | `settings.json`, per-channel documents, DPAPI-encrypted keys, a settings schema that renders the settings UI |
| **Providers** | 4 text backends + router with per-phase routing, 5 image providers, 2 voice providers — one error taxonomy, one retry policy, all tested against recorded HTTP fixtures |
| **Pipeline** | 8 phases, brief → script → director I → visuals → voice → director II → captions → render |
| **Takes** | Re-run with a change; only invalidated phases redo. Survives restart |
| **Export** | CapCut folder: images, narration, `.srt`, `timing.csv`, reference cut |
| **Screens** | Welcome, Home, Studio, Settings, Channels, Create |

`.superpowers/sdd/progress.md` is the running ledger of every decision and
defect. Sessions have died mid-slice six times; that file plus `git log` is
what makes resuming possible. **Trust it over recollection** — but note it
is gitignored and local to this machine only.

---

## 4. Slice 6 — what to build

The user supplied **`F:\Downloads\organic_night_v2.html`** (69 KB, a working
HTML mockup): a reworked layout plus six features lifted from the original
PowerShell app. **The palette is unchanged** — same terracotta, sage,
Caprasimo, both themes. Read the mockup; it is the design authority.

### 4a. The restyle (mechanical, low risk)

| Change | From → to |
|---|---|
| Rail width | 58px → **74px**, items 48px at 16px radius, 4px accent bar at `left:-13px` |
| Radii | larger; `--radius-lg` on cards |
| **Shadows** | none → `--shadow-sm/md/lg` **plus a card hover lift** (`translateY(-2px)`) |
| Grid | fixed columns → `repeat(auto-fill, minmax(var(--cardmin), 1fr))` |
| Home header | adds a kicker + 38px Caprasimo title |
| New chrome | toasts (`.toast`), search box, device-frame player |

**Still unanswered, and it blocks the restyle:** the current app
deliberately uses **no shadows** — depth comes from a 1px top-edge
highlight. The mockup uses both shadows and a hover lift. That is a change
of principle, not a tweak. It was raised on 2026-07-26 and again in the
session that produced this document; no answer either way. **Ask before
starting 4a.**

### 4b. The six features

Five of the six need backend work that does not exist. **Item 5 does not** —
it is largely shipped already and needs re-scoping rather than building; see
below.

**1. Models table** — columns `Phase | Model | Backup | If both fail | Cost per video`.
`avg/providers/text/router.py` already supports per-phase routing; **there is
no setting for it**, so the capability is unreachable. Confirmed: the only
provider/model settings in the catalogue are `captions_provider`,
`voice_provider`, `voice_model_id`, `image_provider`, `image_model` — nothing
per-phase for text. Highest-value item: it lets a cheap model do
segmentation and a strong one write the brief. Needs per-phase model
settings in `avg/core/settings_schema.py`, a backup field, and a failure
action (`stop` / `switch provider` / `fall back`).

**2. Spend card** — sums completed and in-progress phase costs across
projects. **Nothing tracks cost anywhere today** — confirmed: no cost, price
or token-count field exists in `avg/core/models.py` or the settings
catalogue. Needs a real design decision before any code: where do prices come
from (a table you maintain? provider APIs?), what unit is recorded (tokens?
images? seconds?), and where it is stored (on `PhaseRecord`, probably).

**3. Components / health** — FFmpeg, Pillow, moviepy rows with state, a
recheck action, "Download failed", a health cache. This is the old app's
Check Health; no health module exists. **Worth real weight:** it would have
told the user their Gemini account was at zero quota before a video failed.

**4. Bulk select and archive** on Home — `.select-bar`, `bulkArchive`,
`bulkClear`, an `Archived` state. Confirmed: `ProjectDoc` has no `archived`
field. Needs the flag, persistence, and Home selection state.

**5. Run again per phase** — **mostly already built; re-scope this item
before doing anything.** A per-phase re-run *is* exposed today: `POST
/api/projects/{id}/takes` accepts `reroll_from` (`avg/api/routes_takes.py`,
`_build_plan`), and `web/src/NewTakePanel.tsx` renders a "Re-roll a phase"
dropdown over the full `PHASE_ORDER`. What that path does is treat the
chosen phase as a *start* phase: it re-runs that phase **and everything
downstream of it**, in a new take.

The only thing genuinely missing is the *exact* single-phase run —
`runner.run(only="script")`, which runs that phase alone and nothing after
it. That capability exists in `avg/pipeline/runner.py` and has no route.
Decide whether the mockup's "Run again" means the existing downstream
re-roll (already shipped) or a new one-phase-only action, before building a
second control that overlaps the first.

**6. A `partial` phase state** — a **fifth** status alongside the four that
exist (`pending|running|done|failed`). Confirmed: `PhaseStatus` in
`avg/core/models.py` is still those four. The likely meaning is "visuals
finished but 2 of 12 images failed", which the visuals phase already
tolerates without recording. Nothing can emit `partial` today.

### 4c. Also worth folding in

- **The theme setting is disconnected from the app's actual themes.** The
  catalogue's `theme` setting offers nine choices (`soft_dark`,
  `monochrome_dark`, `monochrome_light`, `dark`, `light`,
  `high_contrast_light`, `neon`, `terminal_hacker`, `editorial_cream`) while
  `web/src/theme.ts` implements exactly two: `night` and `day`. **None of the
  nine catalogue values is one of those two, so all nine do nothing** — and
  the wiring is missing on both ends: `useTheme` reads its value from
  `localStorage` only and never reads the settings document at all, so even
  a matching id would not reach the UI. Treat this as "connect the theme
  setting to the theme system", not "fix a stray value".
  *(Two earlier descriptions of this were wrong. The 2026-07-26 handoff said
  the schema does not define `monochrome_light` — it does. The first version
  of this document said eight of nine choices are inert — it is all nine.)*
- `/video`, `/images` and `/thumbnail` are **not take-scoped** — confirmed,
  all three take only `project_id`. Studio works around it by showing a text
  summary for non-current takes.
- Re-running `visuals` regenerates **every** image, not just failed ones.
  Real money per image.
- Silence-removal constants (threshold, min silence, keep, pause length) are
  hardcoded from legacy defaults rather than being settings.
- `web/src/phases.ts` restates `PHASE_ORDER` — a second copy of phase order,
  the exact duplication `avg/pipeline/registry.py` exists to forbid. The
  frontend should read it from an endpoint.

---

## 5. How the work has been run

Every slice: **brainstorm → spec → plan → subagent-driven execution**, using
the `superpowers` skills. One fresh subagent per task, a checkpoint review
partway and at the end, one commit per task.

**Execution mode the user chose:** speed over ceremony — no per-step
RED/GREEN, one commit per task, **no front-end unit tests** (the UI is
verified by looking at it), Python tests kept.

### What repeatedly worked

- Telling an implementer to **verify a supplied fact rather than trust it**.
  It caught two wrong rows in an artifact map handed over as fact — and, on
  2026-07-28, a wrong claim in the previous version of this very document.
- Asking for a test that would **fail if someone "fixed" the behaviour
  later** — that is how the 5xx-does-not-switch-keys rule is genuinely
  protected.
- Doing the **research before dispatch** and handing over exact schemas. One
  task died having spent 26 tool calls on archaeology; the replacement
  started at implementation.

---

## 6. Account state (affects what you can test)

Only **Groq** currently generates text. Gemini is at zero free-tier quota
(429/billing), Pollinations at zero pollen budget (402, keyed and keyless),
HuggingFace returns `model_not_supported`. For images, **Pollinations and
Stable Horde work and are free**; OpenAI and Gemini do not. Voice: Edge TTS
is free and always works.

---

## 7. Suggested first move

Do **not** start coding. Two of the six features need design decisions that
do not exist yet — where cost data comes from, and what `partial` actually
means — so brainstorm Slice 6 first, then spec, then plan, then execute.

Get an answer on **shadows or no shadows** before the restyle.
