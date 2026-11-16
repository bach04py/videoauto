"""Brand context, logo overlay and the user script (config: brand / footage_dir / script_file)."""
from __future__ import annotations

import copy
import json
import logging
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from conftest import FakeProvider
from test_builder import FakeOcc
from test_planner import CATALOGUE, GOOD, TR, base_plan, clip
from videoauto import run as run_cli
from videoauto.brief import Brand, load_project_config
from videoauto.builder.builder import logo_transform
from videoauto.errors import UserError
from videoauto.planner.catalogue import Catalogue
from videoauto.planner.validate import logo_window, transition_window, validate_plan

BRAND = {"name": "Hoàng Gia Gym & Fitness 24h", "tagline": "Khỏe mỗi ngày",
         "context": "Chuỗi phòng tập, hồ bơi và sân pickleball ở Bình Dương."}


def make_logo(path: Path, w=213, h=157) -> Path:
    img = np.zeros((h, w, 4), np.uint8)
    img[:, :, 2] = 220                       # red, opaque in the middle, transparent border
    img[10:-10, 10:-10, 3] = 255
    cv2.imencode(".png", img)[1].tofile(str(path))
    return path


def write_cfg(tmp: Path, footage: Path | None = None, script: str | None = None, **brand) -> Path:
    (tmp / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    lines = ['brief:', '  duration: 15', '  aspect: "9:16"', '  fps: 30',
             'catalogue: catalogue.json', 'openchatcut:', '  repo: nowhere', '  node: node']
    if footage is not None:
        lines.append(f"footage_dir: {json.dumps(str(footage))}")
    if script is not None:
        lines.append(f"script_file: {json.dumps(script)}")
    if brand:
        lines.append("brand:")
        lines += [f"  {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in brand.items()]
    p = tmp / "config.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 1a. validation at preflight

@pytest.mark.parametrize("override,needle", [
    ({"logo": "missing.png"}, "Không tìm thấy logo"),
    ({"logo": "logo.png", "logo_scale": 0.6}, "brand.logo_scale"),
    ({"logo": "logo.png", "logo_scale": 0.01}, "brand.logo_scale"),
    ({"logo": "logo.png", "logo_position": "middle"}, "brand.logo_position"),
    ({"logo": "logo.png", "logo_mode": "intro"}, "brand.logo_mode"),
    ({"logo": "logo.png", "logo_opacity": 2}, "brand.logo_opacity"),
    ({"logo": "notes.txt"}, "PNG"),
])
def test_bad_brand_settings_fail_at_load(tmp_path, override, needle):
    make_logo(tmp_path / "logo.png")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    with pytest.raises(UserError) as e:
        load_project_config(write_cfg(tmp_path, **override))
    assert needle in e.value.message


def test_bad_logo_stops_run_before_any_work(tmp_path, gym, capsys):
    footage = tmp_path / "footage"
    footage.mkdir()
    shutil.copy(gym, footage / "gym.mp4")
    cfg = write_cfg(tmp_path, footage, logo="nope.png")
    p = FakeProvider()
    code = run_cli.main(["--config", str(cfg), "--out", str(tmp_path / "out")], provider=p,
                        occ=FakeOcc(footage))
    assert code == 2 and "Không tìm thấy logo" in capsys.readouterr().err
    assert p.calls == 0 and not (tmp_path / "out" / "index").exists()


def test_valid_brand_loads(tmp_path):
    make_logo(tmp_path / "logo.png")
    cfg = load_project_config(write_cfg(tmp_path, logo="logo.png", logo_position="top-left",
                                        logo_scale=0.3, logo_mode="end", **BRAND))
    b = cfg.brand
    assert (b.name, b.tagline, b.logo_size, b.logo_position, b.logo_mode) == \
        (BRAND["name"], BRAND["tagline"], (213, 157), "top-left", "end")


# ---------------------------------------------------------------- 1c. logo geometry

def brand(**kw):
    return Brand(logo=Path("x.png"), logo_size=(1065, 786), **kw)


def test_logo_transform_matches_measured_occ_semantics():
    # Measured on occ: scale 0.22 -> 240 px wide (0.222 W); x/y = centre offset in % of W/H.
    t = logo_transform(brand(logo_scale=0.22, logo_margin=0.04, logo_position="bottom-right",
                             logo_opacity=0.85), (1080, 1920))
    assert t["scale"] == pytest.approx(0.22) and t["opacity"] == 0.85
    w, h, m = 0.22 * 1080, 0.22 * 1080 * 786 / 1065, 0.04 * 1080
    assert t["x"] == pytest.approx((1080 - m - w / 2 - 540) / 10.8, abs=1e-3)
    assert t["y"] == pytest.approx((1920 - m - h / 2 - 960) / 19.2, abs=1e-3)
    tl = logo_transform(brand(logo_position="top-left"), (1080, 1920))
    assert tl["x"] == pytest.approx(-t["x"]) and tl["y"] < 0


def test_tall_logo_is_sized_by_width_too():
    # occ fits a tall image by HEIGHT; logo_scale must still mean width / frame width
    t = logo_transform(Brand(logo=Path("x.png"), logo_size=(100, 400), logo_scale=0.22), (1080, 1920))
    fitted_w = 100 * min(1080 / 100, 1920 / 400)                      # 480 px
    assert t["scale"] * fitted_w == pytest.approx(0.22 * 1080)


# ---------------------------------------------------------------- 1d. transitions vs logo

def test_transition_window_is_centred_on_the_cut():
    assert transition_window(150, 6) == (147, 153)
    assert transition_window(150, 7) == (147, 154)


def test_logo_always_drops_every_transition():
    p = base_plan(clips=[clip(0, 5), clip(5.5, 10)],
                  transitions=[{"at": 5.0, "id": TR["assetId"], "frames": 6}])
    w = validate_plan(p, Catalogue(CATALOGUE["items"]), brand=brand(logo_mode="always"))
    assert p["transitions"] == [] and "che mất logo" in w[0]


def test_logo_end_keeps_transitions_before_the_logo():
    # 15 s timeline, logo in the last 3 s: cut at 5 s keeps its transition, cut at 13 s loses it
    p = base_plan(clips=[clip(0, 5), clip(5, 8), clip(8, 9.9)],
                  transitions=[{"at": 5.0, "id": TR["assetId"], "frames": 6},
                               {"at": 8.0, "id": TR["assetId"], "frames": 6}])
    p["sources"]["a.mp4"]["duration"] = 10.0
    validate_plan(p, Catalogue(CATALOGUE["items"]), brand=brand(logo_mode="end"))
    assert [t["at"] for t in p["transitions"]] == [5.0]               # 8 s is inside [6.9, 9.9)
    assert logo_window(brand(logo_mode="end"), 297, 30) == (207, 297)


@pytest.fixture
def footage(tmp_path, gym, sunset):
    d = tmp_path / "footage"
    d.mkdir()
    shutil.copy(gym, d / "gym.mp4")
    shutil.copy(sunset, d / "sunset.mp4")
    return d


def go(tmp_path, footage, reply, argv=(), **cfgkw):
    make_logo(tmp_path / "logo.png")
    cfg = write_cfg(tmp_path, footage, **cfgkw)
    p = FakeProvider()
    p.reply = reply
    occ = FakeOcc(footage)
    code = run_cli.main([*argv, "--config", str(cfg), "--out", str(tmp_path / "out")], provider=p, occ=occ)
    return code, p, occ


def test_logo_commands_and_no_transition_under_logo(tmp_path, footage):
    code, p, occ = go(tmp_path, footage, GOOD, logo="logo.png", **BRAND)   # GOOD asks for a transition
    assert code == 0
    labels = [r.label for r in occ.history]
    assert labels[-5:] == ["logo import", "logo track", "logo place", "logo transform", "render"]
    assert not any(lab.startswith("transition") for lab in labels)
    place = occ.history[labels.index("logo place")].tool_args["adds"][0]
    assert (place["type"], place["track"], place["fromFrame"], place["durationInFrames"]) == \
        ("image", "track_v2", 0, 180 + 120 + 150)
    tr = occ.history[labels.index("logo transform")].tool_args["updates"][0]["transform"]
    assert tr["scale"] == pytest.approx(0.22) and tr["x"] > 0 and tr["y"] > 0      # bottom-right
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert plan["transitions"] == [] and any("che mất logo" in w for w in plan["warnings"])
    assert "NO transitions" in p.prompts[0]


def test_logo_end_mode_places_last_three_seconds(tmp_path, footage):
    code, _, occ = go(tmp_path, footage, GOOD, logo="logo.png", logo_mode="end")
    place = next(r for r in occ.history if r.label == "logo place").tool_args["adds"][0]
    assert code == 0 and (place["fromFrame"], place["durationInFrames"]) == (450 - 90, 90)


# ---------------------------------------------------------------- 1b. brand goes to planner only

def test_brand_text_in_planner_prompt_never_in_caption_prompts(tmp_path, footage):
    code, p, _ = go(tmp_path, footage, GOOD, **BRAND)
    assert code == 0 and p.describe_prompts                         # captions were made
    planner = p.prompts[0]
    for text in BRAND.values():
        assert text in planner
        assert not any(text in cp for cp in p.describe_prompts)
    assert "Hoàng Gia" not in "".join(p.describe_prompts)


# ---------------------------------------------------------------- 2. script_file and footage_dir

SCRIPT = "0-3s: cận cảnh tay nắm tạ\n3-8s: toàn cảnh phòng tập\n8-12s: cảnh hồ bơi\n12-15s: logo và lời chào"


def test_user_script_is_followed_and_gaps_are_named(tmp_path, footage):
    (tmp_path / "kb.txt").write_text(SCRIPT, encoding="utf-8")
    reply = copy.deepcopy(GOOD) | {"beats": [
        {"beat": "cận cảnh tay nắm tạ", "clips": [1], "note": ""},
        {"beat": "toàn cảnh phòng tập", "clips": [2], "note": "dùng toàn cảnh gần nhất"},
        {"beat": "cảnh hồ bơi", "clips": [], "note": "footage không có hồ bơi"},
        {"beat": "logo và lời chào", "clips": [3], "note": ""}]}
    code, p, _ = go(tmp_path, footage, reply, script="kb.txt")
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert code == 0 and plan["script_source"] == "user"
    assert "USER SCRIPT" in p.prompts[0] and "cảnh hồ bơi" in p.prompts[0] and "FOLLOW" in p.prompts[0]
    w = "\n".join(plan["warnings"])
    assert "không có cảnh phù hợp cho \"cảnh hồ bơi\"" in w and "footage không có hồ bơi" in w
    assert "chỉ khớp gần đúng" in w and plan["tier"] == "basic"


def test_script_without_beat_report_is_flagged(tmp_path, footage):
    (tmp_path / "kb.txt").write_text(SCRIPT, encoding="utf-8")
    go(tmp_path, footage, GOOD, script="kb.txt")
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert any("không báo cáo" in w for w in plan["warnings"]) and plan["tier"] == "basic"


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_absent_or_empty_script_means_planner_writes_it(tmp_path, footage, content):
    if content is not None:
        (tmp_path / "kb.txt").write_text(content, encoding="utf-8")
    code, p, _ = go(tmp_path, footage, GOOD, script="kb.txt")
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert code == 0 and plan["script_source"] == "planner" and "USER SCRIPT" not in p.prompts[0]


def test_script_flag_overrides_config(tmp_path, footage):
    (tmp_path / "other.txt").write_text("chỉ dùng cảnh hoàng hôn", encoding="utf-8")
    code, p, _ = go(tmp_path, footage, GOOD, argv=["--script", str(tmp_path / "other.txt")])
    assert code == 0 and "chỉ dùng cảnh hoàng hôn" in p.prompts[0]


def test_run_with_no_arguments_uses_config(tmp_path, footage, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="videoauto")
    make_logo(tmp_path / "logo.png")
    write_cfg(tmp_path, footage, logo="logo.png", **BRAND)
    monkeypatch.chdir(tmp_path)                                  # config.yaml is found in cwd
    p = FakeProvider()
    p.reply = GOOD
    assert run_cli.main([], provider=p, occ=FakeOcc(footage)) == 0
    assert (tmp_path / "output" / "final.mp4").exists()
    assert str(footage) in caplog.text


def test_no_footage_anywhere_is_a_clear_error(tmp_path, monkeypatch, capsys):
    write_cfg(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert run_cli.main([], provider=FakeProvider(), occ=FakeOcc(tmp_path)) == 2
    assert "footage_dir" in capsys.readouterr().err
