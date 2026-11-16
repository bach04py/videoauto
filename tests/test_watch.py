"""M5 watcher: interactive setup through stdin, then the watch loop with FakeProvider/FakeOcc."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from conftest import FakeProvider
from test_builder import FakeOcc
from test_planner import GOOD, write_config
from videoauto import run as run_mod
from videoauto import cache, watch
from videoauto.config import load_config
from videoauto.indexer import Indexer, collect


class TTY(io.StringIO):
    """stdin that claims to be a terminal, so the watcher asks its questions."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(scope="module")
def cached_index(tmp_path_factory, gym, sunset):
    """Index both fixtures once; each test copies it, so watch runs cost only the plan call."""
    root = tmp_path_factory.mktemp("watchidx")
    media = root / "media"
    media.mkdir()
    shutil.copy(gym, media / "gym.mp4")
    shutil.copy(sunset, media / "sunset.mp4")
    idx = root / "idx"
    Indexer(load_config(), idx, provider=FakeProvider()).run(collect(media, idx))
    return idx


@pytest.fixture
def ws(tmp_path, monkeypatch, gym, sunset):
    """A working directory with config.yaml + catalogue, and the three folders."""
    footage = tmp_path / "footage"
    footage.mkdir()
    shutil.copy(gym, footage / "gym.mp4")
    shutil.copy(sunset, footage / "sunset.mp4")
    scripts = tmp_path / "kich-ban"
    scripts.mkdir()
    out = tmp_path / "xuat" / "video"
    write_config(tmp_path, footage)
    monkeypatch.chdir(tmp_path)
    return {"root": tmp_path, "footage": footage, "scripts": scripts, "output": out,
            "work": out}                               # work folders live inside the output folder


def stdin(monkeypatch, text: str, tty: bool = True):
    monkeypatch.setattr(sys, "stdin", (TTY if tty else io.StringIO)(text))


def stopped() -> threading.Event:
    e = threading.Event()
    e.set()
    return e


def setup_only(argv=(), provider=None):
    """Run main through prerequisites and setup; the loop exits at once (stop already set)."""
    return watch.main(list(argv), provider=provider or FakeProvider(), occ=FakeOcc(Path(".")),
                      stop=stopped())


# ---------------------------------------------------------------- setup

def test_first_run_asks_all_three_and_saves_on_yes(ws, monkeypatch, capsys):
    stdin(monkeypatch, f"{ws['footage']}\n{ws['scripts']}\n{ws['output']}\n\n")
    assert setup_only() == 0
    out = capsys.readouterr().out
    for i, label in enumerate(("Thư mục video nguồn", "Thư mục kịch bản", "Thư mục xuất video"), 1):
        assert f"{i}. {label}: " in out                     # no [default] on a first run
    assert "Lưu lại cho lần sau? [Y/n]" in out
    saved = yaml.safe_load((ws["root"] / "videoauto.local.yaml").read_text(encoding="utf-8"))
    assert saved == {"footage_dir": str(ws["footage"]), "scripts_dir": str(ws["scripts"]),
                     "output_dir": str(ws["output"])}
    assert ws["output"].is_dir()                             # created
    for d in ("processing", "done", "failed", ".index", ".logs"):
        assert (ws["work"] / d).is_dir()

    # second run: the saved values are offered and Enter accepts each of them
    stdin(monkeypatch, "\n\n\n\n")
    assert setup_only() == 0
    out = capsys.readouterr().out
    assert f"1. Thư mục video nguồn [{ws['footage']}]: " in out
    assert f"2. Thư mục kịch bản [{ws['scripts']}]: " in out
    assert f"3. Thư mục xuất video [{ws['output']}]: " in out
    assert yaml.safe_load((ws["root"] / "videoauto.local.yaml").read_text(encoding="utf-8")) == saved

    # --paths prints them; --reset forgets them
    assert watch.main(["--paths"]) == 0
    assert str(ws["footage"]) in capsys.readouterr().out
    stdin(monkeypatch, "", tty=False)
    assert setup_only(["--reset"]) == 2                       # forgotten, and no terminal to ask in
    assert not (ws["root"] / "videoauto.local.yaml").exists()


def test_typing_a_new_path_replaces_the_saved_one_and_no_keeps_file(ws, monkeypatch, capsys):
    stdin(monkeypatch, f"{ws['footage']}\n{ws['scripts']}\n{ws['output']}\ny\n")
    assert setup_only() == 0
    other = ws["root"] / "kich-ban-2"
    other.mkdir()
    stdin(monkeypatch, f"\n{other}\n\nn\n")
    assert setup_only() == 0
    assert "Không lưu" in capsys.readouterr().out
    saved = yaml.safe_load((ws["root"] / "videoauto.local.yaml").read_text(encoding="utf-8"))
    assert saved["scripts_dir"] == str(ws["scripts"])          # answered no: file unchanged


def test_bad_paths_are_reasked_and_quotes_and_trailing_backslash_stripped(ws, monkeypatch, capsys):
    empty = ws["root"] / "trong"
    empty.mkdir()
    (empty / "ghi-chu.txt").write_text("không phải video", encoding="utf-8")
    new_scripts = ws["root"] / "kich-ban-moi"
    answers = [
        str(ws["root"] / "khong-co"),                  # does not exist
        str(empty),                                    # exists, no video
        "",                                            # nothing typed, no default
        f'"{ws["footage"]}\\"',                        # drag-and-drop quotes + trailing backslash
        str(ws["footage"] / "sub"),                    # scripts inside footage
        str(ws["footage"]),                            # scripts == footage
        f"'{new_scripts}\\'",                          # missing: offered, then created
        "y",
        str(ws["footage"]),                            # output == footage
        f'"{ws["output"]}\\"',
        "n",
    ]
    stdin(monkeypatch, "\n".join(answers) + "\n")
    assert setup_only() == 0
    out = capsys.readouterr().out
    assert "không tồn tại" in out
    assert "không có tệp video nào" in out
    assert "Chưa nhập đường dẫn" in out
    assert "không được nằm trong thư mục video nguồn" in out
    assert "phải khác thư mục video nguồn" in out
    assert "Tạo mới?" in out and new_scripts.is_dir()
    assert "Thư mục xuất video phải khác" in out
    assert f"Thư mục video nguồn:     {ws['footage']}\n" in out       # summary: no quotes, no "\"
    assert f"Thư mục kịch bản:        {new_scripts}\n" in out
    assert "Traceback" not in out


def test_clean_path():
    assert watch.clean_path('  "D:\\quay\\buoi-tap\\"  ') == "D:\\quay\\buoi-tap"
    assert watch.clean_path("'D:\\quay\\'") == "D:\\quay"
    assert watch.clean_path("D:\\") == "D:" + os.sep            # a drive root keeps its separator
    assert watch.clean_path('""') == ""


def test_no_prompt_with_nothing_saved_exits_2(ws, monkeypatch, capsys):
    stdin(monkeypatch, "")
    assert setup_only(["--no-prompt"]) == 2
    err = capsys.readouterr().err
    assert "Thư mục video nguồn" in err and "python -m videoauto.watch" in err and "Traceback" not in err


def test_stdin_not_a_terminal_behaves_as_no_prompt(ws, monkeypatch, capsys):
    (ws["root"] / "videoauto.local.yaml").write_text(yaml.safe_dump({
        "footage_dir": str(ws["footage"]), "scripts_dir": str(ws["root"] / "mat-roi"),
        "output_dir": str(ws["output"])}), encoding="utf-8")
    stdin(monkeypatch, "would be an answer\n", tty=False)
    assert setup_only() == 2
    err = capsys.readouterr().err
    assert "Thư mục kịch bản" in err and "mat-roi" in err and "python -m videoauto.watch" in err
    assert "1. Thư mục" not in capsys.readouterr().out          # nothing was asked


def test_missing_prerequisite_exits_before_any_question(ws, monkeypatch, capsys):
    (ws["root"] / "catalogue.json").unlink()
    stdin(monkeypatch, f"{ws['footage']}\n{ws['scripts']}\n{ws['output']}\n\n")
    assert setup_only() == 2
    cap = capsys.readouterr()
    assert "catalogue" in cap.err and "Thư mục video nguồn" not in cap.out


# ---------------------------------------------------------------- watching

class Rejecting(FakeProvider):
    """Plans normally, but rejects any script that contains TỪ CHỐI."""

    def complete(self, prompt: str) -> str:
        if "TỪ CHỐI" in prompt:
            self.calls += 1
            return json.dumps({"reject": "Không có cảnh phù hợp với kịch bản này."}, ensure_ascii=False)
        return super().complete(prompt)


class Running:
    """main() in a background thread with saved paths and --no-prompt."""

    def __init__(self, ws, cached_index, provider=None, settle=0.3):
        work = ws["work"]
        (work / ".index").parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cached_index, work / ".index", dirs_exist_ok=True)
        (ws["root"] / "videoauto.local.yaml").write_text(yaml.safe_dump({
            "footage_dir": str(ws["footage"]), "scripts_dir": str(ws["scripts"]),
            "output_dir": str(ws["output"])}), encoding="utf-8")
        self.ws, self.stop = ws, threading.Event()
        self.provider = provider or FakeProvider()
        self.provider.reply = GOOD
        self.occ = FakeOcc(ws["footage"])
        self.code = None
        self.settle = settle

    def __enter__(self):
        def target():
            self.code = watch.main(["--no-prompt"], provider=self.provider, occ=self.occ,
                                   stop=self.stop, settle=self.settle, poll=0.05)
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        until(lambda: (self.ws["work"] / "processing").is_dir())
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=30)
        assert not self.thread.is_alive()

    def builds(self) -> int:
        return sum(1 for r in self.occ.history if r.argv[:2] == ["project", "new"])


def until(cond, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def runs_log(ws) -> list[str]:
    f = ws["work"] / ".logs" / "runs.log"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def test_new_script_triggers_exactly_one_build(ws, cached_index, capsys):
    with Running(ws, cached_index) as r:
        (ws["scripts"] / "tap-nguc.txt").write_text("0-5s: cận cảnh tập ngực\n", encoding="utf-8")
        (ws["scripts"] / "ghi-chu.md").write_text("không phải kịch bản", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "tap-nguc.txt").exists())
        time.sleep(1.0)                                       # nothing else may happen
        assert r.builds() == 1
    assert r.code == 0
    out = ws["output"]
    assert (out / "tap-nguc.mp4").exists()
    assert (out / "tap-nguc.mp4.build.json").exists()
    plan = json.loads((out / "tap-nguc.plan.json").read_text(encoding="utf-8"))
    assert plan["script_source"] == "user"
    assert r.provider.calls == 1                             # footage was cached: only the plan call
    assert (ws["scripts"] / "ghi-chu.md").exists()           # ignored
    assert list((ws["work"] / "processing").iterdir()) == []
    lines = runs_log(ws)
    assert len(lines) == 1 and "tap-nguc.txt | OK |" in lines[0] and "1 lượt gọi model" in lines[0]
    assert any((ws["work"] / ".logs").glob("*-tap-nguc.log"))
    stdout = capsys.readouterr().out
    assert "Thấy kịch bản mới: tap-nguc.txt" in stdout and "XONG tap-nguc.txt" in stdout


def test_file_still_being_written_is_not_picked_up_early(ws, cached_index):
    script = ws["scripts"] / "dang-viet.txt"
    with Running(ws, cached_index, settle=0.8) as r:
        lines = [f"{i}-{i + 1}s: cảnh số {i}\n" for i in range(12)]
        for line in lines:                                   # an editor saving bit by bit
            with open(script, "a", encoding="utf-8") as f:
                f.write(line)
            time.sleep(0.2)
            assert script.exists(), "picked up while still being written"
            assert r.builds() == 0
        until(lambda: (ws["work"] / "done" / "dang-viet.txt").exists())
        assert r.builds() == 1
    assert (ws["work"] / "done" / "dang-viet.txt").read_text(encoding="utf-8") == "".join(lines)


def test_two_scripts_run_one_after_the_other(ws, cached_index, monkeypatch):
    active, peak, order = [0], [0], []
    real = run_mod.run_pipeline

    def tracked(*a, **kw):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        order.append(Path(a[0].script_file).name)
        try:
            time.sleep(0.5)                                  # long enough to overlap if it could
            return real(*a, **kw)
        finally:
            active[0] -= 1

    monkeypatch.setattr(run_mod, "run_pipeline", tracked)
    with Running(ws, cached_index) as r:
        for name in ("a.txt", "b.txt"):
            (ws["scripts"] / name).write_text(f"kịch bản {name}\n", encoding="utf-8")
        until(lambda: all((ws["work"] / "done" / n).exists() for n in ("a.txt", "b.txt")))
        assert r.builds() == 2
    assert peak[0] == 1
    assert sorted(order) == ["a.txt", "b.txt"]
    assert r.provider.calls == 2                             # one plan each; zero caption calls
    assert len(runs_log(ws)) == 2


def test_failing_script_lands_in_failed_and_watcher_survives(ws, cached_index, capsys):
    with Running(ws, cached_index, provider=Rejecting()) as r:
        (ws["scripts"] / "hong.txt").write_text("TỪ CHỐI: cảnh dưới nước\n", encoding="utf-8")
        until(lambda: (ws["work"] / "failed" / "hong.loi.txt").exists())
        (ws["scripts"] / "trong.txt").write_text("", encoding="utf-8")
        until(lambda: (ws["work"] / "failed" / "trong.loi.txt").exists())
        (ws["scripts"] / "tot.txt").write_text("cảnh tập gym\n", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "tot.txt").exists())
    assert r.code == 0
    failed = ws["work"] / "failed"
    assert (failed / "hong.txt").exists()
    reason = (failed / "hong.loi.txt").read_text(encoding="utf-8")
    assert "Bước lỗi: lập kế hoạch dựng" in reason
    assert "Không có cảnh phù hợp với kịch bản này." in reason and "Cách xử lý:" in reason
    assert "Kịch bản trống" in (failed / "trong.loi.txt").read_text(encoding="utf-8")
    assert not (ws["output"] / "hong.mp4").exists()
    assert (ws["output"] / "tot.mp4").exists()
    out = capsys.readouterr().out
    assert "!! LỖI" in out and "hong.txt" in out and "Không có cảnh phù hợp" in out
    assert "Traceback" not in out
    lines = runs_log(ws)
    assert [l.split(" | ")[2] for l in lines] == ["LỖI (lập kế hoạch dựng)", "LỖI (đọc kịch bản)", "OK"]


def test_script_left_in_processing_is_recovered_at_startup(ws, cached_index, capsys):
    proc = ws["work"] / "processing"
    proc.mkdir(parents=True)
    (proc / "do-dang.txt").write_text("cảnh tập gym\n", encoding="utf-8")
    with Running(ws, cached_index):
        until(lambda: (ws["work"] / "done" / "do-dang.txt").exists())
    assert "Khôi phục do-dang.txt" in capsys.readouterr().out
    assert not (ws["work"] / ".logs" / "recovered.txt").exists()


def test_script_interrupted_twice_goes_to_failed(ws, cached_index):
    proc = ws["work"] / "processing"
    proc.mkdir(parents=True)
    (proc / "lan-hai.txt").write_text("cảnh tập gym\n", encoding="utf-8")
    (ws["work"] / ".logs").mkdir()
    (ws["work"] / ".logs" / "recovered.txt").write_text("lan-hai.txt\n", encoding="utf-8")
    with Running(ws, cached_index) as r:
        until(lambda: (ws["work"] / "failed" / "lan-hai.loi.txt").exists())
        time.sleep(0.5)
        assert r.builds() == 0
    assert "gián đoạn hai lần" in (ws["work"] / "failed" / "lan-hai.loi.txt").read_text(encoding="utf-8")
    assert list(proc.iterdir()) == []


def test_ctrl_c_during_a_build_leaves_nothing_in_processing(ws, cached_index, monkeypatch, capsys):
    def interrupted(pcfg, *a, **kw):
        Path(a[-1]).write_bytes(b"half a video")           # the render had started
        (ws["work"] / ".index" / "x.json.tmp").write_text("{", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(run_mod, "run_pipeline", interrupted)
    with Running(ws, cached_index) as r:
        (ws["scripts"] / "dung.txt").write_text("cảnh tập gym\n", encoding="utf-8")
        until(lambda: not r.thread.is_alive())
    assert r.code == 0
    assert (ws["scripts"] / "dung.txt").exists()               # back where it came from
    assert list((ws["work"] / "processing").iterdir()) == []
    assert not (ws["output"] / "dung.mp4").exists()
    assert not list((ws["work"] / ".index").glob("*.tmp"))
    assert "BỎ DỞ" in runs_log(ws)[0]
    assert "Đã bỏ dở dung.txt" in capsys.readouterr().out


def test_same_script_name_twice_never_overwrites_the_earlier_video(ws, cached_index, capsys):
    out = ws["output"]
    with Running(ws, cached_index) as r:
        (ws["scripts"] / "tap-nguc.txt").write_text("lần một\n", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "tap-nguc.txt").exists())
        first = {x: (out / f"tap-nguc{x}").stat().st_mtime_ns for x in watch.OUTPUT_SUFFIXES}
        (ws["scripts"] / "tap-nguc.txt").write_text("lần hai\n", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "tap-nguc-2.txt").exists())
        assert r.builds() == 2
    for x in watch.OUTPUT_SUFFIXES:
        assert (out / f"tap-nguc{x}").stat().st_mtime_ns == first[x]       # untouched
        assert (out / f"tap-nguc-2{x}").exists()                             # its own set
    plan2 = json.loads((out / "tap-nguc-2.plan.json").read_text(encoding="utf-8"))
    assert plan2["script_source"] == "user"
    assert (ws["work"] / "done" / "tap-nguc-2.txt").read_text(encoding="utf-8") == "lần hai\n"
    assert "video mới đặt tên tap-nguc-2.mp4" in capsys.readouterr().out
    assert [line.split(" | ")[1:3] for line in runs_log(ws)] == [["tap-nguc.txt", "OK"]] * 2


def test_unchanged_library_is_not_hashed_again(ws, cached_index, monkeypatch):
    hashed: list[str] = []
    real = cache._hash_file

    def counting(path):
        hashed.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(cache, "_hash_file", counting)
    with Running(ws, cached_index):
        (ws["scripts"] / "a.txt").write_text("một\n", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "a.txt").exists())
        assert sorted(hashed) == ["gym.mp4", "sunset.mp4"]     # once each, shared by index/plan/build
        hashed.clear()
        (ws["scripts"] / "b.txt").write_text("hai\n", encoding="utf-8")
        until(lambda: (ws["work"] / "done" / "b.txt").exists())
        assert hashed == []                                     # second run: no hash calls at all

    # a restart: the memo comes back from .index/hashes.memo, still no hash calls
    monkeypatch.setattr(cache, "_memo", {})
    rep = Indexer(load_config(), ws["work"] / ".index", provider=FakeProvider()).run(
        collect(ws["footage"], ws["work"] / ".index"))
    assert hashed == [] and rep.cache_hits == 2

    # a file whose mtime changed is hashed again; same content, so still a cache hit
    gym = ws["footage"] / "gym.mp4"
    st = gym.stat()
    os.utime(gym, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    rep = Indexer(load_config(), ws["work"] / ".index", provider=FakeProvider()).run(
        collect(ws["footage"], ws["work"] / ".index"))
    assert hashed == ["gym.mp4"] and rep.cache_hits == 2 and rep.model_calls == 0

    # a changed file (new size) is re-indexed: the sha256 is still the identity check
    before = cache.sha256_of(gym)
    hashed.clear()
    with open(gym, "ab") as f:
        f.write(b"\0" * 16)
    assert cache.sha256_of(gym) != before and hashed == ["gym.mp4"]
