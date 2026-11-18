"""Group 4: horizontal footage fills a vertical frame (centre crop, no bars); indexing analyses a
small proxy instead of decoding 4K three times; contact sheets are described concurrently."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import FakeProvider, make_clip
from test_builder import FakeOcc
from videoauto import proxy
from videoauto.brief import load_project_config
from videoauto.builder.builder import Builder
from videoauto.cache import sha256_of
from videoauto.config import load_config
from videoauto.crop import Crop, cropped_away, detect_crop, fill_scale
from videoauto.errors import LimitReached, UserError
from videoauto.probe import probe


# ---------------------------------------------------------------- 11. fill the frame

def test_fill_scale_matches_what_occ_needs():
    # measured on the real occ: 1920x1080 in 1080x1920 with scale 3.16049 fills the frame
    assert fill_scale(1920, 1080, 1080, 1920) == pytest.approx(3.16049, abs=1e-4)
    assert fill_scale(1080, 1920, 1080, 1920) == pytest.approx(1.0)
    assert fill_scale(3840, 2160, 1920, 1080) == pytest.approx(1.0)
    assert cropped_away(1920, 1080, 1080, 1920) == pytest.approx(0.6836, abs=1e-3)   # sides of 16:9 lost


@pytest.fixture(scope="module")
def mixed_media(tmp_path_factory):
    media = tmp_path_factory.mktemp("mixed") / "media"
    make_clip(media / "doc.mp4", 6, size="270x480")          # 9:16
    make_clip(media / "ngang.mp4", 6, size="640x360")        # 16:9
    return media


def mixed_plan(media):
    return {
        "version": 1, "target": {"duration": 8, "aspect": "9:16", "fps": 30}, "confidence": 0.8, "tier": "full",
        "clips": [{"src": "doc.mp4", "in": 0.0, "out": 4.0, "track": "V1", "effects": []},
                  {"src": "ngang.mp4", "in": 1.0, "out": 5.0, "track": "V1", "effects": []}],
        "transitions": [], "fades": {"in": 0, "out": 0}, "script": "", "warnings": [],
        "sources": {n: {"sha256": sha256_of(media / n), "duration": 6.0} for n in ("doc.mp4", "ngang.mp4")},
    }


def build_mixed(tmp_path, media, extra=""):
    from test_planner import CATALOGUE
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(f"media_dir: {json.dumps(str(media))}\ncatalogue: catalogue.json\n"
                                          f"{extra}openchatcut:\n  repo: nowhere\n", encoding="utf-8")
    occ = FakeOcc(media)
    rep = Builder(load_project_config(tmp_path / "config.yaml"), occ).build(mixed_plan(media), tmp_path / "v.mp4")
    return rep, occ


def test_horizontal_clip_is_scaled_to_fill_a_vertical_frame(tmp_path, mixed_media):
    rep, occ = build_mixed(tmp_path, mixed_media)
    assert rep.error is None
    fills = [r for r in occ.history if r.label.startswith("fill clip")]
    assert [r.label for r in fills] == ["fill clip 2"]                    # only the 16:9 one
    upd = fills[0].tool_args["updates"][0]
    assert upd["type"] == "video" and upd["transform"] == {"scale": 3.16049, "x": 0, "y": 0}
    clip2 = next(r for r in occ.history if r.label == "clip 2 ngang.mp4")
    assert upd["itemId"] == clip2.data["ops"][0]["result"]["results"][0]["placed"]["itemId"]


def test_fit_keeps_the_whole_picture(tmp_path, mixed_media):
    rep, occ = build_mixed(tmp_path, mixed_media, "brief:\n  fit: fit\n")
    assert rep.error is None and not any(r.label.startswith("fill") for r in occ.history)


def test_bad_fit_value(tmp_path):
    (tmp_path / "config.yaml").write_text("brief:\n  fit: stretch\n", encoding="utf-8")
    with pytest.raises(UserError, match="brief.fit"):
        load_project_config(tmp_path / "config.yaml")


def test_planner_is_told_the_truth_about_cropping(tmp_path):
    from videoauto.planner.planner import _fit_rule
    (tmp_path / "config.yaml").write_text("brief:\n  aspect: \"9:16\"\n", encoding="utf-8")
    rule = _fit_rule(load_project_config(tmp_path / "config.yaml").brief)
    assert "FILL" in rule and "CENTRE" in rule and "Do not warn" in rule


def test_clean_machine_build_fills_the_frame(clean_machine, mixed_media):
    """Committed config.yaml as is (no fit key: the default), a 16:9 clip in its 9:16 brief."""
    from test_planner import CATALOGUE
    (clean_machine / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    cfg = load_project_config(clean_machine / "config.yaml")
    assert cfg.brief.aspect == "9:16" and cfg.brief.fit == "fill"
    cfg.footage_dir = mixed_media
    occ = FakeOcc(mixed_media)
    rep = Builder(cfg, occ).build(mixed_plan(mixed_media), clean_machine / "out" / "v.mp4")
    assert rep.error is None and any(r.label == "fill clip 2" for r in occ.history)


# ---------------------------------------------------------------- 12a. analysis proxy

@pytest.fixture(scope="module")
def big_clip(tmp_path_factory) -> Path:
    """1280x720 picture letterboxed in 1920x1080 (bars top and bottom), a hard cut at 2 s and a
    black second at 3-4 s: everything the proxy must reproduce."""
    out = tmp_path_factory.mktemp("big") / "big.mp4"
    graph = ("[0:v]scale=1280:720,pad=1920:1080:320:180[a];[1:v]scale=1280:720,pad=1920:1080:320:180[b];"
             "[2:v]scale=1920:1080[k];[3:v]scale=1280:720,pad=1920:1080:320:180[c];"
             "[a][b][k][c]concat=n=4:v=1:a=0[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=2",
                    "-f", "lavfi", "-t", "1", "-i", "mandelbrot=s=1280x720:r=30",
                    "-f", "lavfi", "-t", "1", "-i", "color=c=black:s=1920x1080:r=30",
                    "-f", "lavfi", "-i", "smptebars=s=1280x720:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=300:d=6",
                    "-filter_complex", graph, "-map", "[v]", "-map", "4:a", "-shortest",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    return out


def test_proxy_gives_the_same_analysis(big_clip, tmp_path, cfg):
    from videoauto import frames, quality
    pr = probe(big_clip)
    assert proxy.needs_proxy(pr)
    made = proxy.make_proxy(big_clip, pr, tmp_path, "off")
    assert made is not None
    ppath, ppr = made
    assert (ppr.width, ppr.height) == (640, 360) and ppr.fps == pr.fps and ppr.duration == pr.duration

    direct_crop = detect_crop(big_clip, pr.width, pr.height)
    pcrop = detect_crop(ppath, ppr.width, ppr.height)
    mapped = proxy.to_source(pcrop, ppr, pr)
    assert direct_crop is not None and mapped is not None
    for a, b in ((mapped.w, direct_crop.w), (mapped.h, direct_crop.h), (mapped.x, direct_crop.x),
                 (mapped.y, direct_crop.y)):
        assert abs(a - b) <= 8                                       # a few source pixels

    s = cfg["shots"]
    old = frames.analyse(big_clip, pr, direct_crop, s["threshold"], s["min_scene_len"])
    new = frames.analyse(ppath, ppr, pcrop, s["threshold"], s["min_scene_len"])
    assert len(old.cuts) == len(new.cuts) >= 2
    assert all(abs(a - b) <= 1 for a, b in zip(old.cuts, new.cuts))      # same cut, +-1 frame
    q_old = quality.detect(big_clip, pr.duration, True, -50, direct_crop)
    q_new = quality.detect(ppath, pr.duration, True, -50, pcrop)
    assert len(q_old.black) == len(q_new.black) == 1
    assert abs(q_old.black[0][0] - q_new.black[0][0]) < 0.1 and abs(q_old.black[0][1] - q_new.black[0][1]) < 0.1
    assert q_new.silent == q_old.silent == []                            # the audio came along


def test_small_sources_are_analysed_directly(synth_media, tmp_path):
    pr = probe(synth_media / "a.mp4")
    assert not proxy.needs_proxy(pr) and proxy.make_proxy(synth_media / "a.mp4", pr, tmp_path) is None


def test_to_source_stays_inside_the_frame():
    from videoauto.probe import Probe
    src, prx = Probe(10, 30, 3840, 2160, True, True), Probe(10, 30, 640, 360, True, True)
    assert proxy.to_source(Crop(640, 270, 0, 45), prx, src) == Crop(3840, 1620, 0, 270)
    assert proxy.to_source(Crop(641, 361, 0, 0), prx, src) == Crop(3840, 2160, 0, 0)
    assert proxy.to_source(None, prx, src) is None


def test_indexer_uses_the_proxy_and_finds_the_cut(big_clip, tmp_path, monkeypatch):
    from videoauto.indexer import Indexer, collect
    made = []
    real = proxy.make_proxy
    monkeypatch.setattr("videoauto.indexer.make_proxy", lambda *a: made.append(a[0]) or real(*a))
    rep = Indexer(load_config(), tmp_path / "idx", provider=FakeProvider()).run(collect(big_clip, tmp_path / "idx"))
    ix = json.loads(rep.written[0].read_text(encoding="utf-8"))
    assert made == [big_clip] and len(ix["shots"]) >= 3
    assert any("black" in s["flags"] for s in ix["shots"])
    assert ix["width"] == 1920 and ix["height"] == 1080                  # the source's, not the proxy's


# ---------------------------------------------------------------- 12c. NVDEC

def test_nvdec_needs_a_driver_and_an_ffmpeg_with_cuda():
    def no(_):
        raise OSError("nvcuda.dll")
    assert not proxy.nvdec_available("win32", no, "cuda d3d11va")
    assert not proxy.nvdec_available("win32", lambda n: None, "d3d11va qsv")
    assert proxy.nvdec_available("win32", lambda n: None, "Hardware acceleration methods:\ncuda\nd3d11va")
    assert not proxy.nvdec_available("darwin", lambda n: None, "cuda")


def test_failed_nvdec_falls_back_to_cpu_once(big_clip, tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "_state", {"nvdec": True})
    seen = []
    real = subprocess.run

    def run(cmd, **kw):
        if "libx264" in cmd:                       # the proxy encodes (not the probes)
            seen.append("-hwaccel" in cmd)
        if "-hwaccel" in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", b"Cannot load nvcuda.dll")
        return real(cmd, **kw)
    monkeypatch.setattr(proxy.subprocess, "run", run)
    pr = probe(big_clip)
    assert proxy.make_proxy(big_clip, pr, tmp_path, "auto") is not None
    assert proxy.make_proxy(big_clip, pr, tmp_path, "auto") is not None
    assert seen == [True, False, False]                                  # GPU tried once, then CPU only


# ---------------------------------------------------------------- 12b. concurrent captions

class SlowProvider(FakeProvider):
    """Each description takes `delay` seconds; records how many ran at the same time."""

    def __init__(self, delay=0.3, fail_on=None):
        super().__init__()
        self.delay, self.fail_on = delay, fail_on
        self.lock, self.active, self.peak = threading.Lock(), 0, 0

    def describe(self, image, prompt):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls += 1
            n = self.calls
        try:
            time.sleep(self.delay)
            if self.fail_on == n:
                raise LimitReached("hết lượt", retry_hint=None)
            self.calls -= 1                                  # FakeProvider.describe counts again
            return FakeProvider.describe(self, image, prompt)
        finally:
            with self.lock:
                self.active -= 1


def jobs(tmp_path, n):
    from videoauto.vision.captioner import ShotJob
    import cv2
    import numpy as np
    out = []
    for i in range(n):
        p = tmp_path / f"k{i}.jpg"
        cv2.imwrite(str(p), np.full((90, 160, 3), i * 20 % 255, np.uint8))
        out.append(ShotJob((f"sha{i}", "s01"), [p], "static", "vertical"))
    return out


@pytest.mark.parametrize("concurrency", [1, 3])
def test_sheets_are_described_concurrently(tmp_path, concurrency):
    from videoauto.vision.captioner import Captioner
    p = SlowProvider()
    cap = Captioner(p, tmp_path, cells_per_sheet=1, concurrency=concurrency)
    done = []
    t0 = time.monotonic()
    for j in jobs(tmp_path, 6):
        cap.add(j)
        cap.flush(force=False, on_sheet_done=lambda: done.append(1))
    cap.flush(force=True, on_sheet_done=lambda: done.append(1))
    secs = time.monotonic() - t0
    cap.close()
    assert len(cap.results) == 6 and all(v is not None for v in cap.results.values())
    assert cap.stats.calls == 6 and done
    if concurrency == 1:
        assert p.peak == 1 and secs >= 6 * 0.3
    else:
        assert p.peak == 3 and secs < 6 * 0.3 * 0.6                    # 2 rounds of 3 instead of 6


def test_limit_reached_stops_and_keeps_finished_results(tmp_path):
    from videoauto.vision.captioner import Captioner
    p = SlowProvider(delay=0.2, fail_on=2)
    cap = Captioner(p, tmp_path, cells_per_sheet=1, concurrency=2)
    for j in jobs(tmp_path, 6):
        cap.add(j)
    with pytest.raises(LimitReached):
        cap.flush(force=True, on_sheet_done=lambda: None)
    cap.close()
    assert 1 <= len([v for v in cap.results.values() if v is not None]) < 6
    assert p.calls < 6                                                  # the rest was never sent
