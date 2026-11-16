from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import FakeProvider
from videoauto import schema
from videoauto.indexer import Indexer, collect


def click_track(path: Path, bpm: float, seconds: float, sr: int = 22050) -> None:
    """Short decaying 1 kHz clicks on every beat, written as WAV via ffmpeg."""
    y = np.zeros(int(seconds * sr), np.float32)
    click = (np.sin(2 * np.pi * 1000 * np.arange(int(0.03 * sr)) / sr)
             * np.exp(-np.arange(int(0.03 * sr)) / (0.006 * sr))).astype(np.float32)
    for t in np.arange(0.5, seconds - 0.1, 60.0 / bpm):
        i = int(t * sr)
        y[i:i + len(click)] += click[: len(y) - i]
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "f32le", "-ar", str(sr),
                    "-ac", "1", "-i", "-", str(path)], input=y.tobytes(), check=True)


def index_folder(cfg, root: Path, out: Path):
    provider = FakeProvider()
    ix = Indexer(cfg, out, provider=provider)
    rep = ix.run(collect(root, out))
    return provider, ix, rep


def load(rep):
    return [json.loads(p.read_text(encoding="utf-8")) for p in rep.written]


def test_click_track_bpm_and_beats(cfg, tmp_path):
    music = tmp_path / "proj" / "music"
    music.mkdir(parents=True)
    click_track(music / "click128.wav", 128, 12.0)
    provider, ix, rep = index_folder(cfg, tmp_path / "proj", tmp_path / "idx")

    assert provider.calls == 0 and rep.model_calls == 0 and ix.transcriber.loads == 0
    (m,) = load(rep)
    assert schema.errors(m) == [] and m["kind"] == "music"
    assert m["bpm"] == pytest.approx(128, abs=2)
    gaps = np.diff(m["beats"])
    assert np.median(gaps) == pytest.approx(60 / 128, abs=0.02)
    assert len(m["energy"]["values"]) == pytest.approx(12.0 / 0.1, abs=2)
    assert max(m["energy"]["values"]) == 1.0


def test_real_soundtrack_and_cache(cfg, tmp_path, sunset):
    music = tmp_path / "proj" / "music"
    music.mkdir(parents=True)
    shutil.copy(sunset, music / "sunset_track.mp4")     # a video file inside music/ is music
    provider, _, rep = index_folder(cfg, tmp_path / "proj", tmp_path / "idx")
    (m,) = load(rep)
    assert provider.calls == 0 and schema.errors(m) == []
    assert m["duration"] == pytest.approx(9.38, abs=0.05)
    assert m["bpm"] is None or 40 <= m["bpm"] <= 240

    provider2, _, rep2 = index_folder(cfg, tmp_path / "proj", tmp_path / "idx")
    assert rep2.cache_hits == 1 and rep2.written == [] and provider2.calls == 0


def test_same_bytes_as_video_and_music_is_not_a_wrong_cache_hit(cfg, tmp_path, sunset, caplog):
    proj = tmp_path / "proj"
    (proj / "music").mkdir(parents=True)
    shutil.copy(sunset, proj / "clip.mp4")
    shutil.copy(sunset, proj / "music" / "clip.mp4")      # identical bytes, other kind
    _, _, rep = index_folder(cfg, proj, tmp_path / "idx")
    assert len(rep.written) == 1 and rep.cache_hits == 0
    assert len(rep.failed) == 1 and "vừa là video vừa là nhạc" in rep.failed[0][1]
    assert json.loads(rep.written[0].read_text(encoding="utf-8"))["kind"] == "video"


def test_missing_librosa_fails_music_only(cfg, tmp_path, sunset, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "librosa", None)   # import raises ImportError
    proj = tmp_path / "proj"
    (proj / "music").mkdir(parents=True)
    click_track(proj / "music" / "c.wav", 120, 4.0)
    shutil.copy(sunset, proj / "v.mp4")
    _, _, rep = index_folder(cfg, proj, tmp_path / "idx")
    assert len(rep.failed) == 1 and "pip install -e .[music]" in rep.failed[0][1]
    assert [json.loads(p.read_text(encoding="utf-8"))["kind"] for p in rep.written] == ["video"]


def test_silence_has_no_bpm(cfg, tmp_path):
    music = tmp_path / "proj" / "music"
    music.mkdir(parents=True)
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=22050:cl=mono", "-t", "5", str(music / "silence.wav")], check=True)
    _, _, rep = index_folder(cfg, tmp_path / "proj", tmp_path / "idx")
    (m,) = load(rep)
    assert schema.errors(m) == [] and m["bpm"] is None and m["beats"] == []


def test_music_schema_invariants():
    good = {"schema_version": schema.SCHEMA_VERSION, "file": "a.wav", "sha256": "0" * 64, "kind": "music",
            "duration": 4.0, "bpm": 120.0, "beats": [0.5, 1.0, 1.5],
            "energy": {"hop_seconds": 0.1, "values": [0.0, 1.0]}}
    assert schema.errors(good) == []
    for bad in ({"beats": [1.0, 0.5]}, {"beats": [0.5, 9.0]}, {"bpm": None},
                {"energy": {"hop_seconds": 0.1, "values": [1.5]}}, {"shots": []}):
        assert schema.errors({**good, **bad}), bad
