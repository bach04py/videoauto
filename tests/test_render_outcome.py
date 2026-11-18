"""Group 1: a finished render is judged by the file it wrote, never by whether stdout parsed,
and a failed build never deletes the video it produced."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from videoauto.brief import OccConfig
from videoauto.builder import builder as builder_mod
from videoauto.builder.occ import Occ, parse_stdout

PROGRESS = "rendering 1/450\nrendering 225/450\nrendering 450/450\n"
RESULT = {"ok": True, "out": "x.mp4", "frames": 450}


def occ_cfg(tmp_path: Path) -> OccConfig:
    return OccConfig(tmp_path, tmp_path / "d", "node", "1080p", 10, 0.3)


def fake_proc(monkeypatch, stdout: str, code: int = 0, stderr: str = "", write: tuple | None = None):
    def run(cmd, **kw):
        if write:
            Path(write[0]).write_bytes(write[1])
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    monkeypatch.setattr("videoauto.builder.occ.subprocess.run", run)


# ---------------------------------------------------------------- stdout parsing

@pytest.mark.parametrize("stdout", [
    json.dumps(RESULT),
    PROGRESS + json.dumps(RESULT),
    PROGRESS + json.dumps(RESULT, indent=2),
    PROGRESS + json.dumps(RESULT) + "\n\n",
    "rendering {weird} 3/9\n" + json.dumps(RESULT),
])
def test_json_is_found_after_progress_lines(stdout):
    assert parse_stdout(stdout) == RESULT


@pytest.mark.parametrize("stdout", ["", "rendering 1/2\n", "not json at all", PROGRESS + "{broken"])
def test_no_json_gives_none(stdout):
    assert parse_stdout(stdout) is None


def test_occ_run_with_progress_lines_is_ok(tmp_path, monkeypatch):
    """The real failure: `occ render` prints progress lines before its JSON, json.loads of the
    whole stdout failed, and a render with exit 0 was reported as an error."""
    fake_proc(monkeypatch, PROGRESS + json.dumps(RESULT))
    r = Occ(occ_cfg(tmp_path)).run("render", ["render", "p", "--out", "x.mp4"])
    assert r.ok and r.data == RESULT and r.returncode == 0


def test_occ_run_without_json_records_exit_code(tmp_path, monkeypatch):
    fake_proc(monkeypatch, PROGRESS)
    r = Occ(occ_cfg(tmp_path)).run("render", ["render", "p"], json_required=False)
    assert r.ok and r.data is None and r.returncode == 0
    r = Occ(occ_cfg(tmp_path)).run("project", ["project", "new"])     # JSON needed for its id
    assert not r.ok and r.returncode == 0


# ---------------------------------------------------------------- render verdict

def verdict(tmp_path, monkeypatch, *, exists=True, size=4096, duration=15.0, expected=15.0):
    out = tmp_path / "v.mp4"
    if exists:
        out.write_bytes(b"\0" * size)
    monkeypatch.setattr(builder_mod, "media_duration", lambda p: duration)
    return builder_mod.render_problem(out, expected, fps=30)


def test_render_verdict_accepts_a_good_file(tmp_path, monkeypatch):
    assert verdict(tmp_path, monkeypatch) is None
    assert verdict(tmp_path, monkeypatch, duration=15.08) is None          # AAC padding


@pytest.mark.parametrize("kw,needle", [
    ({"exists": False}, "không có tệp"),
    ({"size": 0}, "0 byte"),
    ({"duration": 7.5}, "7.50s"),
    ({"duration": None}, "không đọc được thời lượng"),
])
def test_render_verdict_refuses_bad_files(tmp_path, monkeypatch, kw, needle):
    msg = verdict(tmp_path, monkeypatch, **kw)
    assert msg and needle in msg


def test_real_duration_probe(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=30:d=2",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    assert builder_mod.media_duration(clip) == pytest.approx(2.0, abs=0.05)
    assert builder_mod.media_duration(tmp_path / "missing.mp4") is None


# ---------------------------------------------------------------- the builder end to end

def synth_setup(tmp_path, media):
    from test_planner import CATALOGUE, LUT
    from videoauto.cache import sha256_of
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"media_dir: {json.dumps(str(media))}\ncatalogue: catalogue.json\n"
        "openchatcut:\n  repo: nowhere\n  data_dir: occdata\n", encoding="utf-8")
    plan = {
        "version": 1, "target": {"duration": 7, "aspect": "9:16", "fps": 30}, "confidence": 0.8,
        "tier": "full",
        "clips": [{"src": "a.mp4", "in": 0.0, "out": 4.0, "track": "V1",
                   "effects": [{"id": LUT["assetId"], "params": [0.8]}]},
                  {"src": "b.mp4", "in": 1.0, "out": 4.0, "track": "V1", "effects": []}],
        "transitions": [], "fades": {"in": 0, "out": 0}, "script": "", "warnings": [],
        "sources": {"a.mp4": {"sha256": sha256_of(media / "a.mp4"), "duration": 8.0},
                    "b.mp4": {"sha256": sha256_of(media / "b.mp4"), "duration": 6.0}},
    }
    return plan


def run_build(tmp_path, media, plan, **occ_kw):
    from test_builder import FakeOcc
    from videoauto.brief import load_project_config
    from videoauto.builder.builder import Builder
    occ = FakeOcc(media, **occ_kw)
    return Builder(load_project_config(tmp_path / "config.yaml"), occ).build(plan, tmp_path / "out" / "v.mp4")


def test_render_without_stdout_json_is_a_success(tmp_path, synth_media):
    rep = run_build(tmp_path, synth_media, synth_setup(tmp_path, synth_media), render_json=False)
    assert rep.error is None and rep.output.exists()


def test_short_render_is_refused_and_finish_keeps_the_video(tmp_path, synth_media):
    from videoauto.build import finish_build
    from videoauto.errors import BuildAborted
    rep = run_build(tmp_path, synth_media, synth_setup(tmp_path, synth_media), render_seconds=2.0)
    assert isinstance(rep.error, BuildAborted) and "dài 2.00s" in rep.error.message
    with pytest.raises(BuildAborted) as e:
        finish_build(rep, tmp_path / "out" / "v.mp4")
    kept = tmp_path / "out" / "v.loi.mp4"
    assert kept.is_file() and not (tmp_path / "out" / "v.mp4").exists()
    assert str(kept) in e.value.message
    report = json.loads((tmp_path / "out" / "v.mp4.build.json").read_text(encoding="utf-8"))
    assert report["kept_video"] == str(kept) and report["ok"] is False


def test_keep_failed_video_never_overwrites(tmp_path):
    from videoauto.build import keep_failed_video
    assert keep_failed_video(tmp_path / "none.mp4") is None
    for n, want in ((1, "v.loi.mp4"), (2, "v.loi-2.mp4")):
        (tmp_path / "v.mp4").write_bytes(b"video %d" % n)
        assert keep_failed_video(tmp_path / "v.mp4").name == want
    assert (tmp_path / "v.loi.mp4").read_bytes() == b"video 1"


# ---------------------------------------------------------------- the watcher

def test_watcher_keeps_the_video_of_a_failed_build(tmp_path, monkeypatch, synth_media):
    """The 21.6 MB case: the render finished, a later step called it a failure, and the watcher
    deleted the file. Now it stays as <name>.loi.mp4 and the failure note names it."""
    from conftest import FakeProvider
    from test_builder import FakeOcc
    from videoauto import run as run_mod
    from videoauto import watch
    from videoauto.errors import BuildAborted

    plan = synth_setup(tmp_path, synth_media)
    scripts, out = tmp_path / "kb", tmp_path / "xuat"
    scripts.mkdir()
    from videoauto.brief import load_project_config
    env = watch.Env(load_project_config(tmp_path / "config.yaml"), {}, FakeProvider(), FakeOcc(synth_media))

    def pipeline(pcfg, vcfg, provider, occ, sources, index_dir, plan_path, video, result, on_stage):
        result.stage = "build"
        video.write_bytes(b"\0" * 21_600)          # a whole render...
        raise BuildAborted("occ render: kết quả không đọc được")   # ...then refused

    monkeypatch.setattr(run_mod, "run_pipeline", pipeline)
    w = watch.Watcher({"footage": synth_media, "scripts": scripts, "output": out}, env)
    w.prepare()
    (scripts / "tap.txt").write_text("Kịch bản", encoding="utf-8")
    claimed = w.claim(scripts / "tap.txt")
    assert w.build(claimed) is False
    kept = out / "tap.loi.mp4"
    assert kept.is_file() and kept.stat().st_size == 21_600
    assert not (out / "tap.mp4").exists()
    note = (out / "failed" / "tap.loi.txt").read_text(encoding="utf-8")
    assert str(kept) in note
