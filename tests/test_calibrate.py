from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from videoauto import index as cli


@pytest.fixture
def clips(tmp_path, gym, sunset):
    d = tmp_path / "clips"
    d.mkdir()
    shutil.copy(gym, d / "gym.mp4")
    shutil.copy(sunset, d / "sunset.mp4")
    return d


@pytest.fixture
def no_models(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("calibration must not touch a model")
    monkeypatch.setattr("videoauto.indexer.make_provider", boom)
    monkeypatch.setattr("videoauto.speech.Transcriber.transcribe", boom)
    monkeypatch.setattr("videoauto.vision.claude_cli.ClaudeCLIProvider.describe", boom)


def read_tsv(out: Path) -> list[dict]:
    with open(out / "calibrate_blur.tsv", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_calibrate_default_threshold(clips, tmp_path, no_models, capsys):
    out = tmp_path / "idx"
    assert cli.main([str(clips), "--out", str(out), "--calibrate-blur"]) == 0
    printed = capsys.readouterr().out
    rows = read_tsv(out)
    assert {(r["file"], r["shot"]) for r in rows} == {("gym.mp4", "s01"), ("sunset.mp4", "s01")}
    assert all(r["blurry"] == "0" for r in rows)
    assert [float(r["sharpness"]) for r in rows] == sorted(float(r["sharpness"]) for r in rows)
    assert all(Path(r["keyframe"]).exists() for r in rows)
    assert list(out.glob("*.json")) == []                 # never writes an index
    assert "Không gọi model nào" in printed and "gym.mp4" in printed


def test_calibrate_flags_and_sheet(clips, tmp_path, no_models, monkeypatch, capsys):
    cfg = tmp_path / "videoauto.toml"
    cfg.write_text("[quality]\nblur_threshold = 100000\n", encoding="utf-8")
    out = tmp_path / "idx"
    assert cli.main([str(clips), "--out", str(out), "--calibrate-blur", "--config", str(cfg)]) == 0
    rows = read_tsv(out)
    assert all(r["blurry"] == "1" for r in rows)
    sheets = sorted(out.glob("calibrate_blur_flagged_*.jpg"))
    assert len(sheets) == 1 and sheets[0].stat().st_size > 1000
    assert "calibrate_blur_flagged_01.jpg" in capsys.readouterr().out
