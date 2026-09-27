"""Generate the iOS AppIcon (`ios/AVGStudio/Assets.xcassets/AppIcon.appiconset/
icon.png`) from the SAME brand mark as `make_icon.py` - the dark "A" on a
terracotta ground (`.rail__logo` in `web/src/Rail.tsx`) - instead of the
placeholder committed with the original iOS scaffold (12128c9), which was
never actually derived from this mark and read as an unrelated purple/orange
icon next to the PC build's real one.

Two things make this its own script rather than reusing `make_icon.py`'s
output directly:

1. iOS wants ONE full-bleed square with NO alpha channel and NO baked-in
   corner rounding - the system applies the squircle mask (and, on iOS 26,
   the Liquid Glass icon treatment) itself at render time. `make_icon.py`'s
   PNG is a rounded rect with transparent corners (correct for a desktop
   taskbar/dock icon, wrong here) - App Store Connect rejects an uploaded
   icon that still has an alpha channel.
2. 1024x1024 is the one size Xcode's asset catalog actually needs (see
   Contents.json's "universal"/1024x1024 entry) - Xcode generates every
   smaller size from it - so this only ever needs to emit that one file.

Run it when the mark changes:

    python3 tools/make_ios_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "ios" / "AVGStudio" / "Assets.xcassets" / "AppIcon.appiconset" / "icon.png"

SIZE = 1024
#: Same two colours as `make_icon.py` - `--accent` and `--canvas` from
#: `web/src/tokens.css` - so the iOS icon and the PC build's icon are the
#: same mark, not just "similarly themed".
TERRACOTTA = "#e08a4e"
CANVAS = "#1c1a16"

#: `make_icon.py`'s letter geometry, scaled 2x from its 512 box into this
#: 1024 one (1024 / 512 == 2) - same shape, just resolved at iOS's size
#: instead of re-deriving it.
_SCALE = SIZE / 512
OUTER = [(256, 92), (426, 416), (86, 416)]
COUNTER = [(256, 168), (298, 300), (214, 300)]
LEGGAP = [(187, 346), (325, 346), (366, 424), (146, 424)]


def _scaled(points: list[tuple[int, int]]) -> list[tuple[float, float]]:
    return [(x * _SCALE, y * _SCALE) for x, y in points]


def main() -> None:
    # Drawn at 4x and downsampled - Pillow's polygon fill isn't
    # antialiased, and the "A"'s diagonals are exactly where that shows
    # (same reasoning as make_icon.py).
    scale = 4
    big = Image.new("RGB", (SIZE * scale, SIZE * scale), TERRACOTTA)  # RGB: no alpha channel, full bleed.
    draw = ImageDraw.Draw(big)
    draw.polygon([(x * scale, y * scale) for x, y in _scaled(OUTER)], fill=CANVAS)
    for cut in (COUNTER, LEGGAP):
        draw.polygon([(x * scale, y * scale) for x, y in _scaled(cut)], fill=TERRACOTTA)
    icon = big.resize((SIZE, SIZE), Image.LANCZOS)

    assert icon.mode == "RGB"  # Guards against ever reintroducing an alpha channel here.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT)
    print(f"wrote {OUT} ({icon.size[0]}x{icon.size[1]}, mode={icon.mode})")


if __name__ == "__main__":
    main()
