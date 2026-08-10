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
- [ ] Task 8: frontend `useSettingsData.ts` — ownership and scope
- [ ] Task 9: frontend ownership UI in `SettingsGroup.tsx`/fields
- [ ] Task 10: frontend the category table and tab strip
- [ ] Task 11: frontend the Global/Channel toggle
- [ ] Task 12: small fix — remove "Run again" from phase-error surfaces
- [ ] Task 13: small fix — the "A" logo becomes the main menu

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
