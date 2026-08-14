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
