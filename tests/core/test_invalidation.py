"""Tests for the invalidation map: which phases a settings change re-runs.

`first_invalid_phase`/`phases_to_rerun` are pure - no project, no I/O - so
these tests exercise them purely through setting ids and the real
`REGISTRY`/`SETTINGS` catalogues, deliberately not faking either.
"""

from avg.core.invalidation import first_invalid_phase, phases_to_rerun
from avg.core.settings_schema import SETTINGS
from avg.pipeline.registry import BY_KEY, REGISTRY


def test_every_setting_declares_what_it_invalidates():
    """A setting added later must not silently default to invalidating nothing.

    The dangerous failure is not a wrong row today - it is someone adding a
    setting in six months, forgetting this field, and re-runs quietly doing
    the wrong thing from then on. `invalidates` has no default for exactly
    this reason; this test is what makes forgetting it unmergeable.
    """
    missing = [s.id for s in SETTINGS if not hasattr(s, "invalidates")]
    assert missing == [], f"settings with no invalidates field: {missing}"


def test_every_invalidates_value_is_a_real_phase_key():
    bad = [(s.id, s.invalidates) for s in SETTINGS
           if s.invalidates is not None and s.invalidates not in BY_KEY]
    assert bad == [], f"settings pointing at phases that do not exist: {bad}"


def test_dynamic_pauses_invalidates_the_script_not_voice():
    spec = next(s for s in SETTINGS if s.id == "dynamic_pauses_enabled")
    assert spec.invalidates == "script"


def test_the_whole_catalogue_is_present_and_uniquely_identified():
    """Sanity check on the catalogue's size and shape.

    Was `len(SETTINGS) == 39` before Slice 6B added the 16-entry `models`
    group (the default text model plus model/backup/on-failure for five
    router phases), 55 before `render_threads`, and 56 before three dead
    switches (python_exe, performance_mode, run_as_admin) were removed, and
    53 before Slice 8 added `live_list_source`, 54 before Slice 9
    added `verbose_log`, 55 before it added `topic_research`, and 59
    before Slice 11 added the images group's own backup/on-failure triplet
    (`image_provider_backup`, `image_model_backup`, `image_on_failure`),
    mirroring the text router's shape, 62 before Slice 12 added the
    `app` group's `motion_scale`, `startup_check` and the channel-scoped
    `settings_source`, and 65 before the Slice 14 review's Important 4
    added the `overlay` group's three brand colours (`overlay_accent1`,
    `overlay_accent2`, `overlay_highlight`), and Slice 16 added
    `max_parallel_jobs`, 65 before Slice 19 removed `settings_source`
    (superseded by `resolve_channel_settings`'s per-field resolution), and
    64 after Slice 19 Task 7 also removed `aspect_ratio` (now derived from
    `video_form` - `avg.pipeline.create.derive_aspect_ratio` - rather than
    stored as its own setting), 66 after Slice 21 added the `voice`
    group's `voice_trim_aggressiveness` and
    `voice_max_removable_silence_pct`, and 67 after Slice 25 added
    `kb_intensity`, and 70 after Slice 31 sub-slice 1 added the `filters`
    group's `filters_intensity`, `filters_allow` and `filters_block`, and
    72 after Slice 31 sub-slice 2 added the `images` group's
    `recycle_enabled` and `recycle_ratio`. The
    count is kept as a
    deliberate tripwire on accidental additions or losses; the uniqueness
    check is what actually matters, since `BY_ID` would silently swallow a
    duplicated id.
    """
    assert len(SETTINGS) == 72
    assert len({s.id for s in SETTINGS}) == len(SETTINGS)


def test_changing_video_form_invalidates_visuals_not_merely_brief():
    """Slice 19 Task 7's own consequence #1, pinned rather than left
    implicit: `video_form`'s own `invalidates` says "brief" (phase 0), but
    because `brief` is the first phase, its invalidation sweep already
    carries every later phase with it - `visuals` included. This is what
    makes a `video_form` change (which now also changes the derived
    `aspect_ratio`) actually re-run the phase that bakes the frame
    dimensions into the generated images, not just the phase that writes
    the script."""
    assert "visuals" in phases_to_rerun(["video_form"])


def test_change_set_spanning_two_phases_picks_the_earlier_one():
    # font_family -> captions (id 6), voice_id -> voice (id 4).
    # The earlier of the two is voice.
    assert first_invalid_phase(["font_family", "voice_id"]) == BY_KEY["voice"].id


def test_change_set_of_only_non_creative_settings_returns_none():
    assert first_invalid_phase(["theme", "python_exe", "run_as_admin", "performance_mode"]) is None


def test_unknown_setting_id_is_ignored_rather_than_raising():
    assert first_invalid_phase(["this_setting_does_not_exist"]) is None
    # Mixed with a real, invalidating id: the unknown one is simply ignored.
    assert first_invalid_phase(["this_setting_does_not_exist", "fps"]) == BY_KEY["render"].id


def test_empty_change_set_returns_none():
    assert first_invalid_phase([]) is None


def test_phases_to_rerun_returns_keys_in_registry_order_and_ends_at_render():
    result = phases_to_rerun(["voice_id"])
    expected = [phase.key for phase in REGISTRY if phase.id >= BY_KEY["voice"].id]
    assert result == expected
    assert result[-1] == "render"


def test_phases_to_rerun_empty_when_nothing_invalidated():
    assert phases_to_rerun(["theme"]) == []


def test_phases_to_rerun_from_brief_is_the_whole_pipeline():
    result = phases_to_rerun(["video_form"])
    assert result == [phase.key for phase in REGISTRY]
    assert result[0] == "brief"
    assert result[-1] == "render"


def test_dynamic_pauses_change_reruns_from_script_not_voice():
    """Regression guard for the one row nobody guesses right."""
    result = phases_to_rerun(["dynamic_pauses_enabled"])
    assert result[0] == "script"
    assert "script" in result
    # voice is still re-run because it comes after script in REGISTRY order,
    # but the *start* of the re-run is script, not voice.
    assert result[0] != "voice"
