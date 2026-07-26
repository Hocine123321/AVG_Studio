"""Tests for `avg.pipeline.personality` and `avg.pipeline.prompts`.

The central risk this module exists to prevent is the legacy app's real
`visual_tone` bug: a catalogue id (e.g. `"documentary_cinematic"`) reaching
a prompt instead of its ~40-word descriptive text. Every builder-function
test below therefore asserts BOTH that the descriptive text is present AND
that the bare id string is absent - presence alone would not catch a
regression where the id leaks in ADDITION to (rather than instead of) the
resolved text.
"""

from __future__ import annotations

from unittest import mock

import pytest

from avg.pipeline import personality, prompts
from avg.pipeline.personality import PersonalityPreset, get_preset, load_presets
from avg.pipeline.prompts import (
    CreativeInputs,
    build_brief_prompt,
    build_director_style_bible,
    build_narration_prompt,
    build_pause_section,
    build_scene_count_rule,
    build_scene_segmentation_prompt,
    load_catalog,
    resolve_brand_preset_label,
    resolve_visual_tone,
)

KNOWN_TONE_ID = "documentary_cinematic"
KNOWN_TONE_SNIPPET = "grounded cinematic realism"  # start of the bundled hint text


# ── personality.py ───────────────────────────────────────────────────────

def test_bundled_personality_file_parses_all_three_presets():
    presets = load_presets()
    assert set(presets) == {"documentary", "news_report", "zack_d_films"}


def test_preset_body_never_contains_the_description_line():
    presets = load_presets()
    for preset in presets.values():
        assert "## DESCRIPTION" not in preset.body
        assert preset.description  # every bundled preset has one


def test_preset_body_is_non_trivial_prose():
    # Guards against a parsing regression that captures an empty/near-empty
    # body (e.g. a broken regex swallowing everything after the header).
    for preset in load_presets().values():
        assert len(preset.body) > 200


def test_get_preset_known_name_is_case_insensitive():
    presets = load_presets()
    a = get_preset("documentary", presets)
    b = get_preset("DOCUMENTARY", presets)
    assert a == b
    assert a.name == "documentary"


def test_get_preset_unknown_name_degrades_to_first_preset_not_empty():
    presets = load_presets()
    fallback = get_preset("some_renamed_preset_that_no_longer_exists", presets)
    assert fallback.name == next(iter(presets))
    assert fallback.body


def test_get_preset_blank_name_degrades_to_first_preset():
    presets = load_presets()
    assert get_preset("", presets).name == next(iter(presets))
    assert get_preset("   ", presets).name == next(iter(presets))


def test_get_preset_with_empty_catalog_returns_generic_and_never_raises():
    # Simulates personality.txt missing/unparseable entirely.
    fallback = get_preset("documentary", presets={})
    assert fallback.name == "generic"
    assert fallback.body  # never an empty persona


def test_load_presets_missing_file_returns_empty_dict_not_raise(tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    assert load_presets(missing) == {}


def test_load_presets_from_explicit_malformed_file_degrades(tmp_path):
    junk = tmp_path / "junk.txt"
    junk.write_text("not a personality file at all\njust prose\n", encoding="utf-8")
    presets = load_presets(junk)
    assert presets == {}
    # get_preset on an empty catalog still returns something usable.
    assert get_preset("documentary", presets).body


def test_personality_assets_dir_dev_checkout_points_at_bundled_copy():
    d = personality.assets_dir()
    assert (d / "personality.txt").is_file()
    assert (d / "dynamic_options.txt").is_file()


# ── prompts.py: catalogue loading + resolution ───────────────────────────

def test_load_catalog_parses_video_tone_category():
    catalog = load_catalog()
    tone = catalog["video_tone"][KNOWN_TONE_ID]
    assert tone.label.startswith("Documentary")
    assert KNOWN_TONE_SNIPPET in tone.hint


def test_load_catalog_parses_brand_presets_category():
    catalog = load_catalog()
    assert "modern_dark" in catalog["brand_presets"]


def test_load_catalog_missing_file_returns_empty_not_raise(tmp_path):
    assert load_catalog(tmp_path / "nope.txt") == {}


def test_resolve_visual_tone_expands_known_id_to_label_and_hint():
    resolved = resolve_visual_tone(KNOWN_TONE_ID)
    assert KNOWN_TONE_SNIPPET in resolved
    assert resolved.startswith("Documentary")
    assert KNOWN_TONE_ID not in resolved


def test_resolve_visual_tone_passes_through_already_descriptive_text():
    text = "A hand-typed custom tone, spaces and all."
    assert resolve_visual_tone(text) == text


def test_resolve_visual_tone_unknown_id_degrades_to_raw_value():
    assert resolve_visual_tone("not_a_real_catalog_id") == "not_a_real_catalog_id"


def test_resolve_visual_tone_blank_returns_blank():
    assert resolve_visual_tone("") == ""
    assert resolve_visual_tone("   ") == ""


def test_resolve_brand_preset_label_known_id():
    label = resolve_brand_preset_label("modern_dark")
    assert "Modern Dark" in label
    # the raw hex/rgba colour payload must never appear in prompt text
    assert "#" not in label


def test_resolve_brand_preset_label_unknown_id_degrades():
    assert resolve_brand_preset_label("no_such_brand") == "no_such_brand"


def test_resolve_brand_preset_label_blank_returns_blank():
    assert resolve_brand_preset_label("") == ""


# ── prompts.py: small pure helpers ───────────────────────────────────────

def test_scene_count_rule_exact_vs_organic():
    exact = build_scene_count_rule(3, 15, exact_scene_count=7)
    assert "EXACTLY 7" in exact
    organic = build_scene_count_rule(3, 15, exact_scene_count=0)
    assert "between 3 and" in organic
    assert "15 scenes" in organic


def test_pause_section_frequency_wording_differs():
    low = build_pause_section("low")
    high = build_pause_section("high")
    medium = build_pause_section("medium")
    assert "SPARINGLY" in low
    assert "LIBERALLY" in high
    assert "occasionally" in medium
    assert low != high != medium


# ── prompts.py: the visual_tone trap, across every builder ──────────────

def _inputs(**overrides) -> CreativeInputs:
    base = dict(
        topic="The Antikythera Mechanism",
        content_shape="documentary",
        visual_tone=KNOWN_TONE_ID,
        style_lock="",
        brand_preset="modern_dark",
        form_label="Short-form (9:16)",
        aspect_ratio="9:16",
        duration_seconds=30,
    )
    base.update(overrides)
    return CreativeInputs(**base)


def test_brief_prompt_contains_tone_description_never_the_id():
    text = build_brief_prompt(_inputs())
    assert KNOWN_TONE_SNIPPET in text
    assert KNOWN_TONE_ID not in text


def test_narration_prompt_contains_tone_description_never_the_id():
    text = build_narration_prompt(_inputs())
    assert KNOWN_TONE_SNIPPET in text
    assert KNOWN_TONE_ID not in text


def test_scene_segmentation_prompt_contains_tone_description_never_the_id():
    text = build_scene_segmentation_prompt(_inputs(), narration="Some finished narration text.")
    assert KNOWN_TONE_SNIPPET in text
    assert KNOWN_TONE_ID not in text


def test_director_style_bible_contains_tone_description_never_the_id():
    text = build_director_style_bible(_inputs(), emotional_arc="hook to payoff")
    assert KNOWN_TONE_SNIPPET in text
    assert KNOWN_TONE_ID not in text


def test_brand_preset_label_present_not_raw_hex_in_style_bible():
    text = build_director_style_bible(_inputs())
    assert "Modern Dark" in text
    assert "#" not in text


@pytest.mark.parametrize(
    "builder",
    [
        lambda inp: build_brief_prompt(inp),
        lambda inp: build_narration_prompt(inp),
        lambda inp: build_scene_segmentation_prompt(inp, narration="Narration."),
        lambda inp: build_director_style_bible(inp),
    ],
)
def test_every_builder_routes_tone_through_the_single_resolver(builder):
    """Structural guard: patch the ONE function allowed to turn a
    `visual_tone` id into text, and confirm every builder (a) actually
    calls it and (b) never independently interpolates the raw id anywhere
    in its output. This is what makes a catalogue id reaching a prompt
    "structurally impossible" rather than merely "not currently
    observed" - a future builder that bypasses `resolve_visual_tone` and
    interpolates `inputs.visual_tone` directly would fail this test even
    if it happened to also call the resolver for some other field.
    """
    sentinel = "@@RESOLVED-TONE-SENTINEL@@"
    with mock.patch.object(prompts, "resolve_visual_tone", return_value=sentinel) as mocked:
        text = builder(_inputs())
    assert mocked.called
    assert sentinel in text
    assert KNOWN_TONE_ID not in text


def test_unknown_content_shape_degrades_sensibly_not_raise():
    inputs = _inputs(content_shape="a_preset_that_was_renamed_away")
    text = build_brief_prompt(inputs)  # must not raise
    assert text  # still produced real content, not an empty/blank prompt


def test_missing_personality_catalogue_degrades_to_generic_body(tmp_path, monkeypatch):
    # Point the bundled-catalog cache at an empty directory so every
    # lookup misses, simulating a broken/incomplete install.
    personality._load_bundled.cache_clear()
    monkeypatch.setattr(personality, "assets_dir", lambda: tmp_path)
    try:
        text = build_narration_prompt(_inputs())
        assert "You are a video script writer" in text  # the generic fallback body
    finally:
        personality._load_bundled.cache_clear()


# ── prompts.py: content_shape produces materially different prompts ─────

ALL_SHAPES = ("documentary", "news_report", "zack_d_films")


def test_each_content_shape_has_a_distinct_description():
    presets = load_presets()
    descriptions = {name: presets[name].description for name in ALL_SHAPES}
    assert len(set(descriptions.values())) == len(ALL_SHAPES)


def test_each_content_shape_produces_a_materially_different_brief_prompt():
    prompts_by_shape = {shape: build_brief_prompt(_inputs(content_shape=shape)) for shape in ALL_SHAPES}
    texts = list(prompts_by_shape.values())
    # Pairwise: no two shapes produce the same prompt, and each shape's own
    # description string shows up only in its own prompt.
    assert len(set(texts)) == len(ALL_SHAPES)
    presets = load_presets()
    for shape, text in prompts_by_shape.items():
        assert presets[shape].description in text
        for other in ALL_SHAPES:
            if other != shape:
                assert presets[other].description not in text


def test_each_content_shape_produces_a_materially_different_narration_prompt():
    prompts_by_shape = {shape: build_narration_prompt(_inputs(content_shape=shape)) for shape in ALL_SHAPES}
    presets = load_presets()
    texts = list(prompts_by_shape.values())
    assert len(set(texts)) == len(ALL_SHAPES)
    for shape, text in prompts_by_shape.items():
        # The persona body (a large, distinctive block of prose) must be
        # the one that's actually injected, not some other preset's.
        assert presets[shape].body in text
        for other in ALL_SHAPES:
            if other != shape:
                assert presets[other].body not in text


def test_narration_prompt_word_bounds_and_topic_are_present():
    text = build_narration_prompt(_inputs(topic="Roman concrete"), word_min=55, word_max=80)
    assert "Roman concrete" in text
    assert "55 to 80 words" in text


def test_brief_prompt_duration_phrasing_seconds_vs_minutes():
    # 60 and 180 are chosen to divide evenly by 60 - avoids the
    # round-half-to-even ambiguity of e.g. 90/60 == 1.5 (both Python's
    # round() and the legacy .NET `[Math]::Round` this mirrors round
    # halves to even, so exact behaviour at the midpoint is an accepted
    # quirk this port preserves rather than "fixes").
    short = build_brief_prompt(_inputs(duration_seconds=45))
    assert "45 seconds" in short
    one_min = build_brief_prompt(_inputs(duration_seconds=60))
    assert "1 minute" in one_min
    assert "1 minutes" not in one_min
    three_min = build_brief_prompt(_inputs(duration_seconds=180))
    assert "3 minutes" in three_min


def test_scene_segmentation_prompt_includes_style_lock_tail_when_set():
    with_lock = build_scene_segmentation_prompt(
        _inputs(style_lock="shot on 35mm film, muted palette"), narration="Text."
    )
    assert "shot on 35mm film, muted palette" in with_lock
    without_lock = build_scene_segmentation_prompt(_inputs(style_lock=""), narration="Text.")
    assert "Append this style tag verbatim" not in without_lock


def test_scene_segmentation_prompt_preserves_narration_verbatim_instruction_and_text():
    narration = "This is the exact narration text that must survive untouched."
    text = build_scene_segmentation_prompt(_inputs(), narration=narration)
    assert narration in text
    assert "the narration is FINAL" in text
