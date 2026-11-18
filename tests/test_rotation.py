"""Clips with a rotation tag (stream side data). The indexer re-encodes such a clip once into
.index/normalized/<sha256>.mp4 (ffmpeg applies the tag itself), indexes that, and the builder
imports that file, so what OpenChatCut decodes never depends on it reading the tag."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import FakeProvider, make_clip
from test_builder import FakeOcc
from test_planner import CATALOGUE
from videoauto import normalize
from videoauto.cache import sha256_of
from videoauto.probe import probe


def gray_frame(path: Path, w: int, h: int) -> bytes:
    """First frame as 8-bit gray, decoded by ffmpeg with its default autorotation."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
                           "-f", "rawvideo", "-"], capture_output=True, check=True).stdout


@pytest.fixture(scope="session")
def rotated(tmp_path_factory) -> Path:
    """Stored 640x360 (landscape) with a 90° display rotation: a portrait clip as phones record it.
    Left third white, rest black, so the turn is visible in the pixels."""
    d = tmp_path_factory.mktemp("rot")
    flat = d / "flat.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=640x360:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                    "-vf", "drawbox=x=0:y=0:w=213:h=360:color=white:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(flat)], check=True)
    out = d / "PHONE_0001.MP4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(flat),
                    "-c", "copy", str(out)], check=True)
    return out


def test_fixture_really_carries_a_rotation_tag(rotated):
    pr = probe(rotated)
    assert pr.rotation in (90, -90, 270) and (pr.width, pr.height) == (360, 640)


def test_normalized_copy_is_stored_upright_with_no_tag(rotated, tmp_path):
    sha = sha256_of(rotated)
    out = normalize.ensure(rotated, sha, tmp_path, probe(rotated))
    assert out == tmp_path / "normalized" / f"{sha}.mp4"
    pr = probe(out)
    assert pr.rotation == 0 and (pr.width, pr.height) == (360, 640) and pr.has_audio
    # the stored pixels are what a player shows for the tagged file (ffmpeg applied the tag)
    assert gray_frame(out, 36, 64) == gray_frame(rotated, 36, 64) or \
        sum(abs(a - b) for a, b in zip(gray_frame(out, 36, 64), gray_frame(rotated, 36, 64))) < 36 * 64 * 4
    mtime = out.stat().st_mtime_ns
    assert normalize.ensure(rotated, sha, tmp_path, probe(rotated)) == out and out.stat().st_mtime_ns == mtime


def test_unrotated_clip_is_left_alone(tmp_path):
    clip = make_clip(tmp_path / "flat.mp4", 1.0)
    assert normalize.rotation(probe(clip)) == 0


def index_folder(folder: Path, out: Path, cfg, provider):
    from videoauto.indexer import Indexer, collect
    return Indexer(cfg, out, provider=provider).run(collect(folder, out))


def test_indexer_indexes_the_normalized_copy_and_says_so(rotated, tmp_path, cfg, caplog):
    folder = tmp_path / "footage"
    folder.mkdir()
    (folder / rotated.name).write_bytes(rotated.read_bytes())
    out = tmp_path / "index"
    caplog.set_level("INFO", logger="videoauto")
    rep = index_folder(folder, out, cfg, FakeProvider())
    sha = sha256_of(folder / rotated.name)
    ix = json.loads((out / f"{sha}.json").read_text(encoding="utf-8"))
    assert ix["file"] == rotated.name and ix["sha256"] == sha                  # identity: the original
    assert ix["normalized"]["file"] == f"normalized/{sha}.mp4" and ix["normalized"]["rotation"] in (90, -90, 270)
    assert (ix["width"], ix["height"], ix["orientation"]) == (360, 640, "vertical")
    assert (out / "normalized" / f"{sha}.mp4").is_file()
    assert rep.normalized == [rotated.name]
    assert "chuẩn hoá" in caplog.text


def test_an_index_made_before_this_fix_gets_its_normalized_copy_on_the_next_run(rotated, tmp_path, cfg):
    folder = tmp_path / "footage"
    folder.mkdir()
    (folder / rotated.name).write_bytes(rotated.read_bytes())
    out = tmp_path / "index"
    index_folder(folder, out, cfg, FakeProvider())
    sha = sha256_of(folder / rotated.name)
    f = out / f"{sha}.json"
    old = json.loads(f.read_text(encoding="utf-8"))
    del old["normalized"]                                     # what an older videoauto wrote
    f.write_text(json.dumps(old), encoding="utf-8")
    (out / "normalized" / f"{sha}.mp4").unlink()
    rep = index_folder(folder, out, cfg, FakeProvider())
    assert rep.cache_hits == 1 and rep.model_calls == 0       # no new captions for it
    assert json.loads(f.read_text(encoding="utf-8"))["normalized"]["file"] == f"normalized/{sha}.mp4"
    assert (out / "normalized" / f"{sha}.mp4").is_file() and rep.normalized == [rotated.name]


def test_plan_sources_carry_the_normalized_file(rotated, tmp_path, cfg):
    from videoauto.planner.planner import _base_plan, load_material
    from videoauto.brief import Brief
    folder = tmp_path / "footage"
    folder.mkdir()
    (folder / rotated.name).write_bytes(rotated.read_bytes())
    out = tmp_path / "index"
    index_folder(folder, out, cfg, FakeProvider())
    m = load_material(out, folder)
    src = _base_plan(Brief(15, "9:16", 30, "tiktok", ""), m, 0)["sources"][rotated.name]
    sha = sha256_of(folder / rotated.name)
    assert src["sha256"] == sha and Path(src["normalized"]) == (out / "normalized" / f"{sha}.mp4").resolve()


def test_builder_imports_the_normalized_file_not_the_tagged_original(rotated, tmp_path):
    from videoauto.brief import load_project_config
    from videoauto.builder.builder import Builder
    footage = tmp_path / "footage"
    footage.mkdir()
    (footage / rotated.name).write_bytes(rotated.read_bytes())
    sha = sha256_of(footage / rotated.name)
    idx = tmp_path / "index"
    norm = normalize.ensure(footage / rotated.name, sha, idx, probe(footage / rotated.name))
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        f"footage_dir: {json.dumps(str(footage))}\ncatalogue: catalogue.json\n"
        "openchatcut:\n  repo: nowhere\n  data_dir: occdata\n", encoding="utf-8")
    pcfg = load_project_config(tmp_path / "config.yaml")
    plan = {"version": 1, "target": {"duration": 1.0, "aspect": "9:16", "fps": 30}, "confidence": 1,
            "tier": "basic", "clips": [{"src": rotated.name, "in": 0, "out": 1.0, "track": "V1", "effects": []}],
            "transitions": [], "fades": {"in": 0, "out": 0}, "script": "", "warnings": [],
            "sources": {rotated.name: {"sha256": sha, "duration": 2.0, "orientation": "vertical",
                                       "normalized": str(norm)}}}
    occ = FakeOcc(footage)
    rep = Builder(pcfg, occ).build(plan, tmp_path / "out.mp4")
    assert rep.error is None, rep.error
    imp = [r for r in occ.history if r.label.startswith("normalized import")]
    assert len(imp) == 1 and imp[0].tool_args == {"path": str(norm)}
    clip = next(r for r in occ.history if r.label.startswith("clip 1"))
    assert clip.tool_args["adds"][0]["assetId"] == "logo-asset"      # FakeOcc's import_asset id
    assert not any(r.label.startswith("fill clip") for r in occ.history)   # 360x640 into 9:16: no crop
    # a deleted normalized copy is made again from the original before the build
    norm.unlink()
    rep = Builder(pcfg, FakeOcc(footage)).build(plan, tmp_path / "out2.mp4")
    assert rep.error is None and norm.is_file()


OCC = Path(__file__).resolve().parents[1].parent / "OpenChatCut"


@pytest.mark.skipif(not (OCC / "node_modules" / "tsx").exists(), reason="OpenChatCut not installed next to videoauto")
@pytest.mark.parametrize("use_copy", [True, False])
def test_real_render_is_upright(rotated, tmp_path, use_copy, real_catalogue):
    """Through the real OpenChatCut: the render shows what a player shows for the tagged clip.
    Measured 2026-11-18 with the pinned OpenChatCut: the tagged original (use_copy=False) also
    comes out upright, for this H.264 fixture and for a tagged copy of a Sony RX10M3 4K clip, so
    the copy is a safeguard for decoders that skip the tag, not the cure for the sideways video
    (whose clips carried no tag at all; README, Troubleshooting)."""
    from videoauto.brief import load_project_config
    from videoauto.builder.builder import Builder
    from conftest import REPO
    footage = tmp_path / "footage"
    footage.mkdir()
    (footage / rotated.name).write_bytes(rotated.read_bytes())
    sha = sha256_of(footage / rotated.name)
    src = {"sha256": sha, "duration": 2.0, "orientation": "vertical"}
    if use_copy:
        src["normalized"] = str(normalize.ensure(footage / rotated.name, sha, tmp_path / "index"))
    (tmp_path / "config.yaml").write_text(
        f"footage_dir: {json.dumps(str(footage))}\ncatalogue: {json.dumps(str(REPO / 'catalogue.json'))}\n"
        f"openchatcut:\n  repo: {json.dumps(str(OCC))}\n  data_dir: {json.dumps(str(tmp_path / 'occ'))}\n",
        encoding="utf-8")
    pcfg = load_project_config(tmp_path / "config.yaml")
    real_catalogue.ensure(pcfg.catalogue, pcfg.occ.repo, pcfg.occ.node)
    plan = {"version": 1, "target": {"duration": 1.0, "aspect": "9:16", "fps": 30}, "confidence": 1,
            "tier": "basic", "clips": [{"src": rotated.name, "in": 0, "out": 1.0, "track": "V1", "effects": []}],
            "transitions": [], "fades": {"in": 0, "out": 0}, "script": "", "warnings": [],
            "sources": {rotated.name: src}}
    out = tmp_path / "out.mp4"
    rep = Builder(pcfg).build(plan, out)
    assert rep.error is None, rep.error
    want, got = gray_frame(rotated, 9, 16), gray_frame(out, 9, 16)
    diff = sum(abs(a - b) for a, b in zip(want, got)) / len(want)
    assert diff < 20, f"render differs from the upright picture (mean diff {diff:.0f})"
