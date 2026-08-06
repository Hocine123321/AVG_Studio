"""Preview rendering. Free previews only in this file - the paid ones
(voice on ElevenLabs, image generation) are tested for what they REFUSE,
in tests/api/test_preview_routes.py.
"""

from __future__ import annotations

from avg.media.preview import SAMPLE_SENTENCE, overlay_png, takeover_png


def test_an_overlay_renders_to_real_png_bytes():
    """Drawn by `overlay_design.render_overlay` - the same function the real
    render calls - so the preview cannot drift from what ships. `lower_third`
    was removed from the vendored renderer's element set (Takeover Cards are
    the only overlay architecture left); `explainer` is a real element."""
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


def test_a_caption_frame_renders_from_ass_text():
    """`caption_png` takes TEXT, not settings: `avg/media/` may not import
    from `avg/pipeline/`, so the caption phase builds the .ass and this
    burns it. One construction of the styling, not two."""
    from avg.media.preview import caption_png

    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Preview\n"
    )
    data = caption_png(ass, 640, 360)
    # Either a real PNG, or empty when ffmpeg is unavailable on this machine
    # - never an exception reaching the caller.
    assert data == b"" or data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_caption_preview_leaves_nothing_behind(tmp_path, monkeypatch):
    """Every intermediate lands in a temp directory that is removed before
    the function returns. A preview must leave the library byte-identical."""
    from avg.media.preview import caption_png

    monkeypatch.chdir(tmp_path)
    caption_png("[Script Info]\n", 320, 180)
    assert list(tmp_path.iterdir()) == []
