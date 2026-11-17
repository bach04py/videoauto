"""python -m videoauto.templates: which templates are previewed, the props they get, field
classification and the TSV. The Remotion render itself is not run here (needs OpenChatCut)."""
from __future__ import annotations

import csv
import subprocess
import sys

from videoauto import templates as tp

COLORS = {"accent": "#ED1C24", "ink": "#1A1A1A", "on_accent": "#FFFFFF"}


def _p(key, type_, default=None):
    return {"key": key, "label": key, "type": type_, "default": default, "editable": True}


def _tpl(id_, name, *, vi_ok=True, category="lower-thirds", params=(), w=1920, h=1080):
    return {"id": id_, "assetId": f"library:motion-graphic:{id_}", "kind": "template", "name": name,
            "category": category, "source": "assets/templates/x.json", "width": w, "height": h,
            "vi_ok": vi_ok, "params": list(params)}


ITEMS = [
    _tpl("b", "Zeta card", params=[
        _p("titleText", "text", "Hello world"), _p("unit", "text", "K"), _p("dataValues", "text", "1.2, 1.8"),
        _p("year1", "text", "2024"), _p("avatarUrl", "text", "data:image/svg+xml,x"), _p("empty", "text", ""),
        _p("accentColor", "color", "#00ff00"), _p("bgColor", "color", "rgba(0,0,0,0.72)"),
        _p("wordDelay", "number", 3), _p("introAnimation", "boolean", True), _p("introText", "text", "Hi there"),
        _p("shadowColor", "color", "#000"), _p("strokeWidth", "number", 2), _p("cardBorderRadius", "number", 8),
        _p("bgGlowColor", "color", "#fff"), _p("borderColor", "color", "#fff")]),
    _tpl("a", "Alpha card", params=[_p("keyword", "text", "重点词")], category="social-shorts", w=1080, h=1920),
    _tpl("c", "Not usable", vi_ok=False, params=[_p("titleText", "text", "x y")]),
    {"assetId": "builtin:fx-blur", "kind": "effect", "name": "Blur", "vi_ok": True, "params": []},
]


def test_only_vi_ok_templates_sorted_by_group_then_name():
    items = tp.preview_items(ITEMS, COLORS)
    assert [(it["n"], it["id"], it["group"]) for it in items] == [(1, "b", "lower-thirds"), (2, "a", "social-shorts")]
    assert items[1]["orientation"] == "vertical"


def test_sample_text_goes_to_word_fields_only():
    b = tp.preview_items(ITEMS, COLORS)[0]
    assert b["fill"] == ["titleText", "introText"]       # not units, data, years, URLs or empty fields
    assert b["overrides"]["titleText"] == tp.SAMPLE_TEXT == "ĐỐT MỠ ĐÓN TẾT"
    assert "unit" not in b["overrides"] and "dataValues" not in b["overrides"]


def test_brand_colours_applied_keeping_alpha():
    b = tp.preview_items(ITEMS, COLORS)[0]
    assert b["overrides"]["accentColor"] == "#ED1C24"
    assert b["overrides"]["bgColor"] == "rgba(26,26,26,0.72)"
    assert "shadowColor" not in b["overrides"]           # not a brand role: template default


def test_no_brand_colours_means_template_defaults():
    b = tp.preview_items(ITEMS, {})[0]
    assert set(b["overrides"]) == {"titleText", "introText"}


def test_animation_and_shadow_outline_fields():
    b = tp.preview_items(ITEMS, COLORS)[0]
    assert b["animation"] == ["wordDelay", "introAnimation"]          # introText is text, not a knob
    assert b["shadow_outline"] == ["shadowColor", "strokeWidth", "borderColor"]   # no radius, no bg glow


def test_tsv(tmp_path):
    items = tp.preview_items(ITEMS, COLORS)
    results = {1: {"n": 1, "status": "ok", "frame": 53, "durationInFrames": 90, "sheet": "sheets/01-horizontal.png"},
               2: {"n": 2, "status": "error", "error": "boom"}}
    tp.write_tsv(tmp_path / "t.tsv", items, results)
    rows = list(csv.DictReader(open(tmp_path / "t.tsv", encoding="utf-8"), delimiter="\t"))
    assert list(rows[0]) == list(tp.TSV_COLUMNS)
    assert rows[0]["animation_fields"] == "wordDelay,introAnimation"
    assert rows[0]["shadow_outline_fields"] == "shadowColor,strokeWidth,borderColor"
    assert rows[0]["brand_colour_fields"] == "accentColor,bgColor"
    assert rows[0]["frame"] == "53/90" and rows[0]["sheet"] == "sheets/01-horizontal.png"
    assert rows[1]["status"] == "error: boom" and rows[1]["name"] == "Alpha card"


def test_module_makes_no_model_calls():
    # Everything the command imports (at load and in _run) stays clear of the vision providers
    # and the planner's model call. Fresh interpreter, so other tests' imports do not count.
    code = ("import sys, videoauto.templates, videoauto.brief, videoauto.builder.occ, "
            "videoauto.planner.catalogue; "
            "print([m for m in sys.modules if m.startswith(('videoauto.vision', 'videoauto.planner.planner'))])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
