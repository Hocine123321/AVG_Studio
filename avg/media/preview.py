"""Preview bytes, produced by the code that will produce the real thing.

The point of this module is that a preview cannot lie. Overlays and takeover
cards are drawn by `avg.media.vendor.overlay_design.render_overlay` - the
same function `avg.media.overlays._draw` calls during a real render - and
captions are burned by the same ffmpeg/libass pass the renderer uses. A DOM
or CSS approximation would be a second implementation of the same styling,
free to drift, and a preview that lies is worse than no preview.

**Layering.** `caption_png` takes `.ass` TEXT rather than building it:
`avg/media/` must not import from `avg/pipeline/`, and the `.ass` header is
the caption phase's own. The API layer builds the text there and hands it
here. That keeps one construction of the styling rather than two.

Nothing here writes into a project folder. Every intermediate lands in a
temp directory that is removed before the function returns.
"""

from __future__ import annotations

import io
from typing import Any

#: What a voice preview says. Fixed, and in one place, so every provider
#: says the same thing and the panel can quote it back to the user.
SAMPLE_SENTENCE = "Hello, this is my voice I'll be using in a new video! Do you like it?"


def _brand() -> dict[str, Any]:
    from avg.media.vendor import overlay_design as od  # noqa: PLC0415

    return dict(od.DEFAULT_BRAND)


def _draw_to_png(spec: dict[str, Any], width: int, height: int) -> bytes:
    """One drawn layer as PNG bytes, or `b""`.

    Never raises: `render_overlay` raises on an element it does not know and
    returns `None` for a card with no text. Both mean "there is nothing to
    show", which a preview panel can state - an exception reaching the route
    could not be.
    """
    from avg.media.vendor import overlay_design as od  # noqa: PLC0415

    try:
        image = od.render_overlay(spec, width, height, _brand())  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - an unknown element is "nothing to show"
        return b""
    if image is None:
        return b""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def overlay_png(spec: dict[str, Any], width: int, height: int) -> bytes:
    """One overlay, drawn exactly as the render would draw it."""
    return _draw_to_png(spec, width, height)


def takeover_png(headline: str, sub: str, width: int, height: int) -> bytes:
    """One takeover card, drawn exactly as the render would draw it.

    The element name matches what `avg.media.overlays.render_layers` builds
    for a real takeover, so this is the same card the video will show.
    """
    return _draw_to_png(
        {"element": "chapters_takeover_cards", "headline": headline, "sub": sub},
        width,
        height,
    )


def caption_png(ass_text: str, width: int, height: int) -> bytes:
    """One frame with `ass_text` burned over a flat background, as PNG bytes.

    Rendered by ffmpeg's own subtitles filter - the same libass the renderer
    burns captions with (`avg/media/vendor/avg.py`'s `cmd_caption_burn`), so
    the preview matches the video rather than approximating it.

    Takes TEXT rather than settings on purpose: `avg/media/` may not import
    from `avg/pipeline/`, and the `.ass` construction belongs to the caption
    phase. See `avg.pipeline.phases.captions.sample_ass`.

    Returns `b""` rather than raising when ffmpeg is missing or refuses -
    a preview panel can state "nothing to show", an exception cannot.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from avg.media.environment import ffmpeg_exe  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        ass_path = folder / "preview.ass"
        out_path = folder / "preview.png"
        ass_path.write_text(ass_text, encoding="utf-8")
        # libass resolves the filter argument relative to the working
        # directory, and a Windows path inside a filter string needs its
        # separators and colon escaped - hence the cwd and the bare name.
        try:
            result = subprocess.run(
                [
                    ffmpeg_exe(), "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=0x1c1a16:s={width}x{height}:d=1",
                    "-vf", "subtitles=preview.ass",
                    "-frames:v", "1",
                    str(out_path),
                ],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001 - a missing ffmpeg is "nothing to show"
            return b""
        if result.returncode != 0 or not out_path.is_file():
            return b""
        return out_path.read_bytes()
