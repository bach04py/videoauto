"""Text overlays: which templates may carry Vietnamese text, where they sit in the frame,
which TEXT lines the user's script asks for, and how brand colours map onto template fields.

Everything here is data or pure functions; validate.py enforces the rules, the builder
applies them.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from pathlib import Path

MAX_TEXT = 22             # characters per text field; longer overflowed in the Vietnamese font audit
MIN_OVERLAY = 1.0         # seconds; shorter than this a line cannot be read
DEFAULT_TEMPLATE = "library:motion-graphic:ss-keyword-pop"    # 竖屏·重点词弹出, keyword pop
PREFERRED_GROUP = "social-shorts"                             # catalogue "category" for 9:16

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
# Where each template draws, read from its code in OpenChatCut
# (assets/templates/social-shorts-templates.json), as the frame corners it reaches into.
# "centre" reaches none. A template that is not listed has an unknown position: it is
# refused whenever a logo is on screen, because a corner collision cannot be ruled out.
ZONES = {
    "centre": (),
    "top": ("top-left", "top-right"),
    "bottom": ("bottom-left", "bottom-right"),
    **{c: (c,) for c in CORNERS},
}
PLACEMENT = {
    "ss-keyword-pop": "centre",       # flex centre, maxWidth 980, wraps
    "ss-quote-card": "centre",
    "ss-step-list": "centre",
    "ss-big-number": "centre",
    "ss-cover-title": "centre",
    "ss-checklist": "centre",
    "ss-before-after": "centre",
    "ss-chapter": "top",              # top 120, left/right 48: full-width bar
    "ss-episode-badge": "top-left",   # top 100, left 48
    "ss-price-tag": "top-right",      # top 420, right 48
    "ss-note-sticker": "top-right",   # top 520, right 60
    "ss-big-caption": "bottom",       # bottom 220, left/right 48: full-width bar
    "ss-cta-follow": "bottom",        # flex-end, centred, wide button row
    "ss-name-bar": "bottom",          # bottom 360, left 48, up to 720 px wide (past the middle)
    "ss-social-actions": "bottom-right",   # bottom 380, right 36
}


def template_key(asset_id: str) -> str:
    return asset_id.rsplit(":", 1)[-1]


def zone(asset_id: str) -> str | None:
    return PLACEMENT.get(template_key(asset_id))


def corners(asset_id: str) -> tuple[str, ...] | None:
    z = zone(asset_id)
    return None if z is None else ZONES[z]


# ---------------------------------------------------------------- brand colours

# Template colour field -> brand role. Matched by exact field name; every other colour field
# keeps the template's default. ink is used as the dark surface behind text and on_accent as
# the light text on top of it (or on top of the video), so contrast survives the swap.
COLOR_FIELDS = {
    "accent": ("accentColor", "accentColor1", "highlightColor", "brandColor", "themeColor",
               "accentLineColor", "badgeColor"),
    "ink": ("bgColor", "backgroundColor", "cardColor", "cardBgColor", "cardBackgroundColor",
            "panelColor", "boxColor", "badgeBgColor", "inkColor"),
    "on_accent": ("textColor", "titleColor", "nameColor", "headerTextColor"),
}
_ROLE_OF = {f: role for role, fields in COLOR_FIELDS.items() for f in fields}
_RGBA = re.compile(r"rgba\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)")


def _with_alpha(brand_hex: str, default) -> str:
    """Keep the template's transparency when swapping in a brand colour: a translucent card
    (rgba(0,0,0,0.72) or #0f172aee) stays translucent in the brand colour."""
    d = str(default or "").strip()
    m = _RGBA.fullmatch(d)
    if m:
        r, g, b = (int(brand_hex[i:i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r},{g},{b},{m.group(1)})"
    if re.fullmatch(r"#[0-9a-fA-F]{8}", d):
        return brand_hex + d[7:].upper()
    return brand_hex


def brand_colour_props(template: dict, colors: dict[str, str],
                       defaults: dict | None = None) -> dict[str, str]:
    """Colour props for one template from brand.colors (role -> #RRGGBB), before any contrast
    check. `defaults` (the template's real props, from the colour table) wins over the
    catalogue's param defaults, which are the code fallbacks and can differ."""
    out = {}
    for p in template.get("params", []):
        role = _ROLE_OF.get(p["key"]) if p.get("type") == "color" else None
        if role and role in colors:
            d = (defaults or {}).get(p["key"], p.get("default"))
            out[p["key"]] = _with_alpha(colors[role], d)
    return out


# ---------------------------------------------------------------- contrast

# Measured per template by `python -m videoauto.templates --measure-colours` (brand-independent):
# for the pixels of the template's text, which colour prop (or fixed colour) paints the text and
# which paints what is behind it ("video" where the overlay is transparent). See templates.py.
COLOUR_TABLE = Path(__file__).with_name("overlay_colours.json")
MIN_CONTRAST = 3.0      # WCAG AA for large text; overlay text is display-sized


@functools.cache
def colour_table() -> dict:
    try:
        return json.loads(COLOUR_TABLE.read_text(encoding="utf-8"))["templates"]
    except (OSError, ValueError, KeyError):
        return {}


_HEX = re.compile(r"#([0-9a-fA-F]{3,8})")
_RGB = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")


def parse_colour(value) -> tuple[float, float, float, float] | None:
    """#rgb, #rgba, #rrggbb, #rrggbbaa, rgb(), rgba() -> (r, g, b, a), r/g/b 0-255, a 0-1."""
    s = str(value or "").strip()
    m = _HEX.fullmatch(s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a
    m = _RGB.fullmatch(s)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return float(m.group(1)), float(m.group(2)), float(m.group(3)), min(1.0, a)
    return None


def _luminance(rgb) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _over(top, under_rgb):
    a = top[3]
    return tuple(top[i] * a + under_rgb[i] * (1 - a) for i in range(3))


def contrast(text, backdrop) -> float:
    """WCAG contrast ratio of text over backdrop (both (r,g,b,a)). A translucent backdrop is laid
    over black and over white video, and the worse of the two counts."""
    worst = 21.0
    for video in ((0, 0, 0), (255, 255, 255)):
        bd = _over(backdrop, video)
        tx = _over(text, bd)
        hi, lo = sorted((_luminance(tx), _luminance(bd)), reverse=True)
        worst = min(worst, (hi + 0.05) / (lo + 0.05))
    return worst


def readable_brand_props(template: dict, colors: dict[str, str],
                         table: dict | None = None) -> tuple[dict[str, str], list[str]]:
    """Brand colour props that keep the template's text readable, plus Vietnamese notes for
    every brand colour that was not applied.

    For each measured text/backdrop pair: if the brand colours push it below MIN_CONTRAST and
    below the template's own contrast, the brand colour on the text is dropped first, else the
    one on the backdrop; the template's real default then stays. Text over transparent video
    is not checked (there is nothing fixed to measure against). A template that was never
    measured keeps all of its own colours: readability cannot be checked, so nothing is forced."""
    if not colors:
        return {}, []
    info = (colour_table() if table is None else table).get(template.get("id") or template_key(template["assetId"]))
    if not info:
        wanted = brand_colour_props(template, colors)
        return {}, ([f"giữ màu của template cho {', '.join(wanted)}: template chưa được đo màu chữ/nền "
                     "nên không kiểm tra được độ tương phản"] if wanted else [])
    defaults = info.get("defaults") or {}
    props = brand_colour_props(template, colors, defaults)
    notes = []

    def colour(src, use):
        if "fixed" in src:
            return parse_colour(src["fixed"])
        k = src["param"]
        return parse_colour(use.get(k, defaults.get(k)))

    for _ in range(4):                      # one colour dropped per failing pair and pass
        changed = False
        for g in info.get("groups", []):
            if g["backdrop"].get("video"):
                continue
            mine = [s["param"] for s in (g["text"], g["backdrop"]) if s.get("param") in props]
            if not mine:
                continue
            t, b = colour(g["text"], props), colour(g["backdrop"], props)
            t0, b0 = colour(g["text"], {}), colour(g["backdrop"], {})
            if None in (t, b, t0, b0):
                k = mine[0]
                notes.append(f"giữ màu template cho {k}: không đọc được màu chữ/nền để kiểm tra tương phản")
            else:
                c, c0 = contrast(t, b), contrast(t0, b0)
                if c >= MIN_CONTRAST or c >= c0:
                    continue
                k = mine[0]                 # text side first: it is listed first
                who = "chữ" if k == g["text"].get("param") else "nền sau chữ"
                notes.append(f"giữ màu template cho {k} ({who}): màu thương hiệu {props[k]} chỉ cho "
                             f"độ tương phản {c:.1f}:1 (cần ≥ {MIN_CONTRAST:g}:1; màu gốc {c0:.1f}:1)")
            props.pop(k)
            changed = True
        if not changed:
            break
    return props, notes


# ---------------------------------------------------------------- text

def norm(text: str) -> str:
    """NFC (a decomposed 'ộ' is 3 code points, composed it is 1) and single spaces."""
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


_TEXT_LINE = re.compile(r"(?:^|\|)\s*TEXT\s*:\s*([^|]*)", re.I)


def script_text_lines(script: str) -> list[str]:
    """The TEXT lines of a user script, in order: `TEXT: ...` at the start of a line or of a
    `|` column. These are the only strings an overlay may show."""
    out = []
    for line in script.splitlines():
        m = _TEXT_LINE.search(line)
        if m and norm(m.group(1)):
            out.append(norm(m.group(1)))
    return out


def shorten(text: str, limit: int = MAX_TEXT) -> str:
    """Cut at a word boundary to at most `limit` characters, dropping a dangling '&', '-', ','."""
    if len(text) <= limit:
        return text
    words, out = text.split(" "), ""
    for w in words:
        cand = f"{out} {w}".strip()
        if len(cand) > limit:
            break
        out = cand
    out = out.rstrip(" &-–—,.:;/+|")
    return out or text[:limit]


def text_params(template: dict) -> list[str]:
    return [p["key"] for p in template.get("params", []) if p.get("type") == "text"]


def orientation(template: dict) -> str | None:
    w, h = template.get("width"), template.get("height")
    if not w or not h:
        return None
    return "square" if w == h else ("vertical" if h > w else "horizontal")
