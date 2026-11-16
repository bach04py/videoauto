from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_planner import CATALOGUE, FX, LUT, TR
from videoauto import build as build_cli
from videoauto.brief import OccConfig, load_project_config
from videoauto.builder.builder import Builder
from videoauto.builder.occ import Occ, OccResult
from videoauto.cache import sha256_of


class FakeOcc:
    """Records every occ command; `fail` = set of label prefixes that fail."""

    def __init__(self, media: Path, fail: tuple[str, ...] = ()):
        self.media, self.fail, self.history, self.n = media, fail, [], 0

    def check(self):
        pass

    def run(self, label, args, tool_args=None, timeout=300):
        self.n += 1
        ok = not any(label.startswith(f) for f in self.fail)
        data = None
        if ok:
            if args[:2] == ["project", "new"]:
                data = {"id": "proj1"}
            elif "import_folder" in args:
                data = {"applied": True, "ops": [{"result": {"ok": True, "imported": [
                    {"id": f"asset-{p.name}", "name": p.name} for p in sorted(self.media.iterdir())]}}]}
            elif "edit_item" in args and tool_args.get("adds", [{}])[0].get("type") == "video":
                data = {"applied": True, "ops": [{"result": {"ok": True, "results": [
                    {"placed": {"itemId": f"item{self.n}"}}]}}]}
            elif args[0] == "render":
                Path(args[args.index("--out") + 1]).write_bytes(b"\0" * 2048)
                data = {"frames": 1}
            else:
                data = {"applied": True, "ops": [{"result": {"ok": True}}]}
        res = OccResult(label, args, ok, 0.01, data, "" if ok else "boom", tool_args)
        self.history.append(res)
        return res


@pytest.fixture
def setup(tmp_path, gym, sunset):
    media = tmp_path / "media"
    media.mkdir()
    shutil.copy(gym, media / "gym.mp4")
    shutil.copy(sunset, media / "sunset.mp4")
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"media_dir: {json.dumps(str(media))}\ncatalogue: catalogue.json\n"
        "openchatcut:\n  repo: nowhere\n  data_dir: occdata\n", encoding="utf-8")
    plan = {
        "version": 1, "target": {"duration": 15, "aspect": "9:16", "fps": 30}, "confidence": 0.8,
        "tier": "full",
        "clips": [{"src": "gym.mp4", "in": 0.0, "out": 6.0, "track": "V1",
                   "effects": [{"id": LUT["assetId"], "params": [0.8]}]},
                  {"src": "sunset.mp4", "in": 1.0, "out": 5.0, "track": "V1",
                   "effects": [{"id": LUT["assetId"], "params": [0.8]}]},
                  {"src": "gym.mp4", "in": 8.0, "out": 13.0, "track": "V1",
                   "effects": [{"id": LUT["assetId"], "params": [0.8]}]}],
        "transitions": [{"at": 6.0, "id": TR["assetId"], "frames": 6}],
        "fades": {"in": 0.5, "out": 0.8}, "script": "", "warnings": [],
        "sources": {"gym.mp4": {"sha256": sha256_of(gym), "duration": 15.542},
                    "sunset.mp4": {"sha256": sha256_of(sunset), "duration": 9.381}},
    }
    return tmp_path, media, plan


def build(tmp_path, media, plan, fail=()):
    cfg = load_project_config(tmp_path / "config.yaml")
    occ = FakeOcc(media, fail)
    rep = Builder(cfg, occ).build(plan, tmp_path / "out" / "final.mp4")
    return rep, occ


def test_build_runs_the_sequence(setup):
    tmp_path, media, plan = setup
    rep, occ = build(tmp_path, media, plan)
    assert rep.error is None and rep.output.exists() and rep.failed == 0
    labels = [r.label for r in occ.history]
    assert labels == ["project new", "import_folder", "clip 1 gym.mp4", "clip 2 sunset.mp4",
                      "clip 3 gym.mp4", "effect clip 1", "effect clip 2", "effect clip 3",
                      "transition tr-flash", "fade in", "fade out", "render"]
    assert occ.history[0].argv[-4:] == ["--size", "1080x1920", "--fps", "30"]
    adds = [r.tool_args["adds"][0] for r in occ.history[2:5]]
    assert [(a["fromFrame"], a["sourceStartFrame"], a["sourceDurationInFrames"]) for a in adds] == \
        [(0, 0, 180), (180, 30, 120), (300, 240, 150)]
    assert adds[1]["assetId"] == "asset-sunset.mp4"
    assert occ.history[5].tool_args == {"action": "add", "targetItemId": "item3", "assetId": LUT["assetId"],
                                        "propertyOverrides": {"intensity": 0.8}}
    tr = occ.history[8].tool_args["adds"][0]
    assert (tr["outgoingItemId"], tr["incomingItemId"], tr["durationInFrames"]) == ("item3", "item4", 6)
    assert occ.history[9].tool_args["updates"][0] == {"type": "video", "itemId": "item3", "fadeInSeconds": 0.5}
    assert occ.history[10].tool_args["updates"][0]["itemId"] == "item5"
    assert occ.history[11].argv[-2:] == ["--bitrate", "10000000"]       # default 10 Mbps


def test_one_failed_item_is_skipped_and_recorded(setup):
    tmp_path, media, plan = setup
    rep, occ = build(tmp_path, media, plan, fail=("effect clip 2",))
    assert rep.error is None and rep.output.exists()
    assert rep.failed == 1 and rep.attempted == 9
    assert any("effect clip 2" in w for w in rep.warnings)


def test_failed_clip_packs_left_and_skips_its_transition(setup):
    tmp_path, media, plan = setup
    rep, occ = build(tmp_path, media, plan, fail=("clip 2",))
    assert rep.error is None and rep.failed == 1
    adds = [r.tool_args["adds"][0] for r in occ.history if r.label.startswith("clip ") and r.ok]
    assert [a["fromFrame"] for a in adds] == [0, 180]                  # clip 3 moved into the gap
    assert not any(r.label.startswith("transition") for r in occ.history)
    assert any("chuyển cảnh" in w for w in rep.warnings)


def test_more_than_30_percent_failures_abort_before_render(setup):
    tmp_path, media, plan = setup
    rep, occ = build(tmp_path, media, plan, fail=("clip",))
    assert rep.error is not None and "Dừng dựng" in rep.error.message
    assert "render" not in [r.label for r in occ.history] and not rep.output.exists()


def test_fatal_step_failure_aborts(setup):
    tmp_path, media, plan = setup
    rep, occ = build(tmp_path, media, plan, fail=("import_folder",))
    assert rep.error is not None and "nhập thư mục video" in rep.error.message
    assert [r.label for r in occ.history] == ["project new", "import_folder"]


def test_changed_media_is_not_used(setup):
    tmp_path, media, plan = setup
    plan["sources"]["sunset.mp4"]["sha256"] = "f" * 64
    rep, occ = build(tmp_path, media, plan)
    assert rep.error is None
    assert [r.label for r in occ.history if r.label.startswith("clip")] == ["clip 1 gym.mp4", "clip 2 gym.mp4"]
    assert any("không có tệp khớp nội dung" in w for w in rep.warnings)


def test_cli_writes_report_and_refuses_reject(setup, capsys):
    tmp_path, media, plan = setup
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out" / "final.mp4"
    code = build_cli.main([str(p), "--out", str(out), "--config", str(tmp_path / "config.yaml")],
                          occ=FakeOcc(media, fail=("clip",)))
    assert code == 6 and "Dừng dựng" in capsys.readouterr().err
    report = json.loads((tmp_path / "out" / "final.mp4.build.json").read_text(encoding="utf-8"))
    assert report["ok"] is False and len(report["commands"]) >= 3

    p.write_text(json.dumps({**plan, "tier": "reject", "reason": "Không đủ hình.", "clips": []}),
                 encoding="utf-8")
    code = build_cli.main([str(p), "--out", str(out), "--config", str(tmp_path / "config.yaml")],
                          occ=FakeOcc(media))
    assert code == 2 and "Không đủ hình." in capsys.readouterr().err


def test_occ_runner_uses_one_data_dir_and_args_file(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        seen.update(cmd=cmd, cwd=cwd, data=env["OPENCHATCUT_DATA_DIR"])
        at = next(a for a in cmd if a.startswith("@"))
        seen["args"] = json.loads(Path(at[1:]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"applied": True, "ops": [{"result": {"ok": True}}]}), "")

    monkeypatch.setattr("videoauto.builder.occ.subprocess.run", fake_run)
    cfg = OccConfig(repo=tmp_path, data_dir=tmp_path / "d", node="node", resolution="1080p",
                    timeout=10, max_fail_ratio=0.3)
    r = Occ(cfg).run("x", ["tools", "call", "edit_item", "--project", "p", "--apply"], {"adds": [{"a": "đ"}]})
    assert r.ok
    assert seen["cmd"][:4] == ["node", "--import", "tsx", "cli/main.ts"] and seen["cmd"][-1] == "--json"
    assert seen["cwd"] == tmp_path and seen["data"] == str(tmp_path / "d")
    assert seen["args"] == {"adds": [{"a": "đ"}]}


def test_occ_runner_reports_tool_failure(tmp_path, monkeypatch):
    out = {"applied": False, "ops": [{"result": {"ok": False, "error": "unknown effect"}}]}
    monkeypatch.setattr("videoauto.builder.occ.subprocess.run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(out), ""))
    cfg = OccConfig(tmp_path, tmp_path, "node", "1080p", 10, 0.3)
    r = Occ(cfg).run("fx", ["tools", "call", "manage_effects"], {"action": "add"})
    assert not r.ok and "unknown effect" in r.error
