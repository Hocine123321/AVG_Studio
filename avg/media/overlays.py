"""Pre-render the overlay and takeover-card PNGs a render bakes in.

Legacy's Director II part 2 (`Invoke-SmartEditing -AssetsOnly`) drew every
static overlay to a PNG and listed them in `render/_final_layers.json`, so
that Phase 7's caption pass could composite overlays AND captions in a
SINGLE encode (`avg/media/vendor/avg.py:3399-3408`). This app planned the
cards and never drew them, which is why finished videos carried no card
text: `card_plan.json` was a plan with no renderer behind it.

**The pixels are the vendored library's.** `overlay_design.render_overlay`
is imported and called; nothing here draws. What this module owns is two
mappings, and both of them fail SILENTLY when wrong - a bad spec raises
inside the drawing library, the layer is skipped, and the video comes out
looking exactly as it did when nothing drew cards at all:

  * **plan entry -> vendored spec.** `_render_fullscreen_card` reads
    `headline`/`sub` and ignores `text` (`overlay_design.py:463`), and it
    treats `accent` as a BRAND KEY rather than a colour
    (`brand[spec.get("accent", "accent1")]`, `:456`) - so the hex value
    `card_plan.json` carries would raise `KeyError`. Both are translated
    here, in one place.
  * **plan entry -> window on the timeline.** A card is clamped to its own
    scene's window: a card outliving its scene sits on screen over the next
    image.

Written into `<project>/overlays/`, the directory the vendored compositor
itself uses (`avg.py:3115`), so a later overlay pass and this one cannot
disagree about where an asset lives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Fallback window for a card whose plan entry gives no duration.
_DEFAULT_CARD_SECONDS = 2.5


def _scene_windows(scene_timings: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
    return {
        int(entry["scene"]): (float(entry["start"]), float(entry["end"]))
        for entry in scene_timings
        if isinstance(entry, dict)
        and entry.get("type", "scene") == "scene"
        and entry.get("scene") is not None
    }


def _takeovers(scene_timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in scene_timings if isinstance(entry, dict) and entry.get("type") == "takeover"]


#: Above this many characters a card is drawn as an `explainer` rather than a
#: `focused` card. The drawing library allows a focused card TWO lines and an
#: explainer FOUR (`overlay_design.py:468`, hardcoded in protected vendored
#: code), and it truncates with an ellipsis rather than shrinking further - a
#: 40-character question came out as "Why were lighthous…" on screen.
_FOCUSED_MAX_CHARS = 30


def _vendor_spec(entry: dict[str, Any], brand: dict[str, Any]) -> dict[str, Any]:
    """One plan entry as the drawing library's own spec.

    Three translations, each of which fails silently when skipped:

    * `text` becomes `headline`, because that (or `sub`) is what the renderer
      reads - it ignores `text` entirely and returns a card with no words.
    * `accent` is kept only when it names a key the brand actually has: the
      library indexes the brand dict with it, so the hex value
      `card_plan.json` carries would raise `KeyError` and lose the card.
    * a long card becomes an `explainer` rather than a `focused` one, since
      focused cards get two lines and are ellipsised past that.
    """
    accent = str(entry.get("accent") or "")
    text = str(entry.get("text") or "")
    element = str(entry.get("element") or "focused")
    if element == "focused" and len(text) > _FOCUSED_MAX_CHARS:
        element = "explainer"
    return {
        "element": element,
        "headline": text,
        "accent": accent if accent in brand else "accent1",
    }


def resolve_brand(
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The ONE brand resolver, shared by a real render (`render_layers`
    below, via Director II) and a settings preview
    (`avg.media.preview._draw_to_png`) - so an overlay setting changes the
    frame the same way in both places, which is the entire point of Slice
    14 review's Important 4: `avg.media.preview._brand()` used to hardcode
    `DEFAULT_BRAND` regardless of what the channel's settings said, so no
    overlay setting could ever change the preview it was supposedly
    driving.

    Takes raw setting VALUES rather than a `PhaseContext`, same shape as
    `avg.pipeline.phases._common.resolve_font_family_value`: `avg/media/`
    may not import `avg/pipeline/`, so the pipeline layer resolves its own
    settings and hands plain strings in here, and a preview route (which
    has no channel to read from beyond the request body) can call this
    with exactly the same values a debounced settings change carries.

    `preset_payload` (the selected `brand_preset`'s own catalogue payload -
    `bg`/`panel`/`panel_stroke`/`text`/`text2`/`accent1`/`accent2`/
    `highlight`/`font_path`/`font_bold_path`, resolved by
    `avg.pipeline.prompts.load_catalog` at the call site, never here) is
    the new BASE palette, applied over `DEFAULT_BRAND` before the explicit
    arguments below - so `font`/`accent1`/`accent2`/`highlight` still win
    when a channel has set them by hand, exactly as before. `None`/`{}`
    behaves exactly as it always has: `DEFAULT_BRAND` untouched.

    Every argument left blank keeps whichever value is already in `brand`
    at that point - a setting that has never been touched must draw
    exactly as it did before this function existed.
    """
    from avg.media.vendor import overlay_design as od

    brand: dict[str, Any] = dict(od.DEFAULT_BRAND)
    if preset_payload:
        for key in (
            "bg", "panel", "panel_stroke", "text", "text2",
            "accent1", "accent2", "highlight", "font_path", "font_bold_path",
        ):
            value = preset_payload.get(key, "").strip()
            if value:
                brand[key] = value
    if font:
        # The brand carries font PATHS, not family names; a family name is
        # all this app's settings have, so it is only useful when it happens
        # to be a path. Anything else leaves the library's own default.
        if Path(font).suffix.lower() in (".ttf", ".otf"):
            brand["font_path"] = font
    if accent1:
        brand["accent1"] = accent1
    if accent2:
        brand["accent2"] = accent2
    if highlight:
        brand["highlight"] = highlight
    return brand


def render_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One `{png, start, end}` row per drawable layer.

    An overlay whose scene has no timing is SKIPPED rather than drawn at a
    guessed moment - the vendored compositor makes the same choice with the
    same input (`avg.py:3181-3183`), and failing here would throw away a
    whole render over one stale card. A drawing failure is skipped for the
    same reason, per layer.
    """
    from avg.media.vendor import overlay_design as od

    brand = resolve_brand(font, accent1, accent2, highlight, preset_payload)

    overlays_dir = Path(project_folder) / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    windows = _scene_windows(scene_timings)

    layers: list[dict[str, Any]] = []

    for index, entry in enumerate(edit_plan.get("overlays") or [], start=1):
        if not isinstance(entry, dict) or entry.get("scene") is None:
            continue
        window = windows.get(int(entry["scene"]))
        if window is None:
            continue
        start, scene_end = window
        end = min(start + float(entry.get("duration") or _DEFAULT_CARD_SECONDS), scene_end)
        png = overlays_dir / f"overlay_{index:03d}.png"
        if _draw(od, _vendor_spec(entry, brand), width, height, brand, png):
            layers.append({"png": str(png), "start": round(start, 3), "end": round(end, 3)})

    for index, takeover in enumerate(_takeovers(scene_timings), start=1):
        spec: dict[str, Any] = {
            "element": "chapters_takeover_cards",
            "headline": str(takeover.get("headline") or ""),
            "sub": str(takeover.get("sub") or ""),
        }
        png = overlays_dir / f"takeover_{index:03d}.png"
        if _draw(od, spec, width, height, brand, png):
            layers.append(
                {
                    "png": str(png),
                    "start": round(float(takeover.get("start") or 0.0), 3),
                    "end": round(float(takeover.get("end") or 0.0), 3),
                }
            )

    return layers


def _draw(od: Any, spec: dict[str, Any], width: int, height: int, brand: dict[str, Any], png: Path) -> bool:
    """Draw one layer, returning whether a usable file landed.

    `render_overlay` raises on an element it does not know and can return
    nothing for a card with no text; either way an exception must cost one
    overlay, never the render.
    """
    try:
        image = od.render_overlay(spec, width, height, brand)
        if image is None:
            return False
        image.save(png)
    except Exception:  # noqa: BLE001 - one skipped overlay, never a failed render
        return False
    return png.is_file() and png.stat().st_size > 0


def write_layers(
    project_folder: Path,
    edit_plan: dict[str, Any],
    scene_timings: list[dict[str, Any]],
    width: int,
    height: int,
    font: str = "",
    accent1: str = "",
    accent2: str = "",
    highlight: str = "",
    preset_payload: dict[str, str] | None = None,
) -> int:
    """Render the layers and write `render/_final_layers.json`.

    Writes the file only when there is at least one layer: its very
    EXISTENCE is the flag that tells the caption pass to bake overlays in
    the same encode, so an empty list would send the render down the
    combined path with nothing to combine.
    """
    layers = render_layers(
        project_folder, edit_plan, scene_timings, width, height, font, accent1, accent2, highlight, preset_payload
    )
    if not layers:
        return 0
    render_folder = Path(project_folder) / "render"
    render_folder.mkdir(parents=True, exist_ok=True)
    (render_folder / "_final_layers.json").write_text(json.dumps(layers, indent=2), encoding="utf-8")
    return len(layers)
