"""Text overlays: which templates may carry Vietnamese text, where they sit in the frame,
which TEXT lines the user's script asks for, and how brand colours map onto template fields.

Everything here is data or pure functions; validate.py enforces the rules, the builder
applies them.
"""

from __future__ import annotations

import re
import unicodedata

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


def brand_colour_props(template: dict, colors: dict[str, str]) -> dict[str, str]:
    """Colour props for one template from brand.colors (role -> #RRGGBB)."""
    out = {}
    for p in template.get("params", []):
        role = _ROLE_OF.get(p["key"]) if p.get("type") == "color" else None
        if role and role in colors:
            out[p["key"]] = _with_alpha(colors[role], p.get("default"))
    return out


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
