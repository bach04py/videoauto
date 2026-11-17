"""Text overlays: brand colours, the overlay rules in validate.py, the planner prompt, and the
occ command sequence in the builder."""
from __future__ import annotations

import copy
import json
import unicodedata

import pytest

from test_brand import BRAND, go, make_logo, write_cfg
from test_planner import BIGCAP, CATALOGUE, GOOD, KEYPOP, LOOSE, NOTVI, TR, WIDE, _p, _tpl, base_plan, clip
from videoauto.brief import Brand, load_project_config
from videoauto.errors import UserError
from videoauto.planner import overlays as ov
from videoauto.planner.catalogue import Catalogue
from videoauto.planner.validate import validate_plan

CAT = Catalogue(CATALOGUE["items"])
COLORS = {"accent": "#ED1C24", "ink": "#1A1A1A", "on_accent": "#FFFFFF"}
SCRIPT = """Video giới thiệu phòng tập, 15 giây.

0-3s   | Cận cảnh bàn tay nắm tay cầm máy tập.
       | TEXT: KHÔNG GIAN SỐNG ĐỘNG

3-6s   | Toàn cảnh khu máy tập.

6-10s  | Cảnh rộng kết lại. | TEXT: HOÀNG GIA GYM & FITNESS
"""
LINES = ["KHÔNG GIAN SỐNG ĐỘNG", "HOÀNG GIA GYM & FITNESS"]


# ---------------------------------------------------------------- 1. brand colours

def colours_cfg(tmp_path, block: str):
    write_cfg(tmp_path)
    p = tmp_path / "config.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "brand:\n  name: x\n  colors:\n" + block, encoding="utf-8")
    return p


def test_brand_colours_load_as_upper_case_hex(tmp_path):
    cfg = load_project_config(colours_cfg(tmp_path, '    accent: "#ed1c24"\n    ink: "#111"\n'))
    assert cfg.brand.colors == {"accent": "#ED1C24", "ink": "#111111"}


@pytest.mark.parametrize("block,needle", [
    ('    accent: "red"\n', "brand.colors.accent = 'red' không phải mã màu hex"),
    ('    accent: "#ED1C2"\n', "không phải mã màu hex"),
    ("    accent: #ED1C24\n", "dấu ngoặc kép"),               # unquoted: YAML reads a comment
    ('    primary: "#ED1C24"\n', "khoá không hỗ trợ: primary"),
])
def test_bad_brand_colour_fails_at_load_in_vietnamese(tmp_path, block, needle):
    with pytest.raises(UserError) as e:
        load_project_config(colours_cfg(tmp_path, block))
    assert needle in e.value.message


def test_colour_fields_map_to_roles_and_keep_transparency():
    assert ov.brand_colour_props(KEYPOP, COLORS) == {"accentColor": "#ED1C24", "textColor": "#FFFFFF"}
    assert ov.brand_colour_props(BIGCAP, COLORS) == {
        "bgColor": "rgba(26,26,26,0.72)", "textColor": "#FFFFFF", "accentColor": "#ED1C24"}
    card = {"params": [{"key": "cardColor", "type": "color", "default": "#0f172aee"},
                       {"key": "paperColor", "type": "color", "default": "#fef3c7"}]}
    assert ov.brand_colour_props(card, COLORS) == {"cardColor": "#1A1A1AEE"}     # paperColor: no role
    assert ov.brand_colour_props(KEYPOP, {}) == {}                               # no colours: defaults


# ---------------------------------------------------------------- 2. text helpers

def test_text_lines_are_read_from_the_script():
    assert ov.script_text_lines(SCRIPT) == LINES
    assert ov.script_text_lines("0-3s: cận cảnh tay nắm tạ\n3-6s: toàn cảnh") == []


def test_shorten_cuts_at_a_word_and_drops_a_dangling_ampersand():
    assert ov.shorten("HOÀNG GIA GYM & FITNESS") == "HOÀNG GIA GYM"
    assert ov.shorten("KHÔNG GIAN SỐNG ĐỘNG") == "KHÔNG GIAN SỐNG ĐỘNG"         # 20: unchanged
    assert len("MÔI TRƯỜNG SANG TRỌNG") == 21 and ov.shorten("MÔI TRƯỜNG SANG TRỌNG") == "MÔI TRƯỜNG SANG TRỌNG"


# ---------------------------------------------------------------- 3. validator rules

def over(at, dur=2.0, text=LINES[0], tpl=KEYPOP, field="keyword", **fields):
    return {"template": tpl["assetId"], "at": at, "duration": dur, "fields": {field: text, **fields}}


def check(overlays, lines=LINES, brand=None, **plan):
    p = base_plan(clips=[clip(0, 5), clip(5, 10)], overlays=overlays, text_lines=lines, **plan)
    w = validate_plan(p, CAT, brand=brand)
    return p, "\n".join(w)


def logo_brand(position="bottom-right", mode="always"):
    from pathlib import Path
    return Brand(logo=Path("logo.png"), logo_size=(100, 50), logo_position=position, logo_mode=mode)


def test_valid_overlay_is_kept_and_every_text_field_is_set():
    p, w = check([over(0.5)])
    assert w == "" and p["overlays"] == [
        {"template": KEYPOP["assetId"], "at": 0.5, "duration": 2.0,
         "fields": {"keyword": LINES[0], "subText": ""}}]       # subText "" overrides the Chinese default


def test_only_vi_ok_templates():
    p, w = check([over(0.5, tpl=NOTVI, field="title")])
    assert p["overlays"] == [] and "kiểm tra font tiếng Việt" in w


def test_template_must_match_the_frame_orientation():
    p, w = check([over(0.5, tpl=WIDE, field="title")])
    assert p["overlays"] == [] and "không hợp khung 9:16" in w


def test_invented_text_is_dropped():
    p, w = check([over(0.5, text="TẬP LÀ MÊ")])
    assert p["overlays"] == [] and "không có trong các dòng TEXT" in w


def test_no_text_lines_in_script_means_no_overlays():
    p, w = check([over(0.5)], lines=[])
    assert p["overlays"] == [] and "không có trong các dòng TEXT" in w


def test_second_text_field_cannot_carry_invented_text():
    p, w = check([over(0.5, subText="Đăng ký ngay")])
    assert p["overlays"] == [] and "Đăng ký ngay" in w


def test_text_over_22_characters_is_shortened_with_a_warning():
    p, w = check([over(6.0, text=LINES[1])])
    assert p["overlays"][0]["fields"]["keyword"] == "HOÀNG GIA GYM"
    assert "dài 23 ký tự (tối đa 22)" in w and "rút gọn thành \"HOÀNG GIA GYM\"" in w
    q = copy.deepcopy(p)                                       # the builder validates plan.json again
    assert validate_plan(q, CAT) == [] and q["overlays"] == p["overlays"]


def test_decomposed_unicode_matches_its_composed_script_line():
    nfd = unicodedata.normalize("NFD", LINES[0])
    assert len(nfd) > 22
    p, w = check([over(0.5, text=nfd)])
    assert p["overlays"][0]["fields"]["keyword"] == LINES[0] and "rút gọn" not in w


def test_overlays_never_overlap_and_a_line_is_used_once():
    p, w = check([over(0.5, 2.5), over(2.0, 2.0, text=LINES[1]), over(6.0, 2.0)])
    assert [o["at"] for o in p["overlays"]] == [0.5]
    assert "trùng thời gian" in w and "đã dùng cho overlay khác" in w


def test_overlay_is_clamped_to_the_video_or_dropped_outside_it():
    p, w = check([over(9.0, 3.0), over(9.6, 1.0, text=LINES[1])])
    assert p["overlays"][0]["duration"] == 1.0 and len(p["overlays"]) == 1 and "nằm ngoài video" in w


def test_overlay_in_the_logo_corner_is_dropped():
    p, w = check([over(0.5, tpl=BIGCAP, field="caption")], brand=logo_brand("bottom-right"))
    assert p["overlays"] == [] and "trùng góc logo" in w
    p, w = check([over(0.5, tpl=BIGCAP, field="caption")], brand=logo_brand("top-left"))
    assert len(p["overlays"]) == 1                             # bottom bar, logo top-left: fine


def test_logo_end_mode_only_guards_its_last_three_seconds():
    p, _ = check([over(0.5, tpl=BIGCAP, field="caption"), over(8.0, tpl=BIGCAP, field="caption",
                                                                 text=LINES[1])],
                 brand=logo_brand("bottom-right", "end"))
    assert [o["at"] for o in p["overlays"]] == [0.5]


def test_unknown_position_is_refused_while_a_logo_shows():
    p, w = check([over(0.5, tpl=LOOSE, field="title")], brand=logo_brand())
    assert p["overlays"] == [] and "không rõ template đặt chữ ở đâu" in w
    p, _ = check([over(0.5, tpl=LOOSE, field="title")])        # no logo: nothing to collide with
    assert len(p["overlays"]) == 1


def test_transition_under_an_overlay_is_dropped():
    p, w = check([over(4.0, 2.0)], transitions=[{"at": 5.0, "id": TR["assetId"], "frames": 6}])
    assert p["transitions"] == [] and "che mất chữ overlay" in w


# ---------------------------------------------------------------- 4. planner and builder, end to end

REPLY = copy.deepcopy(GOOD) | {
    "transitions": [],
    "overlays": [{"template": KEYPOP["assetId"], "at": 0.3, "duration": 2.5,
                  "fields": {"keyword": LINES[0], "subText": ""}},
                 {"template": KEYPOP["assetId"], "at": 11.0, "duration": 2.5,
                  "fields": {"keyword": LINES[1]}},
                 {"template": KEYPOP["assetId"], "at": 5.0, "duration": 2.0,
                  "fields": {"keyword": "TOÀN CẢNH PHÒNG TẬP"}}],        # beat without TEXT: invented
    "beats": [{"beat": "x", "clips": [1], "note": ""}]}


def run_with_colours(tmp_path, footage, reply=REPLY, fail=()):
    (tmp_path / "kb.txt").write_text(SCRIPT, encoding="utf-8")
    make_logo(tmp_path / "logo.png")
    cfg = write_cfg(tmp_path, footage, script="kb.txt", logo="logo.png", **BRAND)
    cfg.write_text(cfg.read_text(encoding="utf-8") + "  colors:\n" + "".join(
        f'    {k}: "{v}"\n' for k, v in COLORS.items()), encoding="utf-8")
    from conftest import FakeProvider
    from test_builder import FakeOcc
    from videoauto import run as run_cli
    p, occ = FakeProvider(), FakeOcc(footage, fail)
    p.reply = reply
    code = run_cli.main(["--config", str(cfg), "--out", str(tmp_path / "out")], provider=p, occ=occ)
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "out" / "final.mp4.build.json").read_text(encoding="utf-8"))
    return code, p, occ, plan, report


def test_prompt_lists_safe_templates_and_the_text_lines(tmp_path, footage):
    _, p, _, plan, _ = run_with_colours(tmp_path, footage)
    prompt = p.prompts[0]
    assert "TEXT OVERLAYS" in prompt and "KHÔNG GIAN SỐNG ĐỘNG / HOÀNG GIA GYM & FITNESS" in prompt
    tpl_lines = prompt.split("TEMPLATES (")[1].split("REPLY FORMAT")[0]
    first = tpl_lines.splitlines()[1]
    assert KEYPOP["assetId"] in first                          # the default comes first
    for absent in (BIGCAP, NOTVI, LOOSE, WIDE):                # logo corner / not vi_ok / unknown / 16:9
        assert absent["assetId"] not in tpl_lines
    assert plan["text_lines"] == LINES


def test_no_text_lines_means_no_overlay_section(tmp_path, footage):
    (tmp_path / "kb.txt").write_text("0-3s: cận cảnh tay\n3-6s: toàn cảnh", encoding="utf-8")
    code, p, _ = go(tmp_path, footage, REPLY, script="kb.txt")
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert code == 0 and "TEXT OVERLAYS" not in p.prompts[0] and plan["overlays"] == []


def test_builder_places_overlays_below_the_logo_with_text_and_brand_colours(tmp_path, footage):
    code, _, occ, plan, report = run_with_colours(tmp_path, footage)
    assert code == 0
    assert [o["fields"]["keyword"] for o in plan["overlays"]] == ["KHÔNG GIAN SỐNG ĐỘNG", "HOÀNG GIA GYM"]
    assert any("TOÀN CẢNH PHÒNG TẬP" in w for w in plan["warnings"])
    labels = [r.label for r in occ.history]
    i = labels.index("overlay track")
    assert labels[i:i + 9] == ["overlay track",
                               "overlay 1 add", "overlay 1 find", "overlay 1 duration", "overlay 1 props",
                               "overlay 2 add", "overlay 2 find", "overlay 2 duration", "overlay 2 props"]
    assert labels.index("logo track") > i                      # created later = stacked above
    by = {r.label: r.tool_args for r in occ.history}
    track = occ.history[i].data["ops"][0]["result"]["created"][0]["id"]
    add = by["overlay 1 add"]["adds"][0]
    assert add == {"type": "motion-graphic", "assetId": KEYPOP["assetId"], "track": track, "fromFrame": 9}
    assert by["overlay 1 duration"]["updates"][0]["durationInFrames"] == 75
    assert by["overlay 1 props"]["props"] == {"keyword": "KHÔNG GIAN SỐNG ĐỘNG", "subText": "",
                                              "accentColor": "#ED1C24", "textColor": "#FFFFFF"}
    assert [o["ok"] for o in report["overlays"]] == [True, True]


def test_failed_overlay_is_removed_skipped_recorded_and_not_fatal(tmp_path, footage):
    code, _, occ, _, report = run_with_colours(tmp_path, footage, fail=("overlay 1 props", "overlay 2 add"))
    assert code == 0 and report["ok"] and report["item_failures"] == 0     # not counted towards the abort
    labels = [r.label for r in occ.history]
    assert "overlay 1 remove" in labels and labels[-1] == "render"
    assert not any(i["kind"] == "motion-graphic" for i in occ.timeline)     # no Chinese sample text left
    assert [o["ok"] for o in report["overlays"]] == [False, False]
    assert "điền chữ và màu" in report["overlays"][0]["error"]
    assert sum("lỗi, bỏ qua overlay" in w for w in report["warnings"]) == 2


@pytest.fixture
def footage(tmp_path, gym, sunset):
    import shutil
    d = tmp_path / "footage"
    d.mkdir()
    shutil.copy(gym, d / "gym.mp4")
    shutil.copy(sunset, d / "sunset.mp4")
    return d


# ---------------------------------------------------------------- 6. readable brand colours

def _g(text, backdrop, px=1000):
    return {"px": px, "text": text, "backdrop": backdrop}


LIGHT_CHART = _tpl("light-chart", 1080, 1920, True, [
    _p("title", "text", "x"), _p("textColor", "color", "#ffffff"), _p("accentColor", "color", "#38bdf8")])
DONUT = _tpl("donut", 1080, 1920, True, [_p("title", "text", "x"), _p("cardColor", "color", "#ffffff"),
                                          _p("bgColor", "color", "#E8E6F0")])
TABLE = {
    # real default text colour is dark although the catalogue says #ffffff (code fallback)
    "light-chart": {"defaults": {"textColor": "#26224d", "accentColor": "#38bdf8", "bgGradientStart": "#f6f5ff"},
                    "groups": [_g({"param": "textColor"}, {"param": "bgGradientStart"})]},
    # text colour fixed in the template code, card behind it is a brand (ink) field
    "donut": {"defaults": {"cardColor": "#ffffff", "bgColor": "#E8E6F0"},
              "groups": [_g({"fixed": "rgba(59,66,67,1.00)"}, {"param": "cardColor"})]},
    "ss-keyword-pop": {"defaults": {"accentColor": "#ff4d6d", "textColor": "#ffffff"},
                       "groups": [_g({"param": "textColor"}, {"video": True}),
                                  _g({"param": "accentColor"}, {"video": True})]},
    "ss-big-caption": {"defaults": {"bgColor": "rgba(0,0,0,0.72)", "textColor": "#ffffff"},
                       "groups": [_g({"param": "textColor"}, {"param": "bgColor"})]},
}


def test_brand_text_colour_too_close_to_its_backdrop_keeps_the_template_colour():
    props, notes = ov.readable_brand_props(LIGHT_CHART, COLORS, TABLE)
    assert props == {"accentColor": "#ED1C24"}              # white text on #f6f5ff dropped
    assert len(notes) == 1 and "textColor (chữ)" in notes[0] and "1.1:1" in notes[0]


def test_brand_backdrop_under_fixed_text_keeps_the_template_colour():
    props, notes = ov.readable_brand_props(DONUT, COLORS, TABLE)
    assert props == {"bgColor": "#1A1A1A"}                  # not behind text: brand applies
    assert "cardColor (nền sau chữ)" in notes[0]


def test_readable_pairs_and_text_over_video_keep_brand_colours():
    assert ov.readable_brand_props(KEYPOP, COLORS, TABLE) == (
        {"accentColor": "#ED1C24", "textColor": "#FFFFFF"}, [])
    # white on 72 % ink, judged over black and over white video: still >= 3:1
    assert ov.readable_brand_props(BIGCAP, COLORS, TABLE) == (
        {"bgColor": "rgba(26,26,26,0.72)", "textColor": "#FFFFFF", "accentColor": "#ED1C24"}, [])


def test_template_never_measured_keeps_its_own_colours():
    props, notes = ov.readable_brand_props(LIGHT_CHART, COLORS, {})
    assert props == {} and "chưa được đo" in notes[0]
    assert ov.readable_brand_props(LIGHT_CHART, {}, TABLE) == ({}, [])


def test_template_designed_low_contrast_is_not_blamed_on_the_brand():
    faint = {"light-chart": {"defaults": {"textColor": "#cccccc"},
                             "groups": [_g({"param": "textColor"}, {"fixed": "#bbbbbb"})]}}
    # a faint watermark by design (1.2:1); brand white (about 2:1) is still under 3:1 but better
    # than the template's own colour, so it is applied without a note
    assert ov.readable_brand_props(LIGHT_CHART, {"on_accent": "#FFFFFF"}, faint) == ({"textColor": "#FFFFFF"}, [])


def test_contrast_ratio():
    assert round(ov.contrast((255, 255, 255, 1), (0, 0, 0, 1)), 1) == 21.0
    assert ov.contrast((255, 255, 255, 1), (0, 0, 0, 0)) < 1.1     # transparent over white video
    assert ov.parse_colour("#0f172aee")[3] == pytest.approx(0xee / 255)
    assert ov.parse_colour("rgba(14, 166, 124, 0.2)") == (14, 166, 124, 0.2)
    assert ov.parse_colour("linear-gradient(red, blue)") is None


def test_unreadable_brand_colour_is_a_plan_warning_and_the_overlay_stays(monkeypatch):
    monkeypatch.setattr(ov, "colour_table", lambda: TABLE)
    cat = Catalogue([*CATALOGUE["items"], LIGHT_CHART])
    p = base_plan(clips=[clip(0, 5), clip(5, 10)], text_lines=LINES,
                  overlays=[over(0.5, tpl=LIGHT_CHART, field="title")])
    w = "\n".join(validate_plan(p, cat, brand=Brand(colors=COLORS)))
    assert len(p["overlays"]) == 1
    assert "giữ màu template cho textColor" in w and "tương phản" in w


def test_builder_does_not_force_an_unreadable_brand_colour(tmp_path, footage, monkeypatch):
    # keyword pop drawn on a white card: brand white text would vanish, so it keeps its own
    table = {**TABLE, "ss-keyword-pop": {"defaults": {"accentColor": "#ff4d6d", "textColor": "#1a1a1a"},
                                         "groups": [_g({"param": "textColor"}, {"fixed": "#ffffff"})]}}
    monkeypatch.setattr(ov, "colour_table", lambda: table)
    code, _, occ, plan, _ = run_with_colours(tmp_path, footage)
    assert code == 0
    props = {r.label: r.tool_args for r in occ.history}["overlay 1 props"]["props"]
    assert props["accentColor"] == "#ED1C24" and "textColor" not in props
    assert any("giữ màu template cho textColor" in w for w in plan["warnings"])


# ---------------------------------------------------------------- 7. orientation

def _cfg(aspect):
    from types import SimpleNamespace
    from videoauto.brief import Brief
    return SimpleNamespace(brief=Brief(15, aspect, 30, "tiktok", ""), brand=Brand())


def test_planner_offers_only_templates_of_the_target_orientation():
    from videoauto.planner.planner import overlay_templates
    nosize = _tpl("no-size", None, None, True, [_p("title", "text", "x")])
    cat = Catalogue([*CATALOGUE["items"], nosize])
    vertical = {t["id"] for t in overlay_templates(_cfg("9:16"), cat)}
    assert "wide-title" not in vertical and "no-size" not in vertical
    assert {"ss-keyword-pop", "ss-big-caption", "vertical-unknown"} <= vertical
    assert {t["id"] for t in overlay_templates(_cfg("16:9"), cat)} == {"wide-title"}


def test_template_of_unknown_size_is_refused():
    nosize = _tpl("no-size", None, None, True, [_p("title", "text", "x")])
    cat = Catalogue([*CATALOGUE["items"], nosize])
    p = base_plan(clips=[clip(0, 5), clip(5, 10)], text_lines=LINES,
                  overlays=[over(0.5, tpl=nosize, field="title")])
    w = "\n".join(validate_plan(p, cat, brand=None))
    assert p["overlays"] == [] and "không rõ kích thước" in w and "không hợp khung 9:16" in w
