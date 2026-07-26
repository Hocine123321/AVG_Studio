\# Slice 6 — handoff brief



\*\*Written 2026-07-26 to start a fresh session.\*\* Everything a cold start needs.

Read this first; it assumes no prior context.



\---



\## 1. What this project is



\*\*AVG Studio\*\* — a Windows desktop app that turns a topic into a short-form

(9:16) or long-form (16:9) video: AI writes a brief and script, generates one

image per scene, synthesises narration, plans motion and captions, and renders

an mp4.



It \*\*replaces\*\* a PowerShell 7 CLI that still lives at `D:\\AI\_Video\_Gen`

(read-only reference, \~20,000 lines). The rewrite is at `D:\\AVG\_Studio`.



\- Python engine, FastAPI on `127.0.0.1` with a per-session token, React 19 UI

&#x20; in WebView2 via `pywebview`. One process.

\- Rendering runs in a \*\*child process\*\* calling a vendored copy of the old

&#x20; app's render engine.

\- \*\*Branch: `slice-1`\*\* (every slice has continued on it). \*\*537 tests pass.\*\*



\### Run it



```

\# tests

.venv-build\\Scripts\\python.exe -m pytest tests -q



\# the UI against real projects (kill anything on 8756 first)

.venv-build\\Scripts\\python.exe tools\\\_dev\_serve.py

\#   -> http://127.0.0.1:8756/?token=devtoken



\# build the app + installer from current source (\~4 min)

"Build Installer.bat"

\#   -> "AVG APP\\" (run it) and installer\\AVG-Setup.exe (132 MB)

```



\*\*`.venv-build\\Scripts\\python.exe` is the interpreter for everything.\*\* The

system `python` does not have the dependencies.



\---



\## 2. What is already built (Slices 1–5)



| | |

|---|---|

| \*\*Installer\*\* | One 128 MB `AVG-Setup.exe`, per-user, no UAC. Verified to launch with `PATH` stripped of Python and ffmpeg |

| \*\*Config\*\* | `settings.json`, per-channel documents, DPAPI-encrypted keys, 39-setting schema that renders the settings UI |

| \*\*Providers\*\* | 4 text backends + router with per-phase routing, 5 image providers, 2 voice providers — all behind one error taxonomy and retry policy, all tested against recorded HTTP fixtures |

| \*\*Pipeline\*\* | 8 phases, brief → script → director I → visuals → voice → director II → captions → render |

| \*\*Takes\*\* | Re-run with a change; only invalidated phases redo. Survives restart |

| \*\*Export\*\* | CapCut folder: images, narration, `.srt`, `timing.csv`, reference cut |

| \*\*Screens\*\* | Welcome, Home, Studio, Settings, Channels, Create |



Specs and plans for each are in `docs/superpowers/`. The running commentary,

including every decision and defect found, is `.superpowers/sdd/progress.md`.



\---



\## 3. Slice 6 — what to build



The user supplied \*\*`F:\\Downloads\\organic\_night\_v2.html`\*\* (69 KB, a working

HTML mockup). It is a reworked layout plus six features lifted from the

original PowerShell app. \*\*The palette is unchanged\*\* — same terracotta, sage,

Caprasimo, both themes. Read the mockup; it is the design authority.



\### 3a. The restyle (mechanical, low risk)



| Change | From → to |

|---|---|

| Rail width | 58px → \*\*74px\*\*, items 48px at 16px radius, 4px accent bar at `left:-13px` |

| Radii | larger; `--radius-lg` used on cards |

| \*\*Shadows\*\* | none → `--shadow-sm/md/lg` \*\*plus a card hover lift\*\* (`translateY(-2px)`) |

| Grid | fixed columns → `repeat(auto-fill, minmax(var(--cardmin), 1fr))` |

| Home header | adds a kicker + 38px Caprasimo title |

| New chrome | toasts (`.toast`), search box, device-frame player |



\*\*A decision the user should confirm before you start:\*\* the current app

deliberately uses \*\*no shadows\*\* — depth comes from a 1px top-edge highlight.

The mockup uses both shadows and hover lift. That is a change of principle, not

a tweak. It was raised and the user did not answer either way, so ask.



\### 3b. The six features



Each needs backend work that does not exist. They are \*\*not\*\* styling.



\*\*1. Models table\*\* — columns `Phase | Model | Backup | If both fail | Cost per video`.

The router already supports per-phase routing; \*\*there is no setting for it\*\*,

so the capability is currently unreachable. This is the highest-value item:

it lets a cheap model do segmentation and a strong one write the brief.

Needs: per-phase model settings in `avg/core/settings\_schema.py`, a backup

field, and a failure action (`stop` / `switch provider` / `fall back`).



\*\*2. Spend card\*\* — sums completed and in-progress phase costs across projects.

\*\*Nothing tracks cost anywhere today.\*\* Needs a real decision: where do prices

come from (a table you maintain? provider APIs?), what unit is recorded (tokens?

images? seconds?), and where it is stored (on the `PhaseRecord`, probably).

Design this before building it.



\*\*3. Components / health\*\* — FFmpeg, Pillow, moviepy rows with state, a recheck

action, "Download failed", a health cache. This is the old app's Check Health.

No health module exists. \*\*Worth real weight:\*\* it would have told the user

their Gemini account was at zero quota before a video failed.



\*\*4. Bulk select and archive\*\* on Home — `.select-bar`, `bulkArchive`,

`bulkClear`, an `Archived` state. Needs an archived flag on `ProjectDoc`,

persistence, and Home selection state.



\*\*5. Run again per phase\*\* — `runner.run(only="script")` \*\*already works\*\*;

there is no route and no button. Small and high value.



\*\*6. A `partial` phase state\*\* — a fourth status beyond

`pending|running|done|failed`. The likely meaning is "visuals finished but 2 of

12 images failed", which the visuals phase already tolerates without recording.

Nothing can emit `partial` today.



\### 3c. Also worth folding in



\- Live settings hold `theme: "monochrome\_light"`, a value the schema does not

&#x20; define — the settings screen is showing a state it cannot validate.

\- `/video`, `/images` and `/thumbnail` are \*\*not take-scoped\*\*; Studio works

&#x20; around it by showing a text summary for non-current takes.

\- Re-running the `visuals` phase regenerates \*\*every\*\* image, not just failed

&#x20; ones. Real money per image.

\- Silence-removal constants (threshold, min silence, keep, pause length) are

&#x20; hardcoded from legacy defaults rather than being settings.



\---



\## 4. Rules that are not negotiable



Learned the hard way across five slices. Breaking any of these has already

caused a defect at least once.



\- \*\*Exactly one terracotta (`#e08a4e`) action per screen.\*\* Terracotta means

&#x20; "happening right now" and nothing else; sage `#aebf92` means settled. Four

&#x20; tasks have had to fix violations.

\- \*\*Never add a Google Fonts import.\*\* Caprasimo and Figtree are self-hosted in

&#x20; `web/public/fonts` so the app works offline; a remote `@import` silently

&#x20; destroys the identity with no network and pings Google on every launch.

\- \*\*`avg/media/vendor/` is protected\*\* — byte-for-byte copies of the old render

&#x20; engine, pinned in `.gitattributes` because `core.autocrlf` silently corrupted

&#x20; the stored blob once. Never edit. The only permitted change ever made is a

&#x20; 9-line progress hook.

\- \*\*No live API calls in tests, ever.\*\* Recorded fixtures replay and the

&#x20; transport raises on an unmatched request. AssemblyAI, ElevenLabs and image

&#x20; providers cost real money; one paid ElevenLabs recording was already lost.

\- \*\*Nothing below `avg/api/` may print or prompt.\*\* Lower layers emit typed

&#x20; events. This is what replaced 914 `Write-Host` calls.

\- \*\*`D:\\AI\_Video\_Gen` is read-only.\*\* Ten real projects live there.

\- \*\*Phase order exists only in `avg/pipeline/registry.py`.\*\* A second copy was

&#x20; found and removed once already.

\- \*\*`save\_project` is the only writer of `project.json`\*\*, and it is atomic.

\- \*\*Every setting must declare `invalidates`\*\* — it is a required field with no

&#x20; default, so omitting it fails at import. A test enforces completeness.



\### Account state (affects what you can test)



Only \*\*Groq\*\* currently generates text. Gemini is at zero free-tier quota

(429/billing), Pollinations at zero pollen budget (402, keyed and keyless),

HuggingFace returns `model\_not\_supported`. For images, \*\*Pollinations and Stable

Horde work and are free\*\*; OpenAI and Gemini do not. Voice: Edge TTS is free and

always works.



\---



\## 5. How the work has been run



Every slice: \*\*brainstorm → spec → plan → subagent-driven execution\*\*, using the

`superpowers` skills. One fresh subagent per task, a checkpoint review partway

and at the end, one commit per task.



\*\*Execution mode the user chose:\*\* speed over ceremony — no per-step RED/GREEN,

one commit per task, \*\*no front-end unit tests\*\* (the UI is verified by looking

at it), Python tests kept.



`.superpowers/sdd/progress.md` is the recovery ledger. Sessions have died

mid-slice six times; that file plus `git log` is what makes resuming possible.

\*\*Trust it over recollection.\*\*



\### What repeatedly worked



\- Telling an implementer to \*\*verify a supplied fact rather than trust it\*\* —

&#x20; it caught two wrong rows in an artifact map I had handed over as fact.

\- Asking for a test that would \*\*fail if someone "fixed" the behaviour later\*\*

&#x20; — that is how the 5xx-does-not-switch-keys rule is genuinely protected.

\- Doing the \*\*research before dispatch\*\* and handing over exact schemas. One

&#x20; task died having spent 26 tool calls on archaeology; the replacement started

&#x20; at implementation.



\---



\## 6. Suggested first move



Do \*\*not\*\* start coding. Brainstorm Slice 6 first — two of the six features

need design decisions that do not exist yet (where cost data comes from, and

what `partial` actually means). Then spec, then plan, then execute.



Confirm with the user first: \*\*shadows or no shadows.\*\*



