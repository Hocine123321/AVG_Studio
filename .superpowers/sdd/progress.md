# Slice 19 — Settings, scope, and the small fixes — progress ledger

Environment note: implementing on a Linux sandbox, not the Windows dev box.
`.venv-build` is a Windows venv (WinDLL/pywebview) and cannot run here.
Rebuilt `.venv-linux` (core deps only: fastapi, uvicorn, pydantic, pytest,
httpx, numpy, Pillow — no pywebview/pydub/moviepy) plus the local-only
winstub pytest plugin at `/home/claude/verify_shims/_winstub_plugin.py`
(outside the repo, never committed) that stubs `ctypes.WinDLL` and `webview`
so `avg.config.secrets`/`avg.shell` import here. Same setup as Slice 18's
own verification pass; neither the venv nor the shim persist between
sandbox sessions, so a resumed session must rebuild both before trusting a
"tests pass" claim.

## Baseline (measured on tree at 146e649, before this session's changes)
`pytest tests -q` with the shim: **26 failed, 916 passed, 1 skipped.**
All 26 are pre-existing and unrelated to Slice 19:
- DPAPI round-trip (`tests/config/test_secrets.py`, `test_routes_settings.py`
  secret-leak tests, `test_health.py`) — needs the real Win32 API the stub
  fakes, not fixable on Linux.
- `edge_tts` not installed in this lean venv (`tests/providers/test_voice.py`).
- `startup_cost` import-isolation tests — sensitive to which optional
  packages happen to be installed in the venv.
- `test_shell.py::test_reading_the_setting_needs_no_server_and_no_app` —
  subprocess-based, environment-sensitive.
- `test_resolve_font_family_extracts_family_name_from_a_font_file_path` —
  `Path(r"C:\...").stem` behaves differently on POSIX (backslash isn't a
  path separator) than on Windows; a genuine platform difference in the
  test's own fixture, not a bug in `resolve_font_family_value`.

`mypy` on the 7 phase files touched this task: **628 errors, 15 files**
(baseline, measured before and after — see Task 5 section, identical count
both times).

## Tasks
- [x] Task 1: `resolve_channel_settings`, the one resolver
- [x] Task 2: remove `settings_source`, rewrite CLAUDE.md's Config scopes
- [x] Task 3: wire resolver into routes_settings.py; ownership routes;
      Global-mode read/write
- [x] Task 4: thread `channel_values` through `PhaseContext`/`runner.run`
      and both API callers
- [x] Task 5: migrate every phase file off `ctx.channel.settings`
- [x] Task 6: the drift test
- [x] Task 7: `aspect_ratio` removed, derived from `video_form`
- [x] Task 8: frontend `useSettingsData.ts` — ownership and scope
- [x] Task 9: frontend ownership UI in `SettingsGroup.tsx`/fields
- [x] Task 10: frontend the category table and tab strip
- [x] Task 11: frontend the Global/Channel toggle
- [x] Task 12: small fix — remove "Run again" from phase-error surfaces
- [x] Task 13: small fix — the "A" logo becomes the main menu

## Task 5 notes
Mechanical migration across the seven files from the plan's §0 inventory:
`_common.py`, `writing.py`, `visuals.py`, `voice.py`, `render.py`,
`captions.py`, `director.py`. Every `ctx.channel.settings.get(...)` /
`settings.get(...)` (where `settings = ctx.channel.settings`) replaced with
the equivalent read against `ctx.channel_values`. Local `or _DEFAULT_X`
fallbacks deliberately left untouched, per the plan's own scope note (a
separate, unrequested cleanup with its own risk).

`render.py` additionally lost its `getattr(ctx.channel, "settings", {}) or
{}` guards at both call sites (`_composite_request`, `_render`) — no longer
needed since `channel_values` is a required `PhaseContext` field independent
of whether `ctx.channel` is `None`, unlike the raw `.settings` attribute
access it replaced.

**Drift-scan run by hand** (Task 6's own regex, `\.settings\.get\(|\.settings\[`,
against `avg/pipeline` and `avg/api`) confirms zero offenders in the 7 phase
files after this task. The one remaining hit anywhere in scope is
`avg/pipeline/create.py:140` (`aspect_ratio`) — expected, that's Task 7's
job, not Task 5's.

**Test fallout caught:** `tests/pipeline/test_phase_render.py` builds a bare
`SimpleNamespace` ctx (not a real `PhaseContext`) for ~15 `RenderPhase().run()`
call sites. It had no `channel_values` attribute; with the `getattr` guard
removed from `render.py`, every one of those tests would have raised
`AttributeError`. Fixed by adding `channel_values={}` to that fixture
(mirrors the file's own existing `render_lease=None` precedent and comment
style).

**Representative tests added** (per the task's own instruction: "one
representative field per file is enough given Task 6 catches anything a
file-by-file read missed structurally"), each proving a value absent from
the channel's own document but present in the resolved `channel_values`
(built via `resolve_channel_settings` with a non-empty `defaults` argument)
is what the phase actually uses — the behaviour that could not have been
true before this task:
- `test_phases_text.py`: `test_research_inherits_from_the_app_wide_defaults_when_the_channel_has_no_opinion`
- `test_phases_media.py`: `test_visuals_inherits_image_provider_from_the_app_wide_defaults`,
  `test_voice_inherits_provider_from_the_app_wide_defaults`
- `test_phases_polish.py`: `test_captions_font_family_inherits_from_the_app_wide_defaults`
  (also covers `_common.py`'s `resolve_font_family`),
  `test_captions_font_size_and_position_inherit_from_the_app_wide_defaults`,
  `test_draw_overlay_assets_inherits_accent_colors_from_the_app_wide_defaults`
- `test_phase_render.py`: `test_render_reads_fps_off_channel_values_even_with_no_channel`
  (channel is `None` in this file's own fixture, proving `channel_values`
  is genuinely independent of `ctx.channel`, not merely a renamed read of it)

**Verification:** `pytest tests -q` after all changes: **26 failed, 923
passed, 1 skipped** — identical 26 pre-existing baseline failures (including
the same font-family POSIX/Windows path test), 7 new tests all green, zero
regressions. `mypy` on the 7 touched files: 628 errors both before and after
(stashed the working tree to confirm) — no new errors introduced.

Build/test/commit done as this task's own commit, per plan.

## Task 6 notes

Task 6's own text assumes the drift scan finds zero offenders on the
Task-5-complete tree and says any offender found is a Task-5 straggler to
be amended there. That assumption doesn't hold: a hand-run of the scan
confirms exactly one offender, `avg/pipeline/create.py:140`
(`aspect_ratio`) — which Task 5's own notes above already named and
deliberately deferred as "Task 7's job, not Task 5's." Reopening Task 5's
commit to chase a straggler it explicitly, correctly declined to fix would
contradict Task 5's own reasoning.

Resolution (user-directed): wrote `test_no_reader_bypasses_the_resolver`
into `tests/core/test_settings_schema.py` exactly per the plan's spec —
same regex, same file-level allowlist (`avg/api/routes_settings.py` only,
covering both the resolver's own `_channel_values` construction and the
legitimate `port_to_global` raw read). Did not add a second, temporary
allowlist entry for `create.py` — Task 7 removes that offender outright
(derives `aspect_ratio` from `video_form` instead of reading
`channel.settings` at all), so the test is written to its final, intended
shape now rather than a shape that would need un-relaxing later.

**Known state:** this test will fail until Task 7 lands (one offender:
`avg/pipeline/create.py:140`). Per explicit user instruction, no test runs
between Task 6 and the last task — full verification (`pytest`, `mypy`)
happens once at the end, after Task 13.

## Task 7 notes

Real gap found beyond the plan's own text: Task 4 was supposed to make
`create_project` (`avg/api/app.py`) and `create_take` (`avg/api/
routes_takes.py`) build `effective_settings` from `dict(resolved)` rather
than `dict(channel.settings)` — the plan says so explicitly (§0's inventory
and Task 4.3's own bullets) — but neither call site was actually changed;
both still read `channel.settings` raw. This wasn't caught by Task 6's
drift test because `dict(channel.settings)` matches neither of the test's
two regexes (`.settings.get(` / `.settings[`) — a real blind spot in that
test's coverage, noted here rather than expanded now since widening the
regex is its own, unrequested change with its own risk of new false
positives (e.g. `channel.settings` bare, used legitimately elsewhere for
other purposes).

Fixed both as part of this task, since Task 7.2's own text required it
("this function already needs to move to reading through the resolver per
Task 4's inventory... not a second raw read") — `new_project` needed a
resolved `video_form` in hand, and that only exists if its caller actually
resolved first:
- `avg/api/app.py`'s `create_project`: `effective_settings` now starts
  from `dict(resolve_channel_settings(channel.settings, load_defaults(...)))`.
- `avg/api/routes_takes.py`'s `create_take`: same fix, same reasoning —
  `effective_settings` now starts resolved before overrides fold onto it.

`avg/pipeline/create.py`: `aspect_ratio` `SettingSpec` deleted from the
catalogue. `new_project` gained a required `video_form: str` keyword-only
parameter, replacing its internal `channel.settings.get("aspect_ratio")`
read entirely; `derive_aspect_ratio()` (public, not the plan's own private-
underscore naming, since `routes_takes.py`'s `_sync_current_take` needs to
import the same mapping rather than duplicate it) is the one place the
`shortform -> 9:16` / `longform -> 16:9` mapping is written down.

`avg/api/routes_takes.py`'s `_sync_current_take`: added the `else` branch
deriving from `take.settings_snapshot.get("video_form")` when no legacy
`aspect_ratio` key is present; the `if "aspect_ratio" in ...` branch itself
is untouched, so an old take's own stored ratio still wins, as the spec's
consequence #2 requires. `create_take`'s shadow-project assembly (line
~620) now calls `derive_aspect_ratio` too, instead of reading a key that no
longer exists in `effective_settings`.

**Tests:** `tests/pipeline/test_create.py` rewritten for the new
`video_form=` kwarg throughout; the two old aspect-ratio-from-channel tests
replaced with `test_new_project_derives_aspect_ratio_from_shortform`,
`..._from_longform`, and `test_new_project_ignores_a_stray_aspect_ratio_
key_on_the_channel` (consequence #3). `tests/core/test_invalidation.py`:
tripwire count 65 -> 64, plus a new `test_changing_video_form_invalidates_
visuals_not_merely_brief` pinning consequence #1 (previously implicit in
phase ordering, never asserted). `tests/api/test_routes_settings.py`: the
invalid-choice-value test's example setting swapped from the now-gone
`aspect_ratio` to `video_form` (both `type="choice"`, same test intent).
`tests/api/test_routes_takes.py`: two new direct unit tests against
`_sync_current_take` itself — one proving a legacy snapshot's stored
`aspect_ratio` wins (consequence #2, the plan's own requested "new test"),
one proving the derive-from-`video_form` path when no such key exists.

Audited (no changes needed, confirmed harmless per the plan's own
distinction): `test_preview_routes.py`, `test_models.py`,
`test_persistence.py`, `test_legacy_import.py`, `test_settings_import.py`,
`test_images.py`, `test_prompts.py` — every `aspect_ratio` reference in
these is either `ProjectDoc.aspect_ratio` field construction (the field
itself was never removed, only the settable channel setting) or an image
provider's own `.generate(aspect_ratio=...)` render-time argument, neither
of which this task touches.

**Known state:** no test run yet, per the same explicit instruction as
Task 6 — full verification deferred to after the last task in this slice.

## Task 8 notes

`api.ts`: `getSettingsValues(scope?)` appends `?scope=global` when set;
`saveSettingsValues(values, target?)` passes `{ values, target }`, default
`"channel"` so every pre-existing caller (NewTakePanel does not call this
one, but QuickSettingsTab/PreviewTab share the hook) is unaffected; new
`releaseSettingValue(id)` wraps `DELETE /api/settings/values/{id}`.

`fields/types.ts`: `followingGlobal` removed; added `scope: "channel" |
"global"`, `isOwned: (id: string) => boolean`, `onReleaseOwnership: (id:
string) => void`.

`useSettingsData.ts`: `scope` is local `useState`, not persisted (spec §3 -
a presentation-mode switch). A second effect refetches channel-scope
values whenever `scope` changes (guarded on `schema !== null` so it never
double-fires alongside the initial-load effect) and clears `draft` - an
edit made in one document must not silently apply to the other after a
toggle. `handleSave` now passes `scope` as `saveSettingsValues`'s `target`
and refetches `channels` after a successful save (a save can turn an
inherited field into an owned one). `isOwned` reads `channels` (already in
memory via `getChannels()`) client-side, per the plan's own §8.3 - no new
backend read. `releaseOwnership` calls the new endpoint then refetches
both values and channels.

`NewTakePanel.tsx`: this panel edits per-take overrides, never the channel
document, so ownership doesn't apply here - `scope: "channel"`, `isOwned:
() => true` (always editable, matching the old `followingGlobal: false`'s
intent), `onReleaseOwnership` a no-op stub only present to satisfy
`SettingsCtx`'s shape.

**Build confirmed to fail exactly as the plan predicted** (`npx tsc -b`):
one error, `Settings.tsx(78,22): Property 'followingGlobal' does not exist
on type 'SettingsCtx'` - the one remaining consumer, fixed in Task 9-11.
`web/package-lock.json` picked up 54 lines of Linux-only npm-install noise
(platform-specific optional deps the Windows-generated lockfile doesn't
list) - reverted, not committed; not a real project change.

## Tasks 9-11 notes (committed together with Task 12/13 — see below)

Task 9: `SettingsGroup.tsx` now computes `ownershipApplies = spec.scope
=== "channel" && ctx.scope === "channel"` per row; an inherited field
renders disabled (reusing `.field-input:disabled`/`.switch:disabled` -
no new disabled visual, per plan) with a muted explanatory line and a
"Take ownership" button (`ctx.onCommit(spec, ctx.getValue(spec))` - joins
the ordinary draft batch, saved on the next "Save changes", not an
immediate write); an owned field gets a small "Release" button
(`ctx.onReleaseOwnership(spec.id)` - immediate, not batched, matching the
secret lane's own immediate-delete precedent). `.btn.btn--sm`/`.btn--sm
.btn--ghost` reused rather than new classes, per this file's existing
row-chrome vocabulary.

Task 10: `Settings.tsx`'s sidebar (`.settings__nav`) replaced by
`CATEGORIES` (7 entries, all 10 schema groups covered - `keys` folded
into Models, `overlay` into Captions, `video` into Render, matching the
plan's own grouping) rendered as `.tabstrip`/`.tabchip` (CSS copied
verbatim from `docs/reference/organic_night_v2.html:260-262`).
`activeGroup`/`jumpTo` became `activeCategory`/`jumpToCategory`; clicking
a tab actually filters to that category's groups (`categoryGroups`), not
just an anchor-scroll within one continuous page. Search still wins over
the category filter (`searchResults` scans every group regardless of
`activeCategory`), and a new effect corrects `activeCategory` to wherever
the first search match lives, so the tab strip's highlight never points
at a category with nothing shown. The scrollspy (Intersection Observer)
is kept per Task 10.4's own instruction, narrowed automatically since it
only ever observes what `visibleGroups` renders; its only visible effect
now is `.settings-group__title--active` (a subtle accent-color highlight)
for the three two-group categories (Models, Captions, Render) - inert for
the four single-group ones, as the plan itself predicted. The
category-completeness assertion lives in the `groups` `useMemo` itself: a
schema group with no `CATEGORIES` home throws at render time (this
codebase's stand-in for a frontend unit test, per CLAUDE.md's "no
frontend unit tests" convention already used for the runtime checks in
this same file).

Task 11: the Global/Channel switch sits in `.settings__header`, styled
with the existing `.switch`/`.switch__thumb` tokens (no new toggle
component). Wired straight to `scope`/`setScope` from `useSettingsData`;
disabled when no channel is active (nothing to switch to/from). The old
`followingGlobal` banner (a `settings_source`-reading channel silently
showing every field disabled) is replaced by a `scope === "global"` banner
explaining the opposite state - nothing is disabled in Global mode, this
note is purely informational, not a warning.


## Task 12 notes

`Studio.tsx`'s `ArtifactDetail`: removed both direct `onClick={() => void
onStartRender()}` buttons (the failed-phase one and the render-done one).
`onStartRender` itself removed from `ArtifactDetail`'s props and its call
site (`noUnusedParameters` caught the now-dead prop immediately). Nothing
else touched - confirmed the three surviving paths (take strip's "New
take", the per-phase "Run again" that opens the new-take panel, and the
header CTA) still exist unchanged in this file.

## Task 13 notes

`Rail.tsx`: `.rail__logo` is now a real `<button onClick={onHome}>`
(`aria-label="Your videos"`, `aria-current` when `active === "home"`),
not a `<div aria-hidden>`. The separate `IconGrid` `.rail__item` this
duplicated is deleted outright, along with its now-unused `IconGrid`
import - two controls doing the same job was the defect Task 13 names,
so removing the duplicate rather than keeping both. `tokens.css`'s
`.rail__logo` gains `padding: 0`, `cursor: pointer`, and a hover state
(undoing/adding what a bare `<div>` never needed as a button).

## Final verification

`npx tsc -b`: clean. `npx oxlint`: 0 warnings, 0 errors, 47 files. `npm
run build` (`tsc -b && vite build`): clean, 59 modules, no errors.

Backend `pytest`/`mypy` deferred per explicit instruction since Task 6 -
running now, after Task 13, as the single verification pass for the
whole slice (Tasks 6-13).

`pytest tests -q`: **26 failed, 928 passed, 1 skipped**. Same 26
pre-existing failures as this sandbox's own recorded baseline (DPAPI/
secrets-file round-trip, `edge_tts` not installed, `webview`/`ctypes.
WinDLL` shim gaps, the POSIX/Windows font-family path test, and the
legacy-projects scan needing a real `D:\AI_Video_Gen` that doesn't exist
here) - confirmed same failing test names, zero regressions. Passed count
+5 over the 923 baseline, net of Task 6's one new test, Task 7's swap of
2 old aspect-ratio tests for 4 new ones (+2), the invalidation pin (+1),
and `_sync_current_take`'s 2 new direct unit tests (+2).

`mypy`, whole-package count not directly comparable to the 628 figure
Task 5 recorded (different environment - this sandbox's mypy/stub
versions differ from the real dev machine's, so absolute counts don't
mean the same thing across the two). Instead, ran the same before/after
comparison Task 5 used, but via `git stash`+`checkout d7e6a10` for a true
same-environment baseline rather than a stash of uncommitted work: mypy
on the 4 backend files Tasks 6-7 touched (`avg/api/app.py`, `avg/api/
routes_takes.py`, `avg/core/settings_schema.py`, `avg/pipeline/create.py`)
- **27 errors at `d7e6a10` (pre-Slice-19-Task-6), 27 errors at HEAD**,
same errors verbatim (all pre-existing `dict` missing-type-argument /
`no-any-return` findings unrelated to this slice's changes) - zero new
mypy errors introduced.

**Slice 19 (Tasks 6-13) complete.** Every task committed and pushed
individually to `origin/main`; this verification pass and its ledger
entry are the last piece, committed together.

**DOM/computed-style checks** (per CLAUDE.md's "no frontend unit tests"
convention - a scripted check against a real render, not a persisted test
file; harness lives outside the repo at `/home/claude/dom_check`, same
throwaway-tooling precedent as the Linux `ctypes.WinDLL`/`webview` pytest
shim, since this sandbox has no display for the usual Browser-pane visual
pass):

- `SettingsGroup.tsx` (Task 9), rendered via `esbuild` + `jsdom` +
  `react-dom/client` (`react`/`react-dom` marked external so the harness
  and the bundled component share one React instance - a duplicate copy
  is what "Invalid hook call" meant on the first attempt): an inherited
  field's control (the app's own `Select`, a `<button disabled>` under
  the hood, not a native `<select>`) is genuinely disabled and shows a
  "Take ownership" button that calls `onCommit` with the field's current
  value; an owned field is not disabled and shows a "Release" button that
  calls `onReleaseOwnership` with the field's id; in `scope === "global"`
  neither button renders and nothing is disabled. All 9 assertions passed.
- `Rail.tsx` (Task 13): `.rail__logo` renders as a real `<button>`
  (`aria-label="Your videos"`, `aria-current="page"` when
  `active === "home"`), calls `onHome` on click, and no duplicate
  `.rail__logo`/`IconGrid` element remains. All 6 assertions passed.


---

## Slice 20 — UserAssets storage consolidation (2026-08-14)

Implemented per `docs/superpowers/specs/2026-08-11-slice-20-userassets-design.md`
and `docs/superpowers/plans/2026-08-11-slice-20-userassets-plan.md`, all 7
tasks, on branch `slice-20-userassets` (not `main` - kept as a reviewable
backup branch per instruction), one commit per task, pushed to
`origin/slice-20-userassets` after each.

**Environment note:** this session's sandbox is Linux, not the Windows dev
machine `.venv-build` targets. Built a disposable `.venv-linux` (gitignored,
matching the existing `.venv-linux/` entry) plus a sandbox-only
`sitecustomize.py` shim on `PYTHONPATH` (outside the repo, at
`/home/claude/pytest_linux_shim/` - same throwaway-tooling precedent as
Slice 19's DOM-check harness) that no-ops `ctypes.WinDLL`/`ctypes.
GetLastError` so `avg.config.secrets`'s module-level DPAPI binding doesn't
crash import on non-Windows. Established a same-environment baseline before
touching any code: **947 passed, 7 failed, 1 skipped** - the 7 are all
environment-specific and pre-existing, not Slice 20 regressions: 5 DPAPI
round-trip tests in `test_secrets.py` (need real Windows crypt32), 1 font
resolution test in `test_phases_polish.py` (needs a real Windows font
path), 1 legacy-scan test needing the real dev machine's `D:\AI_Video_Gen`
data. Verified `mypy avg`'s 690 errors are identical (same files, same
messages) before and after the slice via `git stash` comparison - none of
Slice 20's touched files (`avg/config/*.py`, `avg/core/model_prefs.py`,
`avg/api/app.py`) contribute any.

**What moved.** Eight documents now resolve under
`Documents/AVG Studio/UserAssets/`, migrated once and automatically from
their pre-Slice-20 `%APPDATA%/AVG Studio/` location the first time
`user_assets_root()` is called (`migrate_legacy_files`, idempotent by
filesystem state - an item moves only when its old path exists and its
new path doesn't yet, and both existing leaves the old copy alone rather
than overwriting):

| Document | New location |
|---|---|
| `settings.json` | `UserAssets/Config/settings.json` |
| `channel_defaults.json` | `UserAssets/Config/channel_defaults.json` |
| `quick_settings.json` | `UserAssets/Config/quick_settings.json` |
| `model_prefs.json` | `UserAssets/Config/model_prefs.json` |
| `channels/` | `UserAssets/Config/channels/` |
| `secrets.dat` | `UserAssets/Secrets/secrets.dat` |
| `errors.json` | `UserAssets/Logs/errors.json` |
| (new projects) | `UserAssets/Projects/` (`suggest_output_root`'s fallback, Task 6 - existing installs' library location is unchanged, only a *fresh* install's suggested default moves) |

Each move is recorded in an append-only `UserAssets/migration.json`
manifest (`{"events": [{"at": ..., "moved": [...]}]}`).

**What deliberately stayed put** (all caches, rebuildable, not user data
- see `avg/config/user_assets.py`'s module docstring): `working_models.json`
(`avg.core.model_cache`, still `%APPDATA%/AVG Studio/`),
`model_cache.json` (`avg.providers.models`'s advertised-catalogue cache,
untouched - not even imported by this slice), and the thumbnail cache
(`avg.media.thumbnails`, still `%LOCALAPPDATA%`).

**Layering:** `default_errors_path`, `default_model_prefs_path` and
`default_working_models_path` live in `avg/config/user_assets.py` rather
than beside `ErrorStore`/`ModelPrefs`/`ModelCache` in `avg/core/`, because
`avg/core/` cannot import `avg/config/` (CLAUDE.md's Architecture
section). `avg.core.model_prefs.default_prefs_path` is removed - it had
exactly this layering problem already, latent, since `create_app` never
actually called it (its only caller anywhere in the repo was the
isolation test, now updated to call the new function instead).

**Test-harness isolation** (Task 2) extended `tests/conftest.py`'s
session-wide fixture to also redirect `USERPROFILE`, alongside the
existing `APPDATA`/`LOCALAPPDATA` redirect - this is what stops the suite
from ever touching a developer's real `Documents/AVG Studio` the moment
Task 4 makes `default_settings_path()` (and friends) depend on
`user_assets_root()` for real. `tests/test_profile_isolation.py` covers
all eight relocated/new documents plus the three that stayed at
`%APPDATA%`/`%LOCALAPPDATA%` by name.

**Final verification (Step 7.1):**
- `pytest tests -q`: **967 passed, 7 failed, 1 skipped** - same 7
  pre-existing failures as the pre-slice baseline, net +20 passing (16
  from Task 4's full-suite exercise of every `create_app(...)` call site
  through the real new root, +3 from Task 5, +1 from Task 6's new test).
  Zero regressions.
- `mypy avg`: 690 errors, identical set to the pre-slice baseline (verified
  via `git stash` same-environment comparison). Zero new errors.
- `cd web && npm run build && npm run lint`: clean (0 errors, 0 warnings)
  - expected, since no Slice 20 task touches `web/`; run anyway per the
    plan as a sanity check.

**Commits** (branch `slice-20-userassets`, each pushed individually):
`bd206fb` Task 1, `cabe54c` Task 2, `b59749b` Task 3, `ccd39fb` Task 4,
`3bd9ff2` Task 5, `d9a66d6` Task 6, plus this ledger entry for Task 7.

**Deferred / explicitly out of scope** (per the plan): making `UserAssets`
itself user-relocatable; physically moving an *existing* install's video
library (only a fresh install's suggested default changed); the App
Health "Logs" button UI; any UI work at all (this slice is backend-only).

---

## Slice 21 — Duration bug + narration trimming rework (2026-08-15)

Implemented per `docs/superpowers/specs/2026-08-11-slice-21-duration-narration-design.md`
and `docs/superpowers/plans/2026-08-11-slice-21-duration-narration-plan.md`,
both tasks, on branch `slice-21-duration-narration` (off `main`, after
Slice 20's PR #6 merged) - not `main` - pushed to
`origin/slice-21-duration-narration` after each commit.

**Environment:** same Linux sandbox setup as Slice 20 (`.venv-linux` +
the `sitecustomize.py` shim on `PYTHONPATH` at
`/home/claude/pytest_linux_shim/`, outside the repo). Baseline re-confirmed
before touching code: **980 passed, 7 failed, 1 skipped** - same 7
environment-specific failures as Slice 20's baseline (DPAPI, font
resolution, real dev-machine legacy path).

**Task 1 - word budget scales with duration.**
`avg.pipeline.prompts._word_budget_for_duration(duration_seconds)`
replaces the fixed `word_min=60, word_max=75` `build_narration_prompt`
always used regardless of the requested length - the direct cause of
2m/4m requests collapsing to ~30s of actual narration (the writer was
never told to write more). Calibrated to reproduce the old fixed values
exactly at the 30s preset (`* 2.0` / `* 2.5`). `word_min`/`word_max`
became `None`-default so the one caller (`writing.py`) picks up the
scaled budget with no code change; explicit overrides (one existing
test) still win.

**Task 2 - trimming gets a real ceiling.** Two new channel settings
(`voice_trim_aggressiveness`: low/medium/high, default medium;
`voice_max_removable_silence_pct`: float, default 40.0) replace
`avg.pipeline.phases.voice`'s three fixed silence-removal constants with
`_AGGRESSIVENESS_PARAMS`, a three-preset map - "medium" reproduces the
exact old fixed values, so nobody who doesn't touch the setting sees any
change. `_clean_segment` runs `remove_silences_core` once, and if the
result removed more than the cap allows, retries exactly once at one
aggressiveness level down (`_AGGRESSIVENESS_STEP_DOWN`) - already-"low"
retries with itself and is treated as no retry. Still over the cap after
the retry: keeps the result and calls `report_degradation` rather than
raising or silently discarding synthesized audio. `voice_speed` remains
unwired, `_MIN_SCENES`/`_MAX_SCENES` unchanged - both explicitly out of
scope per the design spec.

**Fallout beyond the plan's own file list**, found and fixed during
verification:
- A second, independent fake `remove_silences_core` in
  `tests/pipeline/test_runner.py`'s registry smoke test (the plan's file
  list only named the one in `test_phases_media.py`) had the same
  `None`-return incompatibility once `_clean_segment` started calling
  `.get(...)` on the result - fixed the same way, a realistic result
  dict.
- The plan's own `test_segment_over_cap_retries_one_level_down` fixture
  had an inverted conditional (`8.0 if thresh == -40.0 else 2.0`) that
  contradicted its own comment and never actually exercised the retry
  path it claims to test - the first call (aggressiveness="high",
  thresh=-35.0) fell into the `else` branch and returned an
  under-the-cap value, so no retry ever fired. Corrected to
  `2.0 if thresh == -40.0 else 8.0`, verified the corrected assertion
  passes against the (unmodified, spec-matching) `_clean_segment`
  implementation.
- `tests/core/test_invalidation.py`'s `SETTINGS`-count tripwire
  (64 → 66) for the two new settings - exactly the kind of update its
  own docstring anticipates.
- Three new mypy errors from `voice.py`'s new code (two
  `no-untyped-call` on the vendored `remove_silences_core`, one
  `float(object)` incompatibility in `_removed_pct`) - fixed with the
  same `# type: ignore[no-untyped-call]  # the vendored engine is
  untyped by design` pattern already used elsewhere in this file and
  `director.py`, and by typing `_removed_pct`'s parameter `dict[str,
  Any]` instead of `dict[str, object]`.

**Final verification (Step 3.1):**
- `pytest tests -q`: **980 passed, 7 failed, 1 skipped** - same 7
  pre-existing failures as the pre-slice baseline. Zero regressions.
- `mypy avg`: 689 errors (was 690 before this slice touched anything;
  the one fewer is incidental to this slice's own new code, not a
  pre-existing error that disappeared elsewhere) - zero new errors
  contributed by any file this slice touches, confirmed via `git stash`
  same-environment comparison exactly as Slice 20's Task 7 did.

**Commits** (branch `slice-21-duration-narration`, each pushed):
`25dfe6f` Task 1, `2223ce0` Task 2, plus this ledger entry for Task 3.

**Deferred / explicitly out of scope** (per the design spec): wiring
`voice_speed` into actual TTS synthesis; widening
`_MIN_SCENES`/`_MAX_SCENES` for long-form videos; a dedicated Slider UI
treatment for the two new settings (the existing generic Settings UI
already renders a `choice` and a `float` correctly without one - belongs
with the Settings-selectors slice instead).

---

## Slice 22 — Progress bar phase-transition bug (2026-08-15)

Implemented per `docs/superpowers/specs/2026-08-11-slice-22-progress-bar-design.md`
and `docs/superpowers/plans/2026-08-11-slice-22-progress-bar-plan.md`, on
branch `slice-22-progress-bar` off `main` (pushed to
`origin/slice-22-progress-bar`). Frontend-only slice, one file, one
function, one commit.

**Root cause:** `web/src/useEvents.ts`'s `foldByProject` - the per-project
progress map behind `Transport.tsx` (mounted once, visible on every
screen) - only ever handled `progress` events (write/overwrite) and
terminal events (delete). A `phase_started` event fell through to the
untouched-map branch on purpose, per the function's own prior docstring.
Net effect: phase N's last `progress` event (percent 100) stayed in the
map after phase N+1 started, until phase N+1 happened to emit its own
first `progress` tick - visibly stuck at the previous phase's 100% in
the meantime.

**Fix:** `foldByProject` gained a `phase_started` branch that replaces
(never deletes) the project's entry with a fresh `{percent: 0, message:
"Starting <Phase>…"}`, phase label resolved from `phases.ts`'s
`PHASE_DEFS`. Replace rather than delete because `Home.tsx`'s
hero/stack requires a truthy `byProject` entry to show a job at all - a
bare delete would make an actively running job flicker out of the UI for
the gap, which is worse than the bug being fixed. Side effect noted in
the design spec as intentional: a job's very first phase now appears in
the hero the moment `phase_started` fires (at 0%) rather than waiting for
its first `progress` tick.

**Verification:** `cd web && npm run build && npm run lint` - both clean,
pure addition, nothing else in the file needed adjustment, matching the
plan's prediction exactly. No frontend test runner exists in this repo
(`web/package.json` has no `vitest`/`jest` - confirmed, not introduced
here per the design spec's explicit scope). The plan's documented manual
check (start a real pipeline run, watch the transport bar cross a phase
boundary in a live WebView session) is outside what this sandbox can
exercise - flagging that as unverified-by-me rather than claiming it was
checked; the fix is a direct, minimal, single-branch addition whose logic
was verified by code review against every consumer (`Home.tsx`,
`useRenderClock`) as the design spec itself lays out, but an actual
running-app check is still worth doing before this ships.

**Commit** (branch `slice-22-progress-bar`, pushed): `99fe376`, plus this
ledger entry.

**Deferred / explicitly out of scope** (per the design spec): adding a
frontend test runner; the single last-event `progress`/`phaseStarted`
reducer path (already correct, not touched); `foldPhases` (separate
function/consumer, not implicated in this bug).

---

## Slice 23 — App Health improvements (2026-08-15)

Implemented per `docs/superpowers/specs/2026-08-11-slice-23-app-health-design.md`
and `docs/superpowers/plans/2026-08-11-slice-23-app-health-plan.md`, all 4
tasks, on the single accumulating `development` branch (per instruction:
one branch for every slice going forward, no more per-slice branches, no
more checking whether a previous slice's branch merged to `main` before
starting the next one).

**Mid-slice housekeeping, worth recording:** the `development` branch was
deleted and recreated remotely partway through this slice (PRs #8 "Slice
22 done" and #9 "Slice 21 duration narration" merged into it on GitHub).
Reconciling brought in Slice 21's and 22's code cleanly, but
`.superpowers/sdd/progress.md` on the remote had regressed to its
pre-Slice-20 state - missing the Slice 20 *and* Slice 21 ledger entries
entirely (fewer lines than even `main`, which has Slice 20's entry via
its own merged PR). Reconstructed the file from this session's own commit
history (`aca86f7` for the 19+20+22 shape, `edf3285` for the Slice 21
entry) rather than accept the loss - restored in its own commit
(`7ccb702`) before continuing Task 3.

**Task 1 - Recent Errors always shows something.** `ErrorLog.tsx`'s
expansion previously showed nothing when a row's `error.detail` was
empty - true for every `low`-severity `ErrorStore.degraded()` entry, so
Recent Errors' whole "click a row to expand" affordance silently did
nothing for the most common severity. `contextText` (the same formatting
`promptFor` already put on the clipboard) and `expandedContent`
(`detail || contextText || <fallback sentence>`) mean a click always
reveals *something*.

**Task 2 - Logs button.** `POST /api/health/open-logs` (new route in
`routes_settings.py`) reveals `errors.json`'s folder in Explorer -
`os.startfile` + a `PYTEST_CURRENT_TEST` guard + `suppress(Exception)`,
the same shape `avg.export.capcut.export_take`'s existing Explorer reveal
already uses. Creates the folder first if it's missing (a fresh install
with zero recorded errors has never had reason to create it). Two new
backend tests, one of which spies on `os.startfile` directly - a bare
200-response check can't distinguish "the guard worked" from "the guard's
`AttributeError` got silently swallowed by `suppress(Exception)`" on this
non-Windows sandbox, matching `test_capcut.py`'s own established
technique for the identical problem.

**Task 3 - collapsible App Health sections.** `AppHealth.tsx`'s three
stage headers (`<header>`) became `<button>`s (full CSS reset - border,
background, font, text-align, width - per CLAUDE.md's native-control
rule, since a button is exactly the kind of control that rule exists
for) toggling a `health-checks--collapsed` class on each stage's check
list. Default state (`openStages`, seeded via `useEffect` keyed on
`stage.key` so a user's own toggle is never overridden by a later
report): passing stages start collapsed, failing stages start open - a
failure is this screen's content, not an error state to click past.
Chevron rotation reuses `StatusBar.tsx`'s existing `.statuspill__chevron
.is-open` rotate-the-svg pattern rather than inventing a new one.

**Final verification (Task 4):**
- `pytest tests -q`: **985 passed, 7 failed, 1 skipped** - same 7
  pre-existing failures as every prior slice's baseline (DPAPI, font
  resolution, real dev-machine legacy path). Zero regressions.
- `mypy avg`: 690 errors - identical to the pre-slice baseline (this
  slice's one new mypy-relevant line, the new route's bare `dict` return,
  matches the file's own existing style throughout `routes_settings.py`,
  not a new pattern).
- `cd web && npm run build && npm run lint`: clean (0 errors, 0
  warnings) for both Tasks 2's frontend half and Task 3's CSS/JSX.

**Commits** (branch `development`, pushed after this entry): `03b294f`
Task 1, `33dadbb` Task 2, `3fc1f3e`+`7ccb702` the mid-slice reconciliation,
`42f5569` Task 3, plus this ledger entry for Task 4.

**Deferred / explicitly out of scope** (per the design spec): a
dedicated "open explorer" abstraction shared across `capcut.py` and this
new route (two call sites, not yet worth extracting); persisting
`openStages` across a screen remount (session-only, resets to the
seeded default on next visit - matching every other screen's use of
plain `useState` for UI-only toggle state); a manual live-app check of
the collapse animation, same caveat as Slice 22's manual-check note -
this sandbox cannot exercise a running WebView session.

---

## Slice 24 — Quick Panel resize, puller position, Preview refresh (2026-08-15)

Implemented per `docs/superpowers/specs/2026-08-14-slice-24-quickpanel-design.md`
and `docs/superpowers/plans/2026-08-14-slice-24-quickpanel-plan.md`, all 4
tasks, on the shared `development` branch. Frontend-only slice - no
backend files touched, full `pytest`/`mypy` sweep run anyway as a sanity
check (Step 7.1's habit), confirmed unaffected.

**Task 1 - puller alignment.** `.quickpanel-toggle`'s `top: 78px` put it
inside the Create Video button's own row instead of centered on the "A"
logo, per the item's own report. One-line CSS fix (`top: 22px` - the
logo's own vertical center: 16px rail padding + half the logo's 38px
height, minus half the toggle's own 26px height).

**Task 2 - resizable panel, wider floor.** `.quickpanel`'s fixed
`width: 380px; max-height: 70vh` moved to `QuickPanel.tsx`'s inline
style, driven by a new `size` state (persisted to `localStorage` under
`avg.quickpanel.size`, same pattern as the existing `position` state).
Bounds: 340-640px wide (340 leaves real margin below the old fixed 380
without inviting the five-tab-label wrap the item warned about),
320px-80vh tall. A corner drag handle (`.quickpanel__resize-handle`,
the universal `<textarea>`-resize affordance) reuses the header drag's
existing pointer-capture technique rather than a new implementation. A
resize that pushes the panel's own edge past the window boundary
re-clamps position via a `useEffect` keyed on `size` - covers the half
of "don't strand it off-screen" the existing window-resize listener
doesn't (that one only fires on the WINDOW changing, not the panel).
Checked the Preview tab's frame sizing against the new bounds before
concluding no CSS change was needed there: `.preview-frame img` scales
proportionally (`width: 100%; height: auto`), `.quickpanel__body` is
already `flex: 1; overflow: auto` - nothing clips at either bound.

**Task 3 - Preview refresh button.** A `Refresh` button in
`PreviewTab.tsx` bumps a `refreshKey` counter, threaded through
`PreviewPanel.tsx`'s dispatcher into `CaptionPreview`/`OverlayPreview`'s
existing debounced re-render effect (added to each one's dependency
array) - forces an immediate re-fetch rather than waiting out the
debounce. Deliberately NOT threaded into `VoicePreview`/`ImagePreview` -
both are paid, gated behind their own explicit "Generate a sample"
button (`confirm_paid: true`), and giving Refresh a second path to that
call would be exactly the money-rule regression this module's docstring
already warns against. Documented directly on the `refreshKey` prop
itself so a future reader extending this button doesn't wire it into
the paid previews by reflex.

**Final verification (Task 4):**
- `pytest tests -q`: **983 passed, 7 failed, 1 skipped** - same 7
  pre-existing failures as every prior slice's baseline. Zero
  regressions (expected - no backend file touched).
- `mypy avg`: 690 errors, identical to baseline (no `avg/` files
  touched).
- `cd web && npm run build && npm run lint`: clean (0 errors, 0
  warnings) after every task.

**Commits** (branch `development`, pushed after this entry): `13d15fc`
Task 1, `4f1b6dd` Task 2, `fcad369` Task 3, plus this ledger entry for
Task 4.

**Deferred / explicitly out of scope** (per the design spec): a visible
resize-bounds indicator (e.g. a tooltip on the handle); persisting size
independently per tab (one size for the whole panel, matching how
position already works); wiring Refresh into `VoicePreview`/
`ImagePreview` (see Task 3 above - deliberately never, not merely
deferred); the same manual live-app check caveat as Slices 22 and 23 -
drag-resize and the puller's visual alignment are verified by code
review and build/lint, not an actual running WebView session, which
this sandbox cannot exercise.

---

## Slice 25 — Ken Zoom intensity (2026-08-15)

Implemented per `docs/superpowers/specs/2026-08-14-slice-25-kenzoom-design.md`
and `docs/superpowers/plans/2026-08-14-slice-25-kenzoom-plan.md`, one
task, on the shared `development` branch.

**Root cause, traced in the design spec before any code was touched:**
`kb_intensity` was a `RenderRequest` field with correct plumbing all the
way down to the vendored render call, but no `SettingSpec` existed for
it - the Settings screen had nothing to render, so the value it always
got was `RenderRequest`'s own hardcoded default (100), regardless of
what a user might have wanted. Exact same class of bug
`avg.pipeline.phases.render._render_kwargs`'s own docstring already
names as its reason for existing (`fps`, `ken_burns`, `render_codec`,
`render_engine` all had this same gap once) - `kb_intensity` was simply
never added to that function when it was written.

**The fix, one function extension:** a new `SettingSpec(id="kb_intensity",
group="video", type="int", default=100, scope="channel",
invalidates="render")`, and `_render_kwargs` gains one more branch
(`isinstance(kb_intensity, int) and not isinstance(kb_intensity, bool)`
- the same `bool`-is-an-`int`-subclass guard `fps`'s branch already
uses). `invalidates="render"`, not `ken_burns`'s own `"director2"` -
intensity scales an already-decided per-scene motion at render time, so
changing it only needs a re-render, never re-planning scenes.

**Traced but explicitly not touched:** the mathematical meaning of
"100" (a neutral 1.0x multiplier on each scene's own designed motion
deviation from no-zoom, not "100% zoom") - documented in the setting's
own `help` text so the number means something to whoever sees it in the
UI. `kb_mode`'s pre-existing `"flat"`/`"dynamic"` default mismatch
between `RenderSettings` and the vendored function's own signature -
real, but unrelated to this item and out of scope per the design spec.

**Fallout:** the `SETTINGS`-count tripwire in `test_invalidation.py`
(66 → 67), the same routine update Slice 21's Task 2 needed for its own
two new settings.

**Final verification:**
- `pytest tests -q`: **984 passed, 7 failed, 1 skipped** - same 7
  pre-existing failures as every prior slice's baseline. Zero
  regressions.
- `mypy avg`: 690 errors, identical to baseline. Zero new errors.

**Commit** (branch `development`, pushed): `dd6e998`, plus this ledger
entry.

**Deferred / explicitly out of scope** (per the design spec): `kb_mode`
(not requested, unrelated pre-existing inconsistency); a Slider UI
treatment for `kb_intensity` (the Settings-selectors slice's concern -
the existing generic settings UI already renders an `int` setting
correctly without one).

---

## Slice 26 — Create Video → Research Topic toggle (2026-08-17)

Implemented per `docs/superpowers/specs/2026-08-14-slice-26-research-topic-design.md`
and `docs/superpowers/plans/2026-08-14-slice-26-research-topic-plan.md`
(both pulled onto `development` from `main` this session, along with
Slice 27's and Slice 28's docs - `main` had authored specs/plans for
26-28 that hadn't landed on `development` yet; verified every doc
filename shared between the two branches was byte-identical before
merging, so nothing existing was overwritten). One task, frontend only,
on the shared `development` branch.

**The backend needed nothing.** Traced before touching anything, per
the spec's own §0: `topic_research` is already a complete, fully-wired
`SettingSpec` - `BriefPhase.run()` already reads it
(`ctx.channel_values.get("topic_research")`), makes one extra AI call
via `build_research_prompt`, caches to `script/topic_research.txt`, and
degrades gracefully rather than failing the brief on error;
`ScriptPhase` already folds the cached result into Pass 1's narration
prompt as clearly-labeled `extra_context`. The only actual gap: unlike
`video_form`/`length_preset`/`visual_tone`, this channel-scoped setting
had no per-video override on the Create Video page - editable only
through the generic Settings screen. Confirmed this by grepping the
Create.tsx chip mechanism before writing anything, not assumed.

**The fix, one file:** a fourth chip in `Create.tsx`'s existing
`create__chips` row, reusing `.create-chip`/`.switch` verbatim - zero
new CSS. `overrides` widened from `Record<string, string>` to
`Record<string, unknown>` (matching `createProject`'s own parameter
type already - no API change) since `topic_research` is `type="bool"`
and `avg.core.settings_schema.validate_value` requires a real JSON
boolean for that type, no string coercion like its int/float handling
allows. A new `researchOn()` resolver mirrors `valueFor`'s
overrides-then-channel-then-schema-default chain but stays a real
boolean the whole way through, rather than generalizing `valueFor`
(which is typed to feed `Select`'s string `value` prop).

**One knock-on fix the plan's diff didn't call out:** widening
`overrides` broke `valueFor`'s own early return - `return overrides[id]`
no longer satisfied the function's `string` return type once `overrides`
held `unknown`. Wrapped with `String(...)`, a no-op for the three
existing string-valued chips, matching the function's own fallback two
lines below which already does the same coercion. `tsc -b` caught this
immediately; not a design-spec gap, just a plan-diff gap the design's
own reasoning (§1) already anticipated in prose without showing the
exact line.

**Final verification:**
- `cd web && npm run build && npm run lint`: both clean (0 errors, 0
  warnings).
- `pytest tests -q`: **991 passed, 2 failed, 1 skipped** - this
  session's established baseline (see below), unaffected by a
  frontend-only change. Ran anyway as a sanity check.

**Note on this session's test baseline vs. history:** earlier slices'
ledger entries cite "7 pre-existing failures." This session rebuilt the
Linux sandbox venv from scratch and replaced the DPAPI shim: instead of
a `ctypes.WinDLL` shim that merely no-ops (which fails every
DPAPI-touching test, not just genuinely platform-bound ones), it now
implements a working fake-but-reversible protect/unprotect substitute
wired into the real calling convention `avg/config/secrets.py` uses -
so secrets business-logic tests (round-trip, tamper -> None, atomicity,
env-var precedence, "no route leaks a key") now actually exercise their
real code path instead of blanket-failing. Net effect: baseline dropped
from 7 to 2. The 2 remaining are genuinely environment-bound and match
two of the three original named categories - `test_resolve_font_family_
extracts_family_name_from_a_font_file_path` (Windows path parsing) and
`test_scan_route_counts_real_legacy_projects` (needs the real
`D:\AI_Video_Gen\output` folder on an actual dev machine). The third
named category, "DPAPI round-trips," is the one now fixed by the
improved shim. Future sessions on this sandbox should expect **2**, not
7 - if a fresh clone doesn't reproduce this, the shim (kept outside the
repo at `~/pytest_linux_shim/sitecustomize.py`, `PYTHONPATH`-loaded) may
not have carried over and needs rebuilding.

**Commit** (branch `development`, pushed): `26f2a33`, plus this ledger
entry.

**Deferred / explicitly out of scope** (per the design spec): anything
backend (already correct and complete); exposing research on any other
creation surface (there is only one today). **Not yet done:** the
spec's own manual check (toggle Research on, confirm
`script/topic_research.txt` gets written for a real project; toggle
off, confirm it isn't) - this sandbox has no live WebView2 session to
click through, same standing limitation as every prior UI slice
(Slices 22-25's ledger entries carry the identical caveat). Flagging
for a real click-through when one is available.

---

## Slice 27 — Studio screen: nav, custom players, phase previews (2026-08-17)

Implemented per `docs/superpowers/specs/2026-08-14-slice-27-studio-design.md`
and `docs/superpowers/plans/2026-08-14-slice-27-studio-plan.md` (pulled
onto `development` from `main` alongside Slice 26's and Slice 28's docs
- see Slice 26's own entry). Six tasks, each verified and committed
separately on `development`:

1. `1648445` - Studio rail opens the latest opened project
2. `b2fa17e` - per-take `script`/`render` substates in the takes API
3. `a205f5a` - `useMediaPlayer`, the shared player state/controls hook
4. `eff4ab5` - custom video/voice players replace native `<video
   controls>`/`<audio controls>` everywhere
5. `fc92a8e` - Script's partial ("segmenting into scenes") state
6. `ed1bcf6` - Render's proper base/composite preview

**1. Studio nav.** `App.tsx`'s `openProject` now persists the id to
`localStorage` - every navigation-to-Studio path already goes through
that one function, so recording it there covers Home's card click,
Create's `onCreated`, and Resume without instrumenting each separately.
A new `openStudio()` (the rail item's actual handler, previously a
`<span aria-hidden>` with no click handler at all) resolves the stored
id, confirms it still exists (archived counts - only a genuinely
deleted or never-opened id falls through), and falls back to Home
otherwise.

**2. Backend substates.** `GET /api/projects/{id}/takes` gains
`substates: {take_id: {script, render}}`, the same two functions (
`script_substate`, `render_substate`) `GET /api/projects` already calls
for a project's current take, looped over every take via `take_root`
instead of one. **Plan correction:** the plan assumed `take_root` was
"almost certainly" already imported in `routes_takes.py` - it wasn't,
only `take_folder` was. Added it to the existing import. This matters
per `CLAUDE.md`'s own rule (`take_root`, never `take_folder`, for
reading a take's files) - `app.py`'s equivalent route already gets this
right; mirrored its choice rather than the plan's assumption. One
purpose-built test (not `_add_second_take`, whose 14-byte fake output
is under `render_substate`'s 1024-byte floor and can't exercise
`"base"`); confirmed it actually failed (`KeyError: 'substates'`)
before the implementation existed.

**3. `useMediaPlayer`.** One hook, one state machine (playing,
currentTime, duration, buffering, muted, error) and controls
(togglePlay, seek, toggleMute) against a `HTMLMediaElement` ref -
works unmodified for both `HTMLVideoElement` and `HTMLAudioElement`.
Also exports `formatPlaybackTime` (seconds -> `M:SS`), deliberately
not a consolidation of `Studio.tsx`'s/`TakeStrip.tsx`'s own
`formatDuration` (milliseconds, a phase-duration concern, not media
playback position).

**4. Custom players.** `Player.tsx` rewritten onto the hook - video
keeps its `.player` frame (still Range-seekable) but drops the
browser's own controls for the shared `.media-player` row. New
`VoicePlayer.tsx` is the audio counterpart, plus a mute button (the
one place several narration takes compared back-to-back makes an
unexpected earbud-blast a real concern). Wired into all three
remaining native-control spots: `VoiceBody`'s two narration tracks and
`PreviewPanel.tsx`'s `VoicePreview` sample - the third was found during
this task's own audit, not originally scoped to this slice, and fixed
here anyway rather than left as a lingering fourth spot. Confirmed
zero `<audio>`/`<video>` elements remain anywhere outside the two new
components themselves. **Two plan corrections:** the CSS draft's own
comment flagged its token names as guesses needing verification -
`--surface-3` and `--danger` don't exist in `tokens.css`. Used the real
hover pattern `.rail__item:hover` already uses
(`color-mix(in srgb, var(--text) 6%, transparent)`) and the real name
`--bad` (confirmed via `.btn--danger`). Also used `--radius-sm` rather
than `--radius-md` for the new rule - `--radius-md` is referenced by
`.subphase` elsewhere in this file but was never actually defined in
`:root`, a pre-existing latent bug not worth propagating into a second
rule.

**5. Script partial state.** `ScriptBody` derives a `partial` boolean
(narration exists, scenes doesn't yet) and shows "Draft written —
segmenting into scenes…" with the exact `.dot.pulse` / `var(--accent)`
affordance `Studio.tsx` already uses for a running phase. Confirmed
this genuinely mirrors `avg.pipeline.phases.writing.script_substate`'s
own server-side derivation (`has_narration and not has_scenes ->
"partial"`) before writing it.

**6. Render preview.** `TakesResponse` (`api.ts`) gains `substates`,
threaded through `Studio.tsx`'s `refreshTakes` into new state, and from
there into `ArtifactDetail` as a `renderSubstate` prop keyed off
whichever take is actually *selected* - not `project.render_substate`,
which only ever reflects the project's current take (the phase rail's
own compact status-dot wiring via `displayStatuses` stays on that
current-take-only substate, unchanged - out of scope per the design
spec's own §5, not an oversight). A live render's progress view now
names the active pass ("Step 1 of 2 — base video" / "Step 2 of 2 —
overlays & captions") via a frontend `RENDER_BASE_PERCENT_SPAN`
mirroring the backend's actual `_BASE_PERCENT_SPAN = 70` - confirmed
name and value before writing it, not assumed. Render's pending state
gets its own two-step description. A finished `"base"`-substate render
says overlays/captions weren't baked in. **One plan gap, not a design
gap:** the design spec explicitly says this base-only note belongs in
"both the not-current-take and the normal cases," but the plan's own
diff only showed editing the second (current-take) branch. Added it to
the first (not-current-take, summary-only) branch too - the design's
own reasoning applies just as much to a take you're viewing but
haven't made current.

**Final verification (Task 7):**
- `pytest tests -q`: **992 passed, 2 failed, 1 skipped** - this
  session's established 2-failure baseline (see Slice 26's entry for
  why it's 2 and not the historical 7), zero regressions across all
  six tasks.
- `mypy avg`: **690 errors** - unchanged from baseline throughout.
- `cd web && npm run build && npm run lint`: clean at every single task
  checkpoint, including Task 6's on the first attempt - every plan
  string Task 6 referenced (the backend constant's exact name and
  value, `ArtifactDetail`'s prop shape) was verified against the real
  files before writing, which is exactly why nothing needed fixing
  after the fact there, unlike three earlier tasks in this same slice.

**Pattern worth naming for whoever picks up Slice 28 next:** this
slice's plan had more assumption-vs-reality gaps than Slice 26's single
task did (missing `take_root` import, two invented CSS token names, one
missing branch in a two-branch diff) - all caught by verifying each
plan claim against the actual file before applying it, none by trusting
the plan's own confidence ("almost certainly already imported"). Task 6,
where every claim actually held on first check, produced a clean build
on the first attempt; the earlier tasks that skipped straight to
applying a diff needed a follow-up fix each time. Read-before-write is
cheaper than fix-after-write.

**Deferred / not done:** every manual click-through this slice's
design spec calls for (Studio nav round-trip; both players' real
playback and keyboard control; Script's segmenting message during an
actual Pass 2 gap; Render's step label flipping past 70% and the
base-only note on a real incomplete-composite take) - this sandbox has
no live WebView2 session, the same standing limitation noted on every
UI slice back through Slice 22. Flagging for a real click-through
when one is available; nothing here should be treated as visually
confirmed, only build/lint/type-clean and logically verified against
the backend it mirrors.

**Commits** (branch `development`, pushed): `1648445`, `b2fa17e`,
`a205f5a`, `eff4ab5`, `fc92a8e`, `ed1bcf6`, plus this ledger entry.

---

## Slice 28 — Status pill: circular active, top-left pull-tab idle (2026-08-17)

Implemented per `docs/superpowers/specs/2026-08-16-slice-28-statuspill-
design.md` and `docs/superpowers/plans/2026-08-16-slice-28-statuspill-
plan.md` (pulled onto `development` from `main` alongside Slice 26's
and Slice 27's docs - see Slice 26's own entry). One task, frontend
only, one commit (`4993f26`).

A shape-and-position change on top of state that already existed, not
a new state machine: `docked` (`kind === "ready" && !working &&
!hasSticky && !panelOpen`) was already computed correctly and untouched
here. `.statuspill` goes from an always-top-right pill that grows a
label/clock/"+N" count inline to a fixed 34px circle anchored top-left
(matching `.quickpanel-toggle`/`.health-stage__chevron`'s own circular-
control convention). `.statuspill--docked` goes from a flush-right-edge
sliver to a narrow pull-tab near the (now top-left) corner with a slow
3.2s periodic tug (`statuspill-pull`) as its "grab me" affordance,
replacing the proximity-to-the-edge cue that stopped applying once idle
moved off the edge itself.

The label/clock/overflow-count text that used to render inline
(`.statuspill__expand` and its three children) is gone from the DOM
entirely - that content already existed unchanged as rows in the
click-to-expand detail panel, so the circle signals rather than
duplicates. **Accessibility carry-over, worth flagging explicitly since
it's easy to miss from the diff alone:** removing that inline text
doesn't remove the information - `StatusBar.tsx`'s `aria-label` now
carries exactly what used to render visibly (the label text, plus "…"
while a clock would have been ticking) rather than the previous
`docked ? "Ready" : kind === "ready" && !working ? "Ready" : undefined`,
which only ever covered the two states that had no inline text to
begin with. A screen reader announces the same information a sighted
user could always get by reading the pill directly; it just can't get
it that way anymore, since nothing renders inline now. Two now-dead
pieces removed alongside: the `now`/`setInterval` ticking-clock state
(confirmed via search that nothing else read `now` before removing it)
and `@keyframes pill-pop` (confirmed its only caller was
`.statuspill__label`, deleted in the same edit).

**No plan corrections needed this task** - every string, CSS rule
location, and cited precedent (the reduced-motion block's exact
location, `--motion-scale`, `.quickpanel-toggle`'s 26px) matched the
real files exactly on first check, unlike Slice 27's four gaps. Worth
noting as the clean counterexample to that slice's own "read before
write" lesson: this plan held up because it read the same way.

**Final verification:**
- `pytest tests -q`: **992 passed, 2 failed, 1 skipped** - baseline
  unaffected (frontend-only change; ran anyway as a sanity check).
- `mypy avg`: **690 errors** - unchanged.
- `cd web && npm run build && npm run lint`: clean on the first
  attempt.

**Deferred:** every manual check the design spec's §3 calls for (idle's
visible tug; the docked-to-circle morph landing in place, no jump
across the screen; the accessible name matching what used to be
visible; `prefers-reduced-motion` stopping the tug) - this sandbox has
no live WebView2 session, the same standing limitation noted on every
UI slice back through Slice 22.

**All three slices pulled from `main` this session (26, 27, 28) are now
complete on `development`.** No further specs or plans exist past
Slice 28 as of this session - the next slice needs a fresh brainstorm →
spec → plan before implementation can start.

**Commit** (branch `development`, pushed): `4993f26`, plus this ledger
entry.
