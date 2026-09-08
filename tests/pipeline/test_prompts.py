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


def _parse_catalog_for_test(raw: str):
    from avg.pipeline.prompts import _parse_catalog

    return _parse_catalog(raw)


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


def test_resolve_brand_style_bible_known_preset_matches_legacy_defaults():
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("neon_cyber")
    assert bible["motion"] == "punchy fast pushes and quick pans, high energy"
    assert bible["transitions"] == "cut, whip, slide_left"
    assert bible["grade"] == "vibrant"


def test_resolve_brand_style_bible_unknown_id_returns_the_fallback():
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("some_id_that_does_not_exist")
    assert bible == {
        "motion": "balanced push-ins with occasional drifts",
        "transitions": "cut, crossfade",
        "grade": "none",
    }


def test_resolve_brand_style_bible_blank_id_returns_the_fallback():
    from avg.pipeline.prompts import resolve_brand_style_bible

    assert resolve_brand_style_bible("")["grade"] == "none"


def test_resolve_brand_style_bible_every_preset_in_the_bundled_catalog_resolves():
    # Every id catalog() actually ships must produce a full, valid bible -
    # including the 5 presets this app added that legacy never had.
    from avg.pipeline.prompts import resolve_brand_style_bible

    catalog = load_catalog()
    for preset_id in catalog["brand_presets"]:
        bible = resolve_brand_style_bible(preset_id)
        assert bible["motion"]
        assert bible["transitions"]
        assert bible["grade"]


def test_resolve_brand_style_bible_payload_override_wins_over_the_default():
    catalog = _parse_catalog_for_test(
        "## CATEGORY: brand_presets\n"
        "## OPTION: modern_dark | Modern Dark | motion=totally different motion;grade=noir\n"
    )
    from avg.pipeline.prompts import resolve_brand_style_bible

    bible = resolve_brand_style_bible("modern_dark", catalog)
    assert bible["motion"] == "totally different motion"
    assert bible["grade"] == "noir"
    # transitions wasn't overridden - the built-in default still applies.
    assert bible["transitions"] == "cut, crossfade"


def test_style_bible_text_includes_the_brand_motion_and_grade():
    inputs = _inputs(brand_preset="neon_cyber")
    text = build_director_style_bible(inputs)
    assert "brand motion feel: punchy fast pushes and quick pans, high energy" in text
    assert "brand grade: vibrant" in text


def test_style_bible_text_has_no_brand_lines_when_brand_preset_is_blank():
    inputs = _inputs(brand_preset="")
    text = build_director_style_bible(inputs)
    assert "brand" not in text


def test_director2_prompt_omits_filters_section_when_catalog_is_empty():
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(_inputs(), ["Scene one."], filter_catalog={})
    assert "AVAILABLE FILTERS" not in prompt


def test_director2_prompt_omits_filters_section_when_catalog_is_none():
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(_inputs(), ["Scene one."])
    assert "AVAILABLE FILTERS" not in prompt


def test_director2_prompt_omits_filters_section_when_intensity_is_zero():
    """Regression: 0 is documented as 'off entirely', not merely the
    sparsest wording tier - a non-empty catalog must not leak into the
    prompt just because intensity dropped below the 'sparingly' cutoff."""
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(
        _inputs(), ["Scene one."], filter_catalog={"film_grain": "desc"}, filters_intensity=0
    )
    assert "AVAILABLE FILTERS" not in prompt


def test_director2_prompt_lists_every_cataloged_filter():
    from avg.pipeline.prompts import build_director2_prompt

    catalog = {
        "film_grain": "Old-film grain with subtle flicker.",
        "light_leak": "Warm light bloom drifting across frame.",
    }
    prompt = build_director2_prompt(_inputs(), ["Scene one."], filter_catalog=catalog)
    assert "AVAILABLE FILTERS" in prompt
    assert "film_grain: Old-film grain with subtle flicker." in prompt
    assert "light_leak: Warm light bloom drifting across frame." in prompt
    assert '"filter"' in prompt  # schema line mentions the optional field


@pytest.mark.parametrize(
    "intensity,expected_word",
    [(90, "freely"), (50, "occasionally"), (10, "sparingly")],
)
def test_director2_prompt_filter_frequency_scales_with_intensity(intensity, expected_word):
    from avg.pipeline.prompts import build_director2_prompt

    prompt = build_director2_prompt(
        _inputs(), ["Scene one."], filter_catalog={"film_grain": "desc"}, filters_intensity=intensity
    )
    assert expected_word in prompt


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


from avg.pipeline.prompts import _word_budget_for_duration


@pytest.mark.parametrize(
    "duration_seconds,expected",
    [
        (30, (60, 75)),      # today's fixed values, reproduced exactly
        (60, (120, 150)),
        (90, (180, 225)),
        (120, (240, 300)),   # the "2m" preset
        (240, (480, 600)),   # the "4m" preset
    ],
)
def test_word_budget_scales_with_duration(duration_seconds, expected):
    assert _word_budget_for_duration(duration_seconds) == expected


def test_narration_prompt_default_word_bounds_scale_with_duration():
    text = build_narration_prompt(_inputs(duration_seconds=120))
    assert "240 to 300 words" in text


def test_load_catalog_parses_a_brand_presets_payload_into_a_dict():
    catalog = load_catalog()
    modern_dark = catalog["brand_presets"]["modern_dark"]
    assert modern_dark.payload["bg"] == "#0B0F1A"
    assert modern_dark.payload["accent1"] == "#FF4D6D"
    assert modern_dark.payload["panel"] == "rgba(15,22,36,0.88)"


def test_video_tone_options_get_an_empty_payload_not_a_crash():
    # video_tone's hint is prose, not key=value pairs - payload must
    # degrade to {} rather than raising on the first token with no '='.
    catalog = load_catalog()
    tone = catalog["video_tone"]["documentary_cinematic"]
    assert tone.payload == {}


def test_option_payload_tolerates_a_malformed_token(tmp_path):
    catalog_file = tmp_path / "catalog.txt"
    catalog_file.write_text(
        "## CATEGORY: brand_presets\n"
        "## OPTION: broken | Broken | bg=#111111;not_a_pair;accent1=#222222\n",
        encoding="utf-8",
    )
    catalog = load_catalog(catalog_file)
    opt = catalog["brand_presets"]["broken"]
    assert opt.payload == {"bg": "#111111", "accent1": "#222222"}
