"""Videos of 15 s to 3 min: the length comes from the script's last timed beat, the footage is
checked (with numbers) before any model call, clip lengths vary with the content, and the render
time is estimated before occ render."""

from __future__ import annotations

import json
import logging
import shutil

import pytest

from conftest import FakeProvider, make_clip
from test_builder import FakeOcc
from test_planner import CATALOGUE
from videoauto import duration as du
from videoauto.errors import UserError


def script(seconds: int, step: int = 3) -> str:
    lines = ["Video thử, khung dọc."]
    for a in range(0, seconds, step):
        b = min(a + step, seconds)
        stamp = f"{a}-{b}s" if seconds < 60 else f"{a // 60}:{a % 60:02d}-{b // 60}:{b % 60:02d}"
        lines += [f"{stamp:12}| Cảnh tập số {a // step + 1}.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- length from the script

@pytest.mark.parametrize("seconds", [15, 30, 60, 180])
def test_length_is_the_end_of_the_last_timed_beat(seconds):
    assert du.script_end(script(seconds)) == seconds
    assert du.effective(script(seconds), 15) == (seconds, "mốc thời gian nhịp cuối của kịch bản")


@pytest.mark.parametrize("text,end", [
    ("0-3s   | a\n3-6s | b\n12-15s | c", 15),
    ("0:00-0:03 | a\n2:45-3:00 | b", 180),
    ("[1:30 – 1:45] cảnh cuối", 105),
    ("0 - 4 giây: mở\n4 - 9,5 giây: kết", 9.5),
    ("Nhịp: cắt mỗi 2,5-3 giây\nTEXT: A", None),          # a time range inside a sentence is no beat
    ("2-3 người tập cùng lúc", None),                       # a count, not a time
    ("Video 30 giây, không có mốc", None),
])
def test_timestamp_formats(text, end):
    assert du.script_end(text) == end


def test_outside_5_to_180_seconds_is_refused_with_the_fix():
    with pytest.raises(UserError, match="từ 5 đến 180 giây"):
        du.effective(script(200, 10), 15)
    with pytest.raises(UserError, match="nhịp cuối"):
        du.effective("0-3s | a", 15)


def test_brief_duration_is_only_the_fallback():
    assert du.effective("", 20) == (20, "brief.duration trong config.yaml (model tự viết kịch bản)")
    assert du.effective("Chỉ có lời, không mốc", 20)[0] == 20


def test_brief_duration_range_in_config(tmp_path):
    from videoauto.brief import load_project_config
    for bad in (4, 181):
        (tmp_path / "config.yaml").write_text(f"brief:\n  duration: {bad}\n", encoding="utf-8")
        with pytest.raises(UserError, match="5–180"):
            load_project_config(tmp_path / "config.yaml")


# ---------------------------------------------------------------- footage checked before the model

def test_shortage_message_has_the_numbers():
    assert du.shortage(180, 0.6, 42, "x") == (
        "Không đủ footage: cần ít nhất 108 giây footage dùng được cho video 180 giây, hiện có 42 giây (x). "
        "Thêm clip vào thư mục video hoặc rút ngắn kịch bản.")
    assert du.shortage(15, 0.6, 9, "x") is None


# ---------------------------------------------------------------- cut rhythm

@pytest.mark.parametrize("seconds", [15, 30, 60, 180])
def test_cut_plan_scales_with_length(seconds):
    c = du.cut_plan(seconds)
    assert c["count_lo"] <= seconds / c["avg"] <= c["count_hi"]
    assert c["max"] >= 2 * c["min"]
    rules = du.cut_rules(seconds)
    assert f"about {c['count_lo']}-{c['count_hi']} clips" in rules and "NEVER give every clip the same length" in rules


def test_longer_videos_get_longer_and_more_varied_clips():
    a, b = du.cut_plan(15), du.cut_plan(180)
    assert b["avg"] > a["avg"] and b["max"] - b["min"] > a["max"] - a["min"]


def test_equal_clip_lengths_are_flagged():
    assert du.monotony([3.0] * 60) and "60 clip dài gần bằng nhau" in du.monotony([3.0] * 60)
    assert du.monotony([2.0, 3.5, 5.0, 2.5, 4.0]) is None
    assert du.monotony([3.0, 3.0, 3.0]) is None                  # too few to call it a rhythm


# ---------------------------------------------------------------- render estimate

def test_render_estimate_default_then_learned(tmp_path):
    assert "khoảng 5 phút cho video 15 giây (~21× thời lượng" in du.render_estimate(15, tmp_path)
    du.record_render(tmp_path, 1.0, 30)           # setup's 1 s check: start-up cost, not counted
    du.record_render(tmp_path, 30, 300)
    du.record_render(tmp_path, 60, 600)
    msg = du.render_estimate(180, tmp_path)
    assert "khoảng 30 phút cho video 180 giây (~10× thời lượng, trung vị 2 lần render trước" in msg


# ---------------------------------------------------------------- the whole pipeline, 15 s to 3 min

class Planner(FakeProvider):
    """Captions like FakeProvider; the plan reply fills the target with clips of varying length
    from the one long shot, and records the prompt."""

    def complete(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        import re
        target = float(re.search(r"target duration: ([0-9.]+) s", prompt).group(1))
        clips, t, k = [], 0.0, 0
        sizes = [2.0, 3.5, 5.0, 2.5, 4.5, 3.0, 6.0]
        while t < target - 0.01:
            d = min(sizes[k % len(sizes)], target - t)
            clips.append({"src": "long.mp4", "shot": "s01", "in": round(t, 3), "out": round(t + d, 3)})
            t += d
            k += 1
        return json.dumps({"clips": clips, "look": {"id": "builtin:look-teal-orange", "params": {"intensity": 0.8}},
                           "transitions": [], "fades": {"in": 0.5, "out": 0.5}, "confidence": 0.9,
                           "script": "ok", "beats": [{"beat": "all", "clips": [1], "note": ""}]})


@pytest.fixture(scope="module")
def long_footage(tmp_path_factory):
    d = tmp_path_factory.mktemp("long")
    make_clip(d / "footage" / "long.mp4", 200, size="160x284")
    return d / "footage"


@pytest.fixture(scope="module")
def long_index(long_footage):
    """Indexed once (about 20 s); each test gets its own copy."""
    from videoauto.config import load_config
    from videoauto.indexer import Indexer, collect
    idx = long_footage.parent / "index"
    Indexer(load_config(), idx, provider=FakeProvider()).run(collect(long_footage, idx))
    return idx


def pipeline(tmp_path, footage, text, cfg_extra="", index=None):
    from videoauto.brief import load_project_config
    from videoauto.config import load_config
    from videoauto.indexer import collect
    from videoauto.run import run_pipeline
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "s.txt").write_text(text, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"brief:\n  duration: 15\n  fit: fit\nfootage_dir: {json.dumps(str(footage))}\nscript_file: s.txt\n"
        f"catalogue: catalogue.json\nopenchatcut:\n  repo: nowhere\n  data_dir: occ\n{cfg_extra}", encoding="utf-8")
    pcfg = load_project_config(tmp_path / "config.yaml")
    provider, occ, notes = Planner(), FakeOcc(footage), []
    idx = tmp_path / "index"
    if index is not None:
        shutil.copytree(index, idx)
    res = run_pipeline(pcfg, load_config(), provider, occ, collect(footage, idx), idx, tmp_path / "plan.json",
                       tmp_path / "out.mp4", on_note=notes.append)
    return res, provider, occ, notes, json.loads((tmp_path / "plan.json").read_text(encoding="utf-8")), pcfg


@pytest.mark.parametrize("seconds", [15, 30, 60, 180])
def test_pipeline_builds_the_script_length(tmp_path, long_footage, long_index, seconds):
    res, provider, occ, notes, plan, pcfg = pipeline(tmp_path, long_footage, script(seconds), index=long_index)
    assert res.model_calls == 1                                        # the plan only: captions cached
    assert plan["target"]["duration"] == seconds                       # not config's 15
    assert pcfg.brief.duration == 15                                   # the shared config is untouched
    total = sum(c["out"] - c["in"] for c in plan["clips"])
    assert abs(total - seconds) < 0.05
    lengths = {round(c["out"] - c["in"], 2) for c in plan["clips"]}
    assert len(lengths) > 1 and not any("gần bằng nhau" in w for w in plan["warnings"])
    c = du.cut_plan(seconds)
    assert f"target duration: {seconds:g} s" in provider.prompts[0]
    assert f"about {c['count_lo']}-{c['count_hi']} clips" in provider.prompts[0]
    est = next(n for n in notes if n.startswith("ước tính thời gian render"))
    assert f"cho video {seconds:g} giây" in est
    labels = [r.label for r in occ.history]
    assert labels[-1] == "render"


def test_not_enough_footage_stops_before_any_model_call(tmp_path):
    footage = tmp_path / "footage"
    for n, s in enumerate((14, 14, 14)):
        make_clip(footage / f"c{n}.mp4", s, size="64x112")
    with pytest.raises(UserError) as e:
        pipeline(tmp_path, footage, script(180, 10))
    assert "cần ít nhất 108 giây footage dùng được cho video 180 giây, hiện có 42 giây" in e.value.message
    assert not (tmp_path / "index").exists() or not list((tmp_path / "index").glob("*.json"))


def test_equal_cuts_from_the_model_are_reported(tmp_path, long_footage, long_index):
    from videoauto.planner import planner as pl

    class Equal(Planner):
        def complete(self, prompt):
            self.calls += 1
            self.prompts.append(prompt)
            clips = [{"src": "long.mp4", "shot": "s01", "in": 3.0 * i, "out": 3.0 * i + 3.0} for i in range(20)]
            return json.dumps({"clips": clips, "look": {"id": "builtin:look-teal-orange", "params": {}},
                               "transitions": [], "fades": {"in": 0.5, "out": 0.5}, "confidence": 0.9,
                               "script": "ok", "beats": [{"beat": "all", "clips": [1], "note": ""}]})
    from videoauto.brief import load_project_config
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "s.txt").write_text(script(60), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"footage_dir: {json.dumps(str(long_footage))}\nscript_file: s.txt\ncatalogue: catalogue.json\n"
        "openchatcut:\n  repo: nowhere\n", encoding="utf-8")
    pcfg = load_project_config(tmp_path / "config.yaml")
    import dataclasses
    pcfg = dataclasses.replace(pcfg, brief=dataclasses.replace(pcfg.brief, duration=60))
    plan = pl.make_plan(pcfg, long_index, Equal())
    assert any("20 clip dài gần bằng nhau" in w for w in plan["warnings"]) and plan["tier"] == "basic"


def test_the_plan_command_alone_also_takes_the_length_from_the_script(tmp_path, long_footage, long_index):
    """python -m videoauto.plan (not only run/watch): an 8 s script plans 8 s, not config's 15."""
    from videoauto import plan as plan_cli
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "kb.txt").write_text("0-3s | a\n3-6s | b\n6-8s | c\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("brief:\n  duration: 15\ncatalogue: catalogue.json\n"
                                          "openchatcut:\n  repo: nowhere\n", encoding="utf-8")
    out = tmp_path / "plan.json"
    code = plan_cli.main([str(long_index), "--out", str(out), "--config", str(tmp_path / "config.yaml"),
                          "--footage", str(long_footage), "--script", str(tmp_path / "kb.txt")], provider=Planner())
    assert code == 0 and json.loads(out.read_text(encoding="utf-8"))["target"]["duration"] == 8
