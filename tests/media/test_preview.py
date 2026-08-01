"""Preview rendering. Free previews only in this file - the paid ones
(voice on ElevenLabs, image generation) are tested for what they REFUSE,
in tests/api/test_preview_routes.py.
"""

from __future__ import annotations

from avg.media.preview import SAMPLE_SENTENCE, overlay_png, takeover_png


def test_an_overlay_renders_to_real_png_bytes():
    """Drawn by `overlay_design.render_overlay` - the same function the real
    render calls - so the preview cannot drift from what ships.

    `lower_third` was removed (see overlay_design.py's own note); `explainer`
    is one of the three elements `ELEMENT_RENDERERS` still knows.
    """
    data = overlay_png({"element": "explainer", "headline": "Hello"}, 1080, 1920)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_takeover_card_renders():
    data = takeover_png("A headline", "and a sub", 1080, 1920)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_element_the_drawer_does_not_know_returns_no_bytes():
    """`render_overlay` raises on an unknown element. A preview must report
    that as "nothing to show", never as an exception reaching the route."""
    assert overlay_png({"element": "not-a-real-element"}, 1080, 1920) == b""


def test_the_sample_sentence_is_fixed_and_shared():
    """One constant, so the voice preview always says the same thing and
    the panel can quote it."""
    assert "voice" in SAMPLE_SENTENCE.lower()
