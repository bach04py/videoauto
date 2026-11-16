from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from conftest import FakeProvider
from videoauto import plan as plan_cli
from videoauto.brief import load_project_config
from videoauto.config import load_config
from videoauto.indexer import Indexer, collect
from videoauto.planner.catalogue import Catalogue
from videoauto.planner.planner import make_plan
from videoauto.planner.validate import schema_errors, validate_plan

LUT = {"assetId": "builtin:look-teal-orange", "kind": "lut", "description": "teal shadows, orange highlights",
       "params": [{"key": "intensity", "type": "number", "default": 0.85, "min": 0, "max": 1, "editable": True},
                  {"key": "contrast", "type": "number", "default": 1.1, "min": 0.6, "max": 1.8, "editable": True}]}
FX = {"assetId": "builtin:fx-glitch", "kind": "effect",
      "params": [{"key": "intensity", "type": "number", "default": 1, "min": 0, "max": 2, "editable": True}]}
TR = {"assetId": "builtin:tr-flash", "kind": "transition",
      "params": [{"key": "durationInFrames", "type": "integer", "default": None, "min": 1, "max": None,
                  "editable": True}]}
def _tpl(key, w, h, vi_ok, params, category="social-shorts"):
    return {"assetId": f"library:motion-graphic:{key}", "id": key, "kind": "template", "name": key,
            "category": category, "width": w, "height": h, "durationInFrames": 90, "vi_ok": vi_ok,
            "vi_status": "ok" if vi_ok else "fail_vietnamese", "params": params}


def _p(key, typ, default):
    return {"key": key, "type": typ, "default": default, "editable": True}


# real ids and fields from catalogue.json (social-shorts); positions come from overlays.PLACEMENT
KEYPOP = _tpl("ss-keyword-pop", 1080, 1920, True, [
    _p("keyword", "text", "关键是坚持"), _p("subText", "text", "每天进步一点点"),
    _p("accentColor", "color", "#ff4d6d"), _p("textColor", "color", "#ffffff"), _p("fontSize", "number", 96)])
BIGCAP = _tpl("ss-big-caption", 1080, 1920, True, [         # full-width bar at the bottom
    _p("caption", "text", "先讲结论"), _p("bgColor", "color", "rgba(0,0,0,0.72)"),
    _p("textColor", "color", "#ffffff"), _p("accentColor", "color", "#38bdf8")])
NOTVI = _tpl("ss-not-vi", 1080, 1920, False, [_p("title", "text", "x")])
LOOSE = _tpl("vertical-unknown", 1080, 1920, True, [_p("title", "text", "x")], category="koubo-scenes")
WIDE = _tpl("wide-title", 1920, 1080, True, [_p("title", "text", "x")], category="text-effects")
CATALOGUE = {"items": [LUT, FX, TR, KEYPOP, BIGCAP, NOTVI, LOOSE, WIDE]}


def write_config(tmp: Path, media: Path, **brief) -> Path:
    (tmp / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    b = {"duration": 15, "aspect": "9:16", "fps": 30, "platform": "tiktok", **brief}
    lines = ["brief:"] + [f"  {k}: {json.dumps(v)}" for k, v in b.items()]
    lines += [f"media_dir: {json.dumps(str(media))}", "catalogue: catalogue.json",
              "openchatcut:", "  repo: nowhere", "  node: node"]
    p = tmp / "config.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def indexed(tmp_path_factory, gym, sunset):
    """Real M1 indexes of both fixtures (fake captions)."""
    root = tmp_path_factory.mktemp("m2")
    media = root / "media"
    media.mkdir()
    shutil.copy(gym, media / "gym.mp4")
    shutil.copy(sunset, media / "sunset.mp4")
    idx = root / "idx"
    Indexer(load_config(), idx, provider=FakeProvider()).run(collect(media, idx))
    return media, idx


GOOD = {
    "clips": [{"src": "gym.mp4", "shot": "s01", "in": 0.0, "out": 6.0},
              {"src": "sunset.mp4", "shot": "s01", "in": 1.0, "out": 5.0},
              {"src": "gym.mp4", "shot": "s01", "in": 8.0, "out": 13.0}],
    "look": {"id": "builtin:look-teal-orange", "params": {"intensity": 0.8}},
    "transitions": [{"after_clip": 1, "id": "builtin:tr-flash", "frames": 6}],
    "fades": {"in": 0.5, "out": 0.8},
    "confidence": 0.8,
    "script": "Mở đầu bằng cảnh tập gym, chuyển sang hoàng hôn, kết thúc ở phòng gym.",
}


def plan_with(indexed, tmp_path, reply, **brief):
    media, idx = indexed
    cfg = load_project_config(write_config(tmp_path, media, **brief))
    provider = FakeProvider()
    provider.reply = reply
    return make_plan(cfg, idx, provider), provider


def test_valid_reply_becomes_validated_plan(indexed, tmp_path):
    plan, provider = plan_with(indexed, tmp_path, GOOD)
    assert provider.calls == 1                                   # exactly ONE model call
    assert schema_errors(plan) == [] and plan["model_calls"] == 1
    assert plan["tier"] == "full" and plan["warnings"] == []
    assert [(c["src"], c["in"], c["out"]) for c in plan["clips"]] == \
        [("gym.mp4", 0.0, 6.0), ("sunset.mp4", 1.0, 5.0), ("gym.mp4", 8.0, 13.0)]
    assert all(c["effects"] == [{"id": "builtin:look-teal-orange", "params": [0.8]}] for c in plan["clips"])
    assert plan["transitions"] == [{"at": 6.0, "id": "builtin:tr-flash", "frames": 6}]
    assert plan["fades"] == {"in": 0.5, "out": 0.8}
    assert set(plan["sources"]) == {"gym.mp4", "sunset.mp4"}
    prompt = provider.prompts[0]
    assert "sharpness 139" in prompt and "rank 2/2" in prompt     # gym is the softer of the two
    assert "builtin:look-teal-orange" in prompt and "builtin:tr-flash" in prompt


def test_invalid_parts_are_dropped_with_warnings(indexed, tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["clips"] += [{"src": "gym.mp4", "in": 10.0, "out": 20.0},          # past the end of gym
                     {"src": "gym.mp4", "in": 4.0, "out": 7.0},            # overlaps clip 1
                     {"src": "missing.mp4", "in": 0, "out": 2}]
    bad["transitions"] = [{"after_clip": 1, "id": "builtin:tr-nope", "frames": 6},
                          {"after_clip": 3, "id": "builtin:tr-flash", "frames": 6},   # next clip dropped
                          {"after_clip": 2, "id": "builtin:tr-flash", "frames": 500}]  # too long
    bad["look"]["params"] = {"intensity": 1.5}                                # out of range
    plan, _ = plan_with(indexed, tmp_path, bad)
    assert schema_errors(plan) == []
    assert len(plan["clips"]) == 3 and plan["transitions"] == []
    assert all(c["effects"] == [] for c in plan["clips"])
    w = "\n".join(plan["warnings"])
    for needle in ("ngoài độ dài nguồn", "trùng đoạn nguồn", "không có trong chỉ mục",
                   "tr-nope", "không còn hoặc không liền nhau", "dài hơn nửa clip", "ngoài khoảng"):
        assert needle in w, needle
    assert plan["tier"] == "basic"                                         # things were dropped


def test_not_enough_material_rejects_without_a_model_call(indexed, tmp_path):
    plan, provider = plan_with(indexed, tmp_path, GOOD, duration=120)
    assert provider.calls == 0 and plan["model_calls"] == 0
    assert plan["tier"] == "reject" and plan["clips"] == []
    assert "Không đủ hình dùng được" in plan["reason"] and schema_errors(plan) == []


def test_model_reject_is_kept_not_invented(indexed, tmp_path):
    plan, _ = plan_with(indexed, tmp_path, {"reject": "Cảnh quay không hợp với brief."})
    assert plan["tier"] == "reject" and plan["reason"] == "Cảnh quay không hợp với brief."
    assert plan["clips"] == []


def test_malformed_reply_rejects(indexed, tmp_path):
    plan, provider = plan_with(indexed, tmp_path, "Here's my plan: first the gym...")
    assert provider.calls == 1 and plan["tier"] == "reject" and "sai định dạng" in plan["reason"]


def test_cli_writes_plan_and_exit_codes(indexed, tmp_path, capsys):
    media, idx = indexed
    cfg = write_config(tmp_path, media)
    out = tmp_path / "plan.json"
    ok = FakeProvider()
    ok.reply = GOOD
    assert plan_cli.main([str(idx), "--out", str(out), "--config", str(cfg)], provider=ok) == 0
    assert schema_errors(json.loads(out.read_text(encoding="utf-8"))) == []
    no = FakeProvider()
    no.reply = {"reject": "Không dùng được."}
    assert plan_cli.main([str(idx), "--out", str(out), "--config", str(cfg)], provider=no) == 5
    assert "Từ chối: Không dùng được." in capsys.readouterr().err


# ---------------------------------------------------------------- validator on its own

def base_plan(**over):
    p = {"version": 1, "target": {"duration": 10, "aspect": "9:16", "fps": 30}, "confidence": 0.9,
         "tier": "full", "clips": [], "transitions": [], "fades": {"in": 0.5, "out": 0.5}, "script": "",
         "warnings": [], "sources": {"a.mp4": {"sha256": "0" * 64, "duration": 10.0}}}
    p.update(over)
    return p


def clip(i, o, effects=()):
    return {"src": "a.mp4", "in": i, "out": o, "track": "V1", "effects": list(effects)}


def test_validator_drops_clip_on_black_shot():
    p = base_plan(clips=[clip(0, 3), clip(4, 6)])
    shots = {"a.mp4": [{"id": "s01", "start": 0, "end": 3.5, "flags": []},
                       {"id": "s02", "start": 3.5, "end": 10, "flags": ["black", "silent"]}]}
    w = validate_plan(p, Catalogue(CATALOGUE["items"]), shots)
    assert [c["in"] for c in p["clips"]] == [0] and "s02" in w[0]


def test_validator_param_ranges_and_second_lut():
    cat = Catalogue(CATALOGUE["items"])
    p = base_plan(clips=[clip(0, 3, [{"id": LUT["assetId"], "params": [0.5, 1.9]},          # contrast > 1.8
                                     {"id": FX["assetId"], "params": [1, 2]},               # too many
                                     {"id": LUT["assetId"], "params": [0.5]},
                                     {"id": LUT["assetId"], "params": [0.6]}])])            # 2nd LUT
    validate_plan(p, cat)
    assert p["clips"][0]["effects"] == [{"id": LUT["assetId"], "params": [0.5]}]
    assert len(p["warnings"]) == 3


def test_validator_transition_must_sit_on_a_cut_and_fades_bounded():
    p = base_plan(clips=[clip(0, 3), clip(5, 8)], fades={"in": 2.5, "out": 0.5},
                  transitions=[{"at": 2.0, "id": TR["assetId"], "frames": 4},
                               {"at": 3.0, "id": TR["assetId"], "frames": 4}])
    validate_plan(p, Catalogue(CATALOGUE["items"]))
    assert p["transitions"] == [{"at": 3.0, "id": TR["assetId"], "frames": 4}]
    assert p["fades"] == {"in": 0, "out": 0.5}


def test_validator_rejects_when_nothing_survives():
    p = base_plan(clips=[clip(0, 0.2), clip(9, 30)])
    validate_plan(p, Catalogue(CATALOGUE["items"]))
    assert p["tier"] == "reject" and p["reason"] and schema_errors(p) == []
