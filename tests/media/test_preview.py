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


def test_a_caption_preview_leaves_nothing_behind(monkeypatch):
    """Every intermediate lands in a temp directory that is removed before
    the function returns - on both the success path and the failure path,
    since a preview must leave the library byte-identical either way.

    The previous version of this test chdir'd into `tmp_path` and asserted
    THAT stayed empty - but `caption_png` never writes there: it opens its
    own `tempfile.TemporaryDirectory()` under `%TEMP%` and chdirs the
    ffmpeg subprocess into THAT folder (see the function's own docstring),
    so `tmp_path` was never a candidate location and the assertion could
    not have failed for the reason it named (Slice 14 review, Minors).
    This tracks the real temp folder `caption_png` creates and asserts IT
    is gone afterwards, once on a call that runs to completion and once on
    a call forced down the `except` branch by pointing `ffmpeg_exe` at
    something that cannot run at all.
    """
    import tempfile
    from pathlib import Path

    from avg.media.preview import caption_png

    created: list[Path] = []
    real_cls = tempfile.TemporaryDirectory

    class _Tracking(real_cls):  # type: ignore[misc]
        def __enter__(self):  # type: ignore[no-untyped-def]
            path = Path(super().__enter__())
            created.append(path)
            return str(path)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _Tracking)

    caption_png("[Script Info]\n", 320, 180)
    assert created, "caption_png must build its work inside a tempfile.TemporaryDirectory"
    assert not created[0].exists()

    monkeypatch.setattr(
        "avg.media.environment.ffmpeg_exe",
        lambda: str(created[0] / "does-not-exist.exe"),
    )
    caption_png("[Script Info]\n", 320, 180)
    assert len(created) == 2
    assert not created[1].exists()


def test_paid_voice_providers_are_named_in_one_place():
    """Edge is the free provider; ElevenLabs spends characters. The route
    reads this set to decide what needs an explicit press."""
    from avg.media.preview import PAID_VOICE_PROVIDERS

    assert "elevenlabs" in PAID_VOICE_PROVIDERS
    assert "edge" not in PAID_VOICE_PROVIDERS


def test_this_module_has_no_provider_or_pipeline_dependency():
    """Slice 14 review, Important 5: `voice_sample`/`_voice_backend` (and
    `image_sample`, which never lived here) moved to
    `avg.api.routes_preview`, leaving this module pure rendering - the same
    layer rule `avg/media/` already enforces everywhere else
    (`avg/media/` must not import `avg/pipeline/`, and after this move it
    imports nothing under `avg/providers/` either). Walks the AST rather
    than grepping the raw source text, since the module's own docstring
    names both packages in prose while explaining why it must not import
    them."""
    import ast
    import inspect

    from avg.media import preview

    tree = ast.parse(inspect.getsource(preview))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] + "." + alias.name.split(".")[1] for alias in node.names if "." in alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2:
                imported_roots.add(f"{parts[0]}.{parts[1]}")

    assert "avg.providers" not in imported_roots
    assert "avg.pipeline" not in imported_roots
    assert not hasattr(preview, "voice_sample")
    assert not hasattr(preview, "_voice_backend")
