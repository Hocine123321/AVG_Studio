"""Generate the application icon: `assets/icon.svg`, `icon.png`, `icon.ico`.

The rail's brand mark (`.rail__logo` in `web/src/Rail.tsx`) promoted to the
app icon: a dark "A" on a terracotta rounded square, the same two colours
the app opens with.

The letter is drawn as geometry rather than set in Caprasimo. Caprasimo
ships here as a woff2 (`web/public/fonts/caprasimo-400.woff2`) and neither
Pillow's FreeType build nor the environment's font tooling can load woff2,
so typesetting it would mean adding a font dependency to produce one static
file. A triangle with a triangular counter gives the same heavy display "A"
at 16px, which is the size that actually has to survive.

Run it when the mark changes:

    .venv-build/Scripts/python.exe tools/make_icon.py

Windows needs every size baked into the .ico - without 16 and 32 the
taskbar and title bar downscale the 256 and it goes muddy.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "assets"

SIZE = 512
#: `--accent` and `--canvas` from `web/src/tokens.css`.
TERRACOTTA = "#e08a4e"
CANVAS = "#1c1a16"
#: 22% of the edge, matching the rail logo's radius-to-size ratio.
RADIUS = round(SIZE * 0.22)

#: The letter, in a 512 box. A solid triangle with two pieces cut out of
#: it: the closed counter above the crossbar, and the open space between
#: the legs below it. Cutting both is what makes an "A" rather than a
#: delta - with only the counter removed the legs never separate.
OUTER = [(256, 92), (426, 416), (86, 416)]
COUNTER = [(256, 168), (298, 300), (214, 300)]
#: Extends past the baseline so the cut leaves no hairline of fill behind.
LEGGAP = [(187, 346), (325, 346), (366, 424), (146, 424)]

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <rect width="{size}" height="{size}" rx="{radius}" ry="{radius}" fill="{bg}"/>
  <path fill="{fg}" fill-rule="evenodd" d="{outer} {counter} {leggap}"/>
</svg>
"""


def _path(points: list[tuple[int, int]]) -> str:
    head, *rest = points
    return "M{} {} ".format(*head) + " ".join(f"L{x} {y}" for x, y in rest) + " Z"


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    (ASSETS / "icon.svg").write_text(
        SVG.format(
            size=SIZE,
            radius=RADIUS,
            bg=TERRACOTTA,
            fg=CANVAS,
            outer=_path(OUTER),
            counter=_path(COUNTER),
            leggap=_path(LEGGAP),
        ),
        encoding="utf-8",
    )

    # Drawn at 4x and downsampled: Pillow's polygon fill is not
    # antialiased, and the diagonals of an "A" are exactly where that shows.
    scale = 4
    big = Image.new("RGBA", (SIZE * scale, SIZE * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        [(0, 0), (SIZE * scale - 1, SIZE * scale - 1)],
        radius=RADIUS * scale,
        fill=TERRACOTTA,
    )
    draw.polygon([(x * scale, y * scale) for x, y in OUTER], fill=CANVAS)
    for cut in (COUNTER, LEGGAP):
        draw.polygon([(x * scale, y * scale) for x, y in cut], fill=TERRACOTTA)
    icon = big.resize((SIZE, SIZE), Image.LANCZOS)

    icon.save(ASSETS / "icon.png")
    icon.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"wrote {ASSETS / 'icon.svg'}, icon.png and icon.ico")


if __name__ == "__main__":
    main()
