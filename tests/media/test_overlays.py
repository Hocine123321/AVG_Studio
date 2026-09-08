"""Tests for pre-rendered overlay assets.

The point of pre-rendering: when `render/_final_layers.json` exists, the
caption pass bakes overlays AND captions in one encode
(`avg/media/vendor/avg.py:3399-3408`). Without it, cards are a plan nothing
draws - which is why finished videos carried no card text at all.

The mapping onto the vendored renderer's own spec vocabulary is what these
tests mostly guard, because getting it wrong fails SILENTLY: a bad spec
raises inside the drawing library, the layer is skipped, and the video comes
out looking exactly like it did when nothing was drawing cards in the first
place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from avg.media.overlays import render_layers, write_layers


def _timings() -> list[dict[str, Any]]:
    return [
        {"type": "scene", "scene": 1, "start": 0.0, "end": 6.0, "duration": 6.0},
        {
            "type": "takeover",
            "id": "part-1",
            "headline": "Part one",
            "sub": "the setup",
            "start": 6.0,
            "end": 7.8,
            "duration": 1.8,
        },
        {"type": "scene", "scene": 2, "start": 7.8, "end": 12.0, "duration": 4.2},
    ]


def _scene_only_timings() -> list[dict[str, Any]]:
    """Timings with no takeover node, so a test asserting one layer gets
    exactly the overlay it asked about."""
    return [entry for entry in _timings() if entry["type"] == "scene"]


def test_every_layer_has_a_real_png_and_a_window(tmp_path):
    layers = render_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 2, "text": "Why stripes?", "duration": 2.0}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    assert len(layers) == 1
    png = Path(layers[0]["png"])
    assert png.is_file() and png.stat().st_size > 0
    # The window comes from the SCENE's timing, not from zero.
    assert layers[0]["start"] == 7.8
    assert layers[0]["end"] == 9.8


def test_a_cards_text_reaches_the_renderer_as_headline(tmp_path):
    """`_render_fullscreen_card` reads `headline`/`sub` and ignores `text`
    entirely (`avg/media/vendor/overlay_design.py:463`). Passing our own
    `text` key straight through drew a blank card - the renderer found no
    text, returned early, and the failure was invisible."""
    layers = render_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 1, "text": "Why stripes?"}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    blank = render_layers(
        tmp_path / "blank",
        {"overlays": [{"element": "focused", "scene": 1, "text": ""}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    assert len(layers) == 1
    # A card with text is a materially different image from one without.
    assert blank == [] or Path(layers[0]["png"]).stat().st_size != Path(blank[0]["png"]).stat().st_size


def test_a_hex_accent_does_not_kill_the_card(tmp_path):
    """`accent` names a BRAND KEY, not a colour: the renderer does
    `brand[spec.get("accent", "accent1")]` (`overlay_design.py:456`), so the
    hex string card_plan.json carries would raise KeyError and lose the
    card."""
    layers = render_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 1, "text": "Why?", "accent": "#e08a4e"}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    assert len(layers) == 1
    assert Path(layers[0]["png"]).is_file()


def test_a_takeover_becomes_a_card_layer(tmp_path):
    """Takeover cards are not in the overlay plan - they come from the
    timeline, via the timings. The old app rendered them as
    `chapters_takeover_cards` (`avg/media/vendor/avg.py:3233`)."""
    layers = render_layers(tmp_path, {"overlays": []}, _timings(), 1080, 1920)

    assert len(layers) == 1
    assert layers[0]["start"] == 6.0 and layers[0]["end"] == 7.8


def test_an_overlay_for_a_scene_with_no_timing_is_skipped(tmp_path):
    """It has no window, so there is no moment to draw it at. Skipping is
    what the vendored compositor does with the same input
    (`avg.py:3181-3183`); failing would lose a whole render over one stale
    card."""
    layers = render_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 99, "text": "orphan"}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    assert layers == []


def test_a_card_never_outlives_its_scene(tmp_path):
    """A four-second card on a two-second scene would still be on screen
    over the next image."""
    layers = render_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 2, "text": "Why?", "duration": 99.0}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    assert layers[0]["end"] == 12.0


def test_write_layers_leaves_a_readable_manifest(tmp_path):
    count = write_layers(
        tmp_path,
        {"overlays": [{"element": "focused", "scene": 1, "text": "Hi", "duration": 1.0}]},
        _scene_only_timings(),
        1080,
        1920,
    )

    manifest = json.loads((tmp_path / "render" / "_final_layers.json").read_text(encoding="utf-8"))
    assert count == len(manifest) == 1
    assert Path(manifest[0]["png"]).is_file()


def test_no_layers_means_no_manifest_at_all(tmp_path):
    """The file's EXISTENCE is the flag that tells the caption pass to bake
    overlays in the same encode. An empty list would send the render down
    the combined path with nothing to combine."""
    count = write_layers(tmp_path, {"overlays": []}, [{"type": "scene", "scene": 1, "start": 0.0, "end": 5.0}], 1080, 1920)

    assert count == 0
    assert not (tmp_path / "render" / "_final_layers.json").exists()


def test_a_long_question_is_not_truncated_into_an_ellipsis(tmp_path):
    """The drawing library allows a `focused` card two lines and ellipsises
    the rest (`overlay_design.py:468`, hardcoded in protected vendored
    code) - a 40-character question rendered as "Why were lighthous…". A
    long card is therefore drawn as an `explainer`, which is the same card
    family with four lines.
    """
    from avg.media.overlays import _vendor_spec

    brand = {"accent1": "#FF4D6D"}

    short = _vendor_spec({"element": "focused", "text": "Why stripes?"}, brand)
    long = _vendor_spec(
        {"element": "focused", "text": "Why were lighthouses painted in stripes?"}, brand
    )

    assert short["element"] == "focused"
    assert long["element"] == "explainer"
    assert long["headline"] == "Why were lighthouses painted in stripes?"


def test_resolve_brand_uses_the_preset_payload_as_its_base():
    from avg.media.overlays import resolve_brand

    brand = resolve_brand(preset_payload={"bg": "#111111", "accent1": "#ABCDEF"})
    assert brand["bg"] == "#111111"
    assert brand["accent1"] == "#ABCDEF"
    # A key the payload didn't carry keeps the library's own default.
    assert brand["highlight"] == "#FFD93D"


def test_resolve_brand_explicit_accent_still_wins_over_the_preset_payload():
    from avg.media.overlays import resolve_brand

    brand = resolve_brand(accent1="#00FF00", preset_payload={"accent1": "#ABCDEF"})
    assert brand["accent1"] == "#00FF00"


def test_resolve_brand_with_no_preset_payload_is_unchanged_from_before():
    from avg.media.overlays import resolve_brand
    from avg.media.vendor import overlay_design as od

    assert resolve_brand() == dict(od.DEFAULT_BRAND)
