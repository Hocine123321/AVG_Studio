"""
overlay_design.py  —  AVG Takeover Cards (Director 2 / AI Graphics phase)

Pillow-based rendering of full-screen "Takeover Cards" — the only overlay
architecture after the B2.16 Advanced & Smart Editing update. The old library of
floating procedural graphics (lower_third, info_card, text_emphasis,
stat_callout, banner, name_badge, quote_card, progress_bar, chapter_title) was
removed. Layout, colors, fonts and sizing are deterministic; the AI only picks
which card appears and the short text it carries.

Card elements:
  chapters_takeover_cards — chapters-preset divider (injected by avg.py
      overlay_render from timeline.json takeover entries). Was "phase_card".
  explainer / focused     — full-screen attention cards (Stage 6).

Every card renders full-bleed/opaque (it OWNS its window, not floating over
footage), so it skips the drop-shadow pass, cuts in/out instantly, and centers
its text. Live word-highlight sync is handled by the caption/ASS engine, not
here — this module only draws the card background + static text fallback.

Usage (CLI):
    python overlay_design.py <out_dir> <W> <H> <brand.json> <spec.json>

  <spec.json> is a single overlay spec object:
    {"element": "chapters_takeover_cards"|"explainer"|"focused",
     "headline": "...", "sub": "...", "accent": "accent1"|"accent2"|"highlight"}

  Writes <out_dir>/overlay.png (RGBA, size WxH, transparent background).

Exit codes: 0 = written, 1 = bad args/spec.
"""

import os
import sys
import json
import re

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── default brand kit (mirrors Get-EditBrandDefaults in utilities.ps1) ──────
DEFAULT_BRAND = {
    "bg": "#0B0F1A", "panel": "rgba(15,22,36,0.88)", "panel_stroke": "rgba(255,255,255,0.12)",
    "text": "#F2F4F8", "text2": "#B8BEC9",
    "accent1": "#FF4D6D", "accent2": "#FF9F43", "highlight": "#FFD93D",
    "font_path": "", "font_bold_path": ""
}


# B2.17.2 — the corner/vertical placement helpers (resolve_corner_position /
# resolve_vertical_zone) and the TOP_ZONE_MAX clamp were removed with the floating
# overlays. Takeover Cards are full-screen and centered, so there is no corner placement.

# ── color helpers ─────────────────────────────────────────────────────────

def parse_color(value, default_alpha=255):
    """Parse '#RRGGBB' or 'rgba(r,g,b,a)' into an (r,g,b,a) tuple."""
    value = str(value).strip()
    if value.startswith('#'):
        h = value.lstrip('#')
        if len(h) == 6:
            r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
            return (r, g, b, default_alpha)
        if len(h) == 8:
            r, g, b, a = (int(h[i:i+2], 16) for i in (0, 2, 4, 6))
            return (r, g, b, a)
    if value.startswith('rgba'):
        parts = value[value.index('(') + 1: value.index(')')].split(',')
        r, g, b = (int(float(p.strip())) for p in parts[:3])
        a = float(parts[3].strip())
        return (r, g, b, int(round(a * 255)))
    if value.startswith('rgb'):
        parts = value[value.index('(') + 1: value.index(')')].split(',')
        r, g, b = (int(float(p.strip())) for p in parts[:3])
        return (r, g, b, default_alpha)
    return (255, 255, 255, default_alpha)


def darken(rgba, factor=0.5):
    return (max(0, int(rgba[0] * factor)), max(0, int(rgba[1] * factor)),
            max(0, int(rgba[2] * factor)), rgba[3])


# ── font loading ─────────────────────────────────────────────────────────

_WINDOWS_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\segoevar.ttf",
    r"C:\Windows\Fonts\Arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\helvetica.ttf",
    r"C:\Windows\Fonts\Tahoma.ttf",
]

_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\segoevar.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
]

_ITALIC_CANDIDATES = [
    r"C:\Windows\Fonts\georgiai.ttf",
    r"C:\Windows\Fonts\timesi.ttf",
    r"C:\Windows\Fonts\Ariali.ttf",
]


_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]

def _resolve_font_name(name):
    """B2.16 — resolve a font FAMILY NAME (e.g. 'Montserrat', 'Bebas Neue') to a .ttf/.otf
    path by scanning the OS font dirs. Returns a path or None. Used so the user's global
    font (which may be a name, not a path) reaches both Takeover Cards and captions."""
    if not name:
        return None
    key = "".join(ch for ch in name.lower() if ch.isalnum())
    for d in _FONT_DIRS:
        if not d or not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        # Prefer an exact-ish stem match; fall back to a substring match.
        for want_exact in (True, False):
            for fn in entries:
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in (".ttf", ".otf"):
                    continue
                norm = "".join(ch for ch in stem.lower() if ch.isalnum())
                if (want_exact and norm == key) or (not want_exact and key in norm):
                    return os.path.join(d, fn)
    return None

def _find_font(candidates, custom_path=""):
    if custom_path:
        if os.path.exists(custom_path):
            return custom_path
        # Not a path — try resolving it as an installed font family name.
        resolved = _resolve_font_name(custom_path)
        if resolved:
            return resolved
    for p in candidates:
        if os.path.exists(p):
            return p
    return None  # PIL falls back to its built-in bitmap font


def load_font(size, bold=False, italic=False, brand=None):
    custom = ''
    if brand:
        if bold and brand.get('font_bold_path'):
            custom = brand['font_bold_path']
        elif (not bold) and brand.get('font_path'):
            custom = brand['font_path']
    if italic:
        candidates = _ITALIC_CANDIDATES
    elif bold:
        candidates = _BOLD_CANDIDATES
    else:
        candidates = _WINDOWS_FONT_CANDIDATES
    path = _find_font(candidates, custom)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ── text measurement / wrapping (exact, via PIL) ─────────────────────────

def measure(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def measure_h(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def wrap_text(draw, text, font, max_w, max_lines):
    words = text.strip().split()
    lines = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if measure(draw, cand, font) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
        while measure(draw, lines[-1], font) > max_w and len(lines[-1]) > 1:
            lines[-1] = lines[-1][:-2] + "…"
    return lines


# ── shared drawing primitives ───────────────────────────────────────────────

def safe_radius(r, w, h):
    return max(0, min(r, min(w, h) // 2))


def rounded_panel(draw, x, y, w, h, radius, brand, fill_alpha=None, stroke=True):
    r = safe_radius(radius, w, h)
    fill = parse_color(brand["panel"]) if fill_alpha is None else (*parse_color(brand["panel"])[:3], fill_alpha)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill)
    if stroke:
        stroke = parse_color(brand["panel_stroke"])
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r, outline=stroke, width=2)
        # subtle top highlight
        hl = (*parse_color(brand["text"])[:3], 30)
        draw.rounded_rectangle([x, y, x + w, y + r], radius=r, fill=hl)


def accent_bar(draw, x, y, w, h, bar_w, color):
    """Vertical accent bar on the LEFT edge of a panel (LTR reading order)."""
    inset = min(bar_w * 1.6, h * 0.15)
    r = bar_w / 2
    draw.rounded_rectangle(
        [x + inset * 0.2, y + inset, x + bar_w, y + h - inset],
        radius=r, fill=color
    )


def drop_shadow(img, offset=(0, 7), blur=14, opacity=0.34):
    """Soft blurred drop shadow using Pillow's GaussianBlur."""
    if blur <= 0:
        return img
    alpha = img.split()[-1].point(lambda a: int(a * opacity))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 255), (offset[0], offset[1]), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    shadow.alpha_composite(img, (0, 0))
    return shadow



# ── element: chapters_takeover_cards ─────────────────────────────────────────────────────
# chapters preset takeover divider. Renders into the FULL black placeholder
# clip Phase 5 reserved for this window — opaque full-bleed background (not a
# panel floating over footage like every other element), small "PART N" label
# above a larger title, with accent rules echoing chapter_title's composition
# but visually distinct: this is a deliberate chapter break, not a quote/title
# shown over existing footage. headline/sub come straight from timeline.json —
# no AI placement involved, the window is already fully specified.

def render_chapters_takeover_cards(spec, W, H, brand):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    headline = (spec.get("headline") or "").strip()
    sub = (spec.get("sub") or "").strip()
    if not headline and not sub:
        return img

    S = min(W, H)

    # Full-bleed opaque background — this card OWNS the frame for its window,
    # unlike every other element which floats semi-transparently over an image.
    bg_color = parse_color(brand["bg"])
    draw.rectangle([0, 0, W, H], fill=(*bg_color[:3], 255))

    accent_color = parse_color(brand[spec.get("accent", "accent1")])
    text_color = parse_color(brand["text"])

    # timeline.json's shape (see Build-Timeline in ai.ps1): "headline" is the
    # short "Part N" kicker label, "sub" is the descriptive chapter title.
    # Fall back gracefully if only one of the two was supplied.
    if headline and sub:
        kicker, title = headline.upper(), sub
    elif sub:
        kicker, title = "", sub
    else:
        kicker, title = "", headline

    fs_kicker = max(16, int(S * 0.034))
    fs_title = max(34, int(S * 0.078))

    font_kicker = load_font(fs_kicker, bold=True, brand=brand)
    font_title = load_font(fs_title, bold=True, brand=brand)

    max_w = int(W * 0.82)
    title_lines = wrap_text(draw, title, font_title, max_w, 2) if title else []

    line_h = int(fs_title * 1.28)
    title_h = len(title_lines) * line_h
    kicker_gap = int(fs_kicker * 1.8) if kicker else 0
    total_h = kicker_gap + title_h

    y = max(int(H * 0.08), (H - total_h) // 2)

    # Kicker label, letter-spaced via manual spacing (PIL has no native tracking)
    if kicker:
        spaced = " ".join(list(kicker)) if len(kicker) <= 12 else kicker  # avoid absurd spacing on long labels
        kw = measure(draw, spaced, font_kicker)
        kx = (W - kw) // 2
        draw.text((kx, y), spaced, font=font_kicker, fill=accent_color)
        y += kicker_gap

    ty = y
    for line in title_lines:
        tw = measure(draw, line, font_title)
        tx = (W - tw) // 2
        draw.text((tx + 3, ty + 3), line, font=font_title, fill=(0, 0, 0, 140))
        draw.text((tx, ty), line, font=font_title, fill=text_color)
        ty += line_h

    # Accent rule beneath the title — echoes chapter_title's framing device
    line_thickness = max(3, int(S * 0.007))
    rule_w = min(int(W * 0.22), int(S * 0.18))
    rule_x = (W - rule_w) // 2
    rule_y = ty + int(fs_title * 0.2)
    draw.rounded_rectangle(
        [rule_x, rule_y, rule_x + rule_w, rule_y + line_thickness],
        radius=line_thickness // 2, fill=accent_color
    )

    return img


# ── Takeover Cards: Explainer + Focused (B2.16) ──────────────────────────────
# Full-screen attention cards that drop an opaque background covering the screen with
# DEAD-CENTER text. Both variants share one composition; they differ only in intent
# (Explainer = a "board" for a deeper concept/summary, more words allowed; Focused =
# isolates a brief high-impact statement or rhetorical question, fewer words, bigger).
# Text size is highly dynamic — it scales to the exact word count so there is ZERO
# overflow at any length. These cut in/out instantly (no animation).
#
# When spec["text_via_ass"] is truthy the card renders the BACKGROUND ONLY — the live
# word-highlight text is burned by the caption/ASS (karaoke) engine on top, so this
# module must not also draw the text (that would double it). Otherwise it draws a
# static centered text fallback.

def _fit_card_font(draw, text, brand, W, H, max_lines, bold=True):
    """Largest font size that fits `text` within the card's safe box in <= max_lines.
    Returns (font, wrapped_lines, size). Guarantees no horizontal/vertical overflow."""
    box_w = int(W * 0.84)
    box_h = int(H * 0.72)
    S = min(W, H)
    size = int(S * 0.15)
    floor = max(14, int(S * 0.035))
    while size > floor:
        font = load_font(size, bold=bold, brand=brand)
        lines = wrap_text(draw, text, font, box_w, max_lines)
        widest = max((measure(draw, l, font) for l in lines), default=0)
        total_h = len(lines) * int(size * 1.26)
        if widest <= box_w and total_h <= box_h and len(lines) <= max_lines:
            return font, lines, size
        size -= max(2, int(size * 0.06))
    font = load_font(floor, bold=bold, brand=brand)
    return font, wrap_text(draw, text, font, box_w, max_lines), floor


# ── B2.17.6/B2.17.9: optional custom background image for Focused/Explainer cards ────
# User-supplied image(s) under Assets/Ai Graphics/ replace the solid card colour for the
# Focused/Explainer takeover cards (same "OWNS the frame" mechanic as chapters cards). Toggle:
# config.edit.focused_card_bg (default on). Two orientation modes so ONE file serves each video
# format (the image is NOT cut by the script — you place one per format):
#   focused-explainer background tall.(png|jpg|jpeg)  → portrait 9:16 (short-form)
#   focused-explainer background wide.(png|jpg|jpeg)  → landscape 16:9 (long-form)
#   focused-explainer background.(png|jpg|jpeg)       → generic fallback if the oriented one is absent
# Missing file OR toggle off => graceful fall back to the solid brand colour. Resolved fresh per
# card (few per video; the render server is long-lived, so a settings toggle takes effect
# immediately). Cover-fit (scaled to fill, centre-cropped) to each card's W×H.
_CARD_BG_BASE = "focused-explainer background"
_CARD_BG_EXTS = (".png", ".jpg", ".jpeg")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _focused_card_bg_enabled():
    # Reads config.edit.focused_card_bg (default True). Tolerant: any read/parse error => on.
    try:
        cfg_path = os.path.join(_repo_root(), "config", "config.json")
        if not os.path.exists(cfg_path):
            return True
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        edit = cfg.get("edit", {}) if isinstance(cfg, dict) else {}
        val = edit.get("focused_card_bg", True) if isinstance(edit, dict) else True
        return bool(val)
    except Exception:
        return True


def _focused_card_bg_path(portrait):
    """Resolve the custom card-background file (or None) — toggle + file must both be present.
    Picks the TALL image for portrait (9:16) video and the WIDE image for landscape (16:9);
    falls back to the un-suffixed generic name. Case-insensitive on filenames."""
    try:
        if not _focused_card_bg_enabled():
            return None
        bg_dir = os.path.join(_repo_root(), "Assets", "Ai Graphics")
        if not os.path.isdir(bg_dir):
            return None
        oriented = f"{_CARD_BG_BASE} tall" if portrait else f"{_CARD_BG_BASE} wide"
        # Preference order: orientation-specific first, then the generic un-suffixed name.
        candidates = [f"{oriented}{e}".lower() for e in _CARD_BG_EXTS] + \
                     [f"{_CARD_BG_BASE}{e}".lower() for e in _CARD_BG_EXTS]
        present = {fn.lower(): fn for fn in os.listdir(bg_dir)}
        for want in candidates:
            if want in present:
                return os.path.join(bg_dir, present[want])
    except Exception:
        pass
    return None


def _draw_card_background(img, W, H, brand):
    """Fill the card frame: the custom image (cover-fit, cropped centered) if present,
    otherwise the solid brand background colour. Always leaves an opaque full-bleed frame."""
    src = _focused_card_bg_path(H > W)   # portrait (H>W) => tall image, landscape => wide
    if src:
        try:
            with Image.open(src) as raw:
                bg = raw.convert("RGBA")
            sw, sh = bg.size
            if sw > 0 and sh > 0:
                scale = max(W / sw, H / sh)          # cover: fill the frame, crop the overflow
                nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
                bg = bg.resize((nw, nh), Image.LANCZOS)
                left, top = (nw - W) // 2, (nh - H) // 2
                bg = bg.crop((left, top, left + W, top + H))
                # flatten onto the solid colour first so any transparency stays opaque.
                base_c = parse_color(brand["bg"])
                base = Image.new("RGBA", (W, H), (*base_c[:3], 255))
                base.alpha_composite(bg)
                img.paste(base, (0, 0))
                return
        except Exception as e:
            print(f"  [cards] custom background '{src}' unreadable ({e}) — using solid colour.",
                  file=sys.stderr)
    bg_color = parse_color(brand["bg"])
    ImageDraw.Draw(img).rectangle([0, 0, W, H], fill=(*bg_color[:3], 255))


def _render_fullscreen_card(spec, W, H, brand, focused=False):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # Full-bleed opaque background — the card OWNS the frame for its window. B2.17.6: this is
    # a user's custom Assets/Ai Graphics image when present+enabled, else the solid brand colour.
    _draw_card_background(img, W, H, brand)
    draw = ImageDraw.Draw(img)
    accent_color = parse_color(brand[spec.get("accent", "accent1")])
    text_color = parse_color(brand["text"])

    # The caption/ASS engine draws the live-highlight text on top — background only here.
    if spec.get("text_via_ass"):
        return img

    text = (spec.get("headline") or spec.get("sub") or "").strip()
    if not text:
        return img

    # Focused = punchy statement/question (fewer words, bigger). Explainer = board (more words).
    max_lines = 2 if focused else 4
    font, lines, size = _fit_card_font(draw, text, brand, W, H, max_lines)
    line_h = int(size * 1.26)
    total_h = len(lines) * line_h
    y = max(int(H * 0.06), (H - total_h) // 2)

    for line in lines:
        tw = measure(draw, line, font)
        tx = (W - tw) // 2
        draw.text((tx + 3, y + 3), line, font=font, fill=(0, 0, 0, 140))
        draw.text((tx, y), line, font=font, fill=text_color)
        y += line_h

    # Accent rule under the text (shared framing device with chapters_takeover_cards).
    lt = max(3, int(min(W, H) * 0.007))
    rule_w = min(int(W * 0.24), int(min(W, H) * 0.2))
    rule_x = (W - rule_w) // 2
    draw.rounded_rectangle(
        [rule_x, y + int(size * 0.2), rule_x + rule_w, y + int(size * 0.2) + lt],
        radius=lt // 2, fill=accent_color
    )
    return img


def render_explainer_card(spec, W, H, brand):
    return _render_fullscreen_card(spec, W, H, brand, focused=False)


def render_focused_card(spec, W, H, brand):
    return _render_fullscreen_card(spec, W, H, brand, focused=True)


# B2.16 Advanced & Smart Editing — the procedural "AI graphics types" (lower_third,
# info_card, text_emphasis, stat_callout, banner, name_badge, quote_card, progress_bar,
# chapter_title) were removed. Takeover Cards are now the ONLY overlay architecture:
#   chapters_takeover_cards — the chapters-preset divider (was "phase_card")
#   explainer / focused      — full-screen attention cards (added in Stage 6)
# All are full-bleed opaque cards that OWN their window, so none need the float
# drop-shadow pass, and none animate internally (they cut in/out instantly).
FULLSCREEN_CARDS = {"chapters_takeover_cards", "explainer", "focused"}

ELEMENT_RENDERERS = {
    "chapters_takeover_cards": render_chapters_takeover_cards,
    "explainer": render_explainer_card,
    "focused": render_focused_card,
}

ANIMATED_ELEMENTS = set()


def render_overlay(spec, W, H, brand, progress=1.0):
    """
    Renders a single overlay spec. progress is retained for signature
    compatibility with avg.py's overlay_render but is unused now — every
    remaining element is a full-screen card that cuts in/out instantly.
    """
    element = spec.get("element", "chapters_takeover_cards")
    renderer = ELEMENT_RENDERERS.get(element)
    if renderer is None:
        raise ValueError(f"Unknown overlay element: {element!r}")
    img = renderer(spec, W, H, brand)
    if element in FULLSCREEN_CARDS:
        # Full-bleed opaque background — a drop shadow on a frame-filling alpha
        # channel is both invisible and a wasted GaussianBlur pass over the
        # whole frame. These cards own the frame, so skip the shadow.
        return img
    return drop_shadow(img)


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 6:
        print("Usage: overlay_design.py <out_dir> <W> <H> <brand.json> <spec.json>", file=sys.stderr)
        sys.exit(1)

    out_dir = sys.argv[1]
    W = int(sys.argv[2])
    H = int(sys.argv[3])
    brand_path = sys.argv[4]
    spec_path = sys.argv[5]

    brand = dict(DEFAULT_BRAND)
    if os.path.exists(brand_path):
        try:
            with open(brand_path, "r", encoding="utf-8") as f:
                brand.update(json.load(f))
        except Exception as e:
            print(f"Warning: could not read brand.json ({e}) — using defaults.", file=sys.stderr)

    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        print(f"Error: could not read spec.json ({e})", file=sys.stderr)
        sys.exit(1)

    try:
        img = render_overlay(spec, W, H, brand)
    except Exception as e:
        print(f"Error rendering overlay: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "overlay.png")
    img.save(out_path)
    print(f"Wrote {out_path} ({W}x{H})")
    sys.exit(0)


if __name__ == "__main__":
    main()