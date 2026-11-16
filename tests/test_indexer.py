from __future__ import annotations

import copy
import json
import logging
import shutil
from pathlib import Path

import pytest

from conftest import FakeProvider
from videoauto import index as cli
from videoauto import schema, tools
from videoauto.cache import Cache
from videoauto.errors import IndexValidationError, LimitReached, ProviderUnavailable
from videoauto.indexer import Indexer, collect
from videoauto.vision.base import classify, limit_error


def run(cfg, target: Path, out: Path, provider, **kw):
    ix = Indexer(cfg, out, provider=provider, **kw)
    return ix, ix.run(collect(target, out))


# ---------------------------------------------------------------- real-video pipeline

@pytest.fixture(scope="module")
def gym_index(gym, tmp_path_factory):
    from videoauto.config import load_config
    out = tmp_path_factory.mktemp("gym")
    _, rep = run(load_config(), gym, out, FakeProvider())
    assert len(rep.written) == 1
    return json.loads(rep.written[0].read_text(encoding="utf-8"))


def test_gym_index_is_valid_and_sensible(gym_index):
    assert schema.errors(gym_index) == []
    assert gym_index["kind"] == "video" and gym_index["has_speech"] is False
    assert gym_index["audio"]["has_music"] is None
    s01 = gym_index["shots"][0]
    assert "black" in s01["flags"] and s01["end"] == pytest.approx(1.5, abs=0.1)   # title card
    assert all("silent" in s["flags"] for s in gym_index["shots"])                 # silent track
    assert all(s["camera"] == "static" for s in gym_index["shots"])


# ---------------------------------------------------------------- 1. cache hit: zero calls

def test_cache_hit_makes_no_model_calls(cfg, sunset, tmp_path, caplog):
    first = FakeProvider()
    _, rep1 = run(cfg, sunset, tmp_path, first)
    assert first.calls == 1 and len(rep1.written) == 1

    second = FakeProvider()
    caplog.set_level(logging.INFO, logger="videoauto")
    ix, rep2 = run(cfg, sunset, tmp_path, second)
    assert second.calls == 0 and rep2.model_calls == 0
    assert ix.transcriber.loads == 0
    assert rep2.cache_hits == 1 and rep2.written == []
    assert any("cache: TRÚNG" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- 2. schema catches malformed

MUTATIONS = {
    "missing field": lambda ix: ix.pop("fps"),
    "motion out of range": lambda ix: ix["shots"][0].update(motion=1.5),
    "unknown camera": lambda ix: ix["shots"][0].update(camera="drone"),
    "null caption without flag": lambda ix: ix["shots"][0].update(caption=None),
    "bad sha": lambda ix: ix.update(sha256="xyz"),
    "extra key": lambda ix: ix.update(surprise=True),
    "quality counts wrong": lambda ix: ix["quality"].update(rejected=7),
    "shots not contiguous": lambda ix: ix["shots"][-1].update(start=ix["shots"][-1]["start"] + 0.5),
    "has_speech lies": lambda ix: ix.update(has_speech=True),
    "wrong schema_version": lambda ix: ix.update(schema_version=2),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_schema_rejects_malformed_index(gym_index, tmp_path, name):
    bad = copy.deepcopy(gym_index)
    MUTATIONS[name](bad)
    assert schema.errors(bad), f"{name} was not caught"
    cache = Cache(tmp_path)
    with pytest.raises(IndexValidationError):
        cache.put(bad)
    assert list(tmp_path.glob("*.json*")) == []        # nothing written, not even a temp file


def test_corrupt_cache_entry_is_not_a_hit(cfg, sunset, tmp_path):
    _, rep = run(cfg, sunset, tmp_path, FakeProvider())
    p = rep.written[0]
    p.write_text(p.read_text(encoding="utf-8")[:-40], encoding="utf-8")   # truncated JSON
    again = FakeProvider()
    _, rep2 = run(cfg, sunset, tmp_path, again)
    assert rep2.cache_hits == 0 and again.calls == 1
    assert schema.errors(json.loads(p.read_text(encoding="utf-8"))) == []


# ---------------------------------------------------------------- 3. rate limit stops cleanly

@pytest.fixture
def two_videos(tmp_path, sunset, gym):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(sunset, src / "a_sunset.mp4")   # processed first (sorted)
    shutil.copy(gym, src / "b_gym.mp4")
    return src


def test_rate_limit_stops_cleanly(cfg, two_videos, tmp_path):
    out = tmp_path / "idx"
    # 2 cells per sheet: call 1 = sunset's shot (finishes sunset), call 2 = gym's first shot.
    provider = FakeProvider({2: limit_error("Claude", "khoảng 15:00 ngày 16/11")})
    _, rep = run(cfg, two_videos, out, provider, cells_per_sheet=2)

    assert isinstance(rep.stopped, LimitReached)
    assert provider.calls == 2
    written = sorted(out.glob("*.json"))
    assert len(written) == 1
    done = json.loads(written[0].read_text(encoding="utf-8"))
    assert done["file"] == "a_sunset.mp4" and schema.errors(done) == []
    assert list(out.glob("*.tmp")) == []
    assert "giới hạn" in rep.stopped.message and "15:00" in rep.stopped.message

    # Retry later: the finished file is a cache hit, only the unfinished one is processed.
    retry = FakeProvider()
    _, rep2 = run(cfg, two_videos, out, retry, cells_per_sheet=2)
    assert rep2.stopped is None and rep2.cache_hits == 1 and len(rep2.written) == 1
    assert len(list(out.glob("*.json"))) == 2


def test_rate_limit_cli_message(cfg, two_videos, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("videoauto.indexer.make_provider",
                        lambda _cfg: FakeProvider({1: limit_error("Claude", None)}))
    code = cli.main([str(two_videos), "--out", str(tmp_path / "idx")])
    err = capsys.readouterr().err
    assert code == 3
    assert "Dừng: Đã chạm giới hạn" in err and "Traceback" not in err
    assert list((tmp_path / "idx").glob("*.json")) == []   # first sheet never finished


# ---------------------------------------------------------------- malformed replies

def test_malformed_reply_retried_twice_then_null_caption(cfg, sunset, tmp_path):
    provider = FakeProvider({1: "not json", 2: "[{\"cell\": 99}]", 3: "```json\n[]\n```"})
    _, rep = run(cfg, sunset, tmp_path, provider)
    assert provider.calls == 3 and rep.shots_per_call == [1, 1, 1]
    ix = json.loads(rep.written[0].read_text(encoding="utf-8"))
    shot = ix["shots"][0]
    assert shot["caption"] is None and "caption_failed" in shot["flags"]
    assert any("không tạo được mô tả" in n for n in ix["quality"]["notes"])


def test_malformed_then_good_reply_recovers(cfg, sunset, tmp_path):
    provider = FakeProvider({1: "Sorry, here you go: {oops"})
    _, rep = run(cfg, sunset, tmp_path, provider)
    shot = json.loads(rep.written[0].read_text(encoding="utf-8"))["shots"][0]
    assert provider.calls == 2 and shot["caption"] and "caption_failed" not in shot["flags"]


# ---------------------------------------------------------------- user-facing errors

@pytest.mark.parametrize("text,kind", [
    ("Claude AI usage limit reached|1763300000", LimitReached),
    ("5-hour limit reached ∙ resets 3pm", LimitReached),
    ("API Error: 429 rate_limit_error", LimitReached),
    ("Invalid API key · Please run /login", ProviderUnavailable),
])
def test_error_classification(text, kind):
    assert isinstance(classify("Claude", text), kind)


def test_missing_ffmpeg_message(monkeypatch, capsys, sunset, tmp_path):
    tools.which.cache_clear()
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    try:
        code = cli.main([str(sunset), "--out", str(tmp_path)])
    finally:
        tools.which.cache_clear()
    err = capsys.readouterr().err
    assert code == 2 and "Không tìm thấy ffmpeg" in err and "Traceback" not in err


def test_unreadable_file_message(caplog, capsys, tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"this is not a video")
    caplog.set_level(logging.INFO, logger="videoauto")
    code = cli.main([str(bad), "--out", str(tmp_path / "idx")])
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert code == 1 and "Không đọc được tệp 'broken.mp4' (Invalid data" in msgs
    assert str(tmp_path) not in msgs and "Traceback" not in capsys.readouterr().err
