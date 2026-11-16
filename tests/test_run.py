from __future__ import annotations

import json
import logging
import shutil

import pytest

from conftest import FakeProvider
from test_builder import FakeOcc
from test_planner import GOOD, write_config
from videoauto import run as run_cli
from videoauto.vision.base import limit_error


@pytest.fixture
def folder(tmp_path, gym, sunset):
    media = tmp_path / "clips"
    media.mkdir()
    shutil.copy(gym, media / "gym.mp4")
    shutil.copy(sunset, media / "sunset.mp4")
    return media


def go(tmp_path, folder, provider, occ=None, config=None):
    cfg = config or write_config(tmp_path, tmp_path / "elsewhere")       # run's folder overrides media_dir
    out = tmp_path / "output"
    code = run_cli.main([str(folder), "--out", str(out), "--config", str(cfg)],
                        provider=provider, occ=occ or FakeOcc(folder))
    return code, out


def test_one_command_folder_to_mp4(tmp_path, folder, caplog):
    caplog.set_level(logging.INFO, logger="videoauto")
    p = FakeProvider()
    p.reply = GOOD
    code, out = go(tmp_path, folder, p)
    assert code == 0
    assert (out / "final.mp4").exists() and (out / "final.mp4.build.json").exists()
    assert json.loads((out / "plan.json").read_text(encoding="utf-8"))["tier"] == "full"
    assert len(list((out / "index").glob("*.json"))) == 2
    assert p.calls == 2                                       # 1 caption sheet + 1 plan
    text = caplog.text
    assert "[1/3]" in text and "[2/3]" in text and "[3/3]" in text and "XONG" in text


def test_preflight_failure_spends_nothing(tmp_path, folder, capsys):
    cfg = write_config(tmp_path, folder)
    (tmp_path / "catalogue.json").unlink()
    p = FakeProvider()
    code, out = go(tmp_path, folder, p, config=cfg)
    err = capsys.readouterr().err
    assert code == 2 and "catalogue" in err and "Traceback" not in err
    assert p.calls == 0 and not (out / "index").exists()


def test_rejected_plan_stops_before_build(tmp_path, folder, capsys):
    p = FakeProvider()
    p.reply = {"reject": "Cảnh quay không đủ cho brief này."}
    occ = FakeOcc(folder)
    code, out = go(tmp_path, folder, p, occ=occ)
    assert code == 5 and "Cảnh quay không đủ cho brief này." in capsys.readouterr().err
    assert occ.history == [] and not (out / "final.mp4").exists()


def test_limit_during_index_stops_cleanly(tmp_path, folder, capsys):
    p = FakeProvider({1: limit_error("Claude", None)})
    code, out = go(tmp_path, folder, p)
    assert code == 3 and "giới hạn" in capsys.readouterr().err
    assert not (out / "plan.json").exists()
