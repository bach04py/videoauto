"""Group 3: a clean machine runs. Questions before checks, every problem in one report, a sample
config.yaml that loads as committed, and speech recognition that can never stop a build."""

from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from conftest import REPO, FakeProvider, make_clip
from videoauto import run as run_cli
from videoauto import speech, watch
from videoauto.brief import load_project_config
from videoauto.errors import UserError
from videoauto.planner.catalogue import Catalogue


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def stopped() -> threading.Event:
    e = threading.Event()
    e.set()
    return e


# ---------------------------------------------------------------- the committed config.yaml

def test_committed_config_loads_as_is(clean_machine):
    """No brand/logo.png in the repo: the sample config must not point at it (item 8)."""
    cfg = load_project_config(clean_machine / "config.yaml")
    assert cfg.brand.logo is None and cfg.brand.name
    assert cfg.whisper == {"model": "off"}                     # speech recognition off by default


def test_sample_values_marked_change_are_detected(clean_machine):
    found = watch.changed_sample_lines(clean_machine / "config.yaml")
    assert any(f.startswith("footage_dir:") for f in found) and any(f.startswith("repo:") for f in found)
    assert watch.first_setup(None).startswith("chưa có videoauto.local.yaml")


# ---------------------------------------------------------------- the watcher on a clean machine

def test_clean_machine_watcher_asks_first_then_reports_everything(clean_machine, synth_media, tmp_path,
                                                                 monkeypatch, capsys):
    """Fresh clone, committed config.yaml, no local file: the three questions appear, the answers
    are saved, and only then the check runs and lists every problem in one message."""
    scripts, out = tmp_path / "kb", tmp_path / "xuat"
    monkeypatch.setattr(sys, "stdin", TTY(f"{synth_media}\n{scripts}\n\n{out}\n\n"))
    code = watch.main([], provider=FakeProvider(), stop=stopped())
    cap = capsys.readouterr()
    assert code == 2
    assert "Thiết lập lần đầu" in cap.out and "1. Thư mục video nguồn" in cap.out
    assert (clean_machine / "videoauto.local.yaml").exists()
    # ../OpenChatCut next to this temporary clone does not exist: reported, not hidden
    assert "openchatcut.repo" in cap.err and "OpenChatCut" in cap.err


def test_problems_are_reported_together(tmp_path, monkeypatch, capsys):
    """Item 7: missing logo, footage folder, script and catalogue used to take four runs."""
    (tmp_path / "config.yaml").write_text(
        "footage_dir: khong-co-video\nscript_file: khong-co.txt\ncatalogue: khong-co.json\n"
        "brand:\n  name: X\n  logo: brand/khong-co.png\nopenchatcut:\n  repo: khong-co-occ\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    p = FakeProvider()
    assert run_cli.main(["--out", str(tmp_path / "out")], provider=p) == 2
    err = capsys.readouterr().err
    for needle in ("logo", "Thư mục video", "kịch bản", "openchatcut.repo", "catalogue.json"):
        assert needle in err, needle
    assert "Còn " in err and "việc cần sửa" in err and p.calls == 0


def test_single_problem_keeps_its_plain_message(tmp_path, monkeypatch, capsys, synth_media):
    from test_builder import FakeOcc
    from test_planner import CATALOGUE
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    (tmp_path / "config.yaml").write_text("catalogue: catalogue.json\nopenchatcut:\n  repo: x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert run_cli.main([str(synth_media), "--script", "nope.txt"], provider=FakeProvider(),
                        occ=FakeOcc(synth_media)) == 2
    err = capsys.readouterr().err
    assert "Không tìm thấy tệp kịch bản 'nope.txt'" in err and "việc cần sửa" not in err


def test_catalogue_message_matches_the_new_behaviour(tmp_path):
    with pytest.raises(UserError) as e:
        Catalogue.load(tmp_path / "catalogue.json")
    assert "python -m videoauto.setup" in e.value.message and "Chép" not in e.value.message


# ---------------------------------------------------------------- speech recognition

def test_whisper_is_off_by_default_and_never_loads():
    from videoauto.config import load_config
    t = speech.Transcriber(load_config()["whisper"])
    assert not t.enabled and t.transcribe(np.ones(16000, np.float32)) is None and t.loads == 0


@pytest.mark.parametrize("raw,want", [(False, "off"), ("off", "off"), (None, "off"), ("Small", "small")])
def test_model_setting(raw, want):
    assert speech.model_setting(raw) == want


def test_bad_whisper_model_is_a_config_error(tmp_path):
    (tmp_path / "config.yaml").write_text("whisper:\n  model: huge\n", encoding="utf-8")
    with pytest.raises(UserError, match="large-v3 \\(3,1 GB\\)"):
        load_project_config(tmp_path / "config.yaml")


def test_compute_type_is_auto_and_missing_cuda_libs_fall_back_to_cpu(monkeypatch):
    monkeypatch.setattr(speech, "cuda_device_count", lambda: 1)
    monkeypatch.setattr(speech, "cuda_libs_missing", lambda: ["cublas64_12.dll"])
    assert speech.pick_model("auto") == ("medium", "cpu", "auto")
    monkeypatch.setattr(speech, "cuda_libs_missing", lambda: [])
    assert speech.pick_model("auto") == ("large-v3", "cuda", "auto")
    monkeypatch.setattr(speech, "cuda_device_count", lambda: (_ for _ in ()).throw(RuntimeError("driver")))
    assert speech.pick_model("small") == ("small", "cpu", "auto")


def test_cuda_lib_probe():
    def loader(name):
        raise OSError(name)
    assert speech.cuda_libs_missing("win32", loader) == ["cublas64_12.dll"]
    assert speech.cuda_libs_missing("win32", lambda n: None) == []
    assert speech.cuda_libs_missing("darwin", loader) == []


@pytest.mark.parametrize("error", [ValueError("Requested float16 compute type, but the target device "
                                              "or backend do not support efficient float16 computation."),
                                   RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")])
def test_any_speech_error_gives_null_and_disables(monkeypatch, error):
    t = speech.Transcriber({"model": "small", "min_speech_seconds": 0.5})
    monkeypatch.setattr(speech, "speech_seconds", lambda a: 5.0)
    calls = []

    def get(device=None):
        calls.append(device)
        raise error
    monkeypatch.setattr(t, "_get", get)
    assert t.transcribe(np.ones(16000, np.float32)) is None
    assert t.failed and t.transcribe(np.ones(16000, np.float32)) is None
    assert len(calls) == (2 if isinstance(error, RuntimeError) else 1)     # CUDA error: CPU tried once


def test_indexing_survives_a_speech_failure(tmp_path, monkeypatch):
    """The reported case: cublas64_12.dll missing killed the whole build. Now the clip is indexed
    with transcript: null."""
    from videoauto.config import load_config
    from videoauto.indexer import Indexer, collect
    media = tmp_path / "media"
    make_clip(media / "talk.mp4", 3, audio=True)
    cfg = load_config()
    cfg["whisper"]["model"] = "small"
    monkeypatch.setattr(speech, "speech_seconds", lambda a: 2.0)
    monkeypatch.setattr(speech.Transcriber, "_get",
                        lambda self, device=None: (_ for _ in ()).throw(RuntimeError("cublas64_12.dll")))
    rep = Indexer(cfg, tmp_path / "idx", provider=FakeProvider()).run(collect(media, tmp_path / "idx"))
    assert len(rep.written) == 1 and not rep.failed
    ix = json.loads(rep.written[0].read_text(encoding="utf-8"))
    assert ix["has_speech"] is False and all(s["transcript"] is None for s in ix["shots"])


def test_config_yaml_whisper_wins_over_toml():
    merged = speech.merge_whisper({"whisper": {"model": "auto", "language": None}, "x": 1}, {"model": "off"})
    assert merged["whisper"] == {"model": "off", "language": None} and merged["x"] == 1
