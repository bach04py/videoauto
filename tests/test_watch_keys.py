"""Watcher keys: S edits the style (a running build keeps its config), P shows paths, Q quits.
No video needed: the Watcher is built around a config only."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from test_planner import CATALOGUE, LUT
from test_style_edit import COMMENTED
from videoauto import watch
from videoauto.brief import load_project_config


@pytest.fixture
def w(tmp_path):
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    conf = tmp_path / "config.yaml"
    conf.write_text(COMMENTED.replace("../OpenChatCut", "nowhere"), encoding="utf-8")
    paths = {k: tmp_path / k for k in ("footage", "scripts", "output")}
    for p in paths.values():
        p.mkdir()
    env = watch.Env(load_project_config(conf), {}, None, None)
    wt = watch.Watcher(paths, env)
    wt.keys_on = True
    return wt


def answers(*items, during=None):
    """An input() stand-in; `during(i)` runs before answer i (to simulate the build printing)."""
    it = iter(items)
    n = [0]

    def ask(prompt):
        print(prompt, flush=True)
        if during:
            during(n[0])
        n[0] += 1
        return next(it)
    return ask


def test_s_while_idle_saves_and_next_build_sees_it(w, capsys):
    old = w.env.pcfg
    ok = w.edit_style(answers("Phòng tập mới.", ".", "tươi mát, thư giãn", "calm", LUT["assetId"], ""))
    assert ok
    assert w.env.pcfg is not old                                   # a new object for the next build
    assert w.env.pcfg.style.tone == "tươi mát, thư giãn" and w.env.pcfg.style.pace == "calm"
    assert w.env.pcfg.brand.context == "Phòng tập mới."
    assert old.style.tone == ""                                    # the old one is never mutated
    out = capsys.readouterr().out
    assert "Lưu vào config.yaml? [Y/n]" in out and "Đã lưu vào" in out
    assert watch.HINT in out                                       # hint again once the editor closes
    text = w.env.pcfg.path.read_text(encoding="utf-8")
    assert "# CHANGE: folder with the raw clips" in text


def test_s_answer_no_leaves_config_untouched(w, capsys):
    before = w.env.pcfg.path.read_bytes()
    assert not w.edit_style(answers("", "êm", "", "", "n"))
    assert w.env.pcfg.path.read_bytes() == before
    assert "Không lưu" in capsys.readouterr().out


def test_s_during_a_build_warns_and_can_be_declined(w, capsys):
    w.current = Path("tap-nguc.txt")
    before = w.env.pcfg
    assert not w.edit_style(answers("n"))
    out = capsys.readouterr().out
    assert "Đang dựng tap-nguc.txt" in out and "chỉ áp dụng từ kịch bản sau" in out
    assert "Tiếp tục sửa? [Y/n]" in out
    assert w.env.pcfg is before
    assert watch.HINT not in out                                    # no idle hint while building


def test_s_during_a_build_never_changes_the_running_builds_config(w):
    """The build holds its own reference (watch.Watcher.build: pcfg = self.env.pcfg)."""
    w.current = Path("tap-nguc.txt")
    running = w.env.pcfg
    assert w.edit_style(answers("", "", "mạnh mẽ", "fast", "", ""))
    assert running.style.tone == "" and running.style.pace == ""
    assert w.env.pcfg.style.pace == "fast"


def test_build_lines_wait_while_the_editor_is_open(w, capsys):
    def build_prints(i):
        if i == 1:
            watch.say("  Dựng video bằng OpenChatCut…")       # from the build, mid-question
    w.current = Path("x.txt")
    w.edit_style(answers("y", "", "", "", "", during=build_prints))
    out = capsys.readouterr().out
    assert out.index("4. prefer_luts") < out.index("Dựng video bằng OpenChatCut")


def test_changed_on_disk_asks_before_overwriting(w, capsys):
    path = w.env.pcfg.path

    def someone_saves(i):
        if i == 4:                                              # after the last field, before Y/n
            path.write_text(path.read_text(encoding="utf-8").replace("duration: 15", "duration: 20"),
                            encoding="utf-8")
    assert not w.edit_style(answers("", "êm", "", "", "", "", during=someone_saves))   # Y, then default N
    out = capsys.readouterr().out
    assert "đã bị sửa ở nơi khác" in out and "[y/N]" in out
    assert "duration: 20" in path.read_text(encoding="utf-8") and "êm" not in path.read_text(encoding="utf-8")

    assert w.edit_style(answers("", "êm", "", "", "", "y", during=someone_saves))
    text = path.read_text(encoding="utf-8")
    assert "duration: 20" in text and "êm" in text                # both edits kept


def test_invalid_config_is_reported_and_not_saved(w, capsys):
    path = w.env.pcfg.path
    path.write_text(path.read_text(encoding="utf-8").replace('aspect: "9:16"', 'aspect: "2:1"'),
                    encoding="utf-8")
    before = path.read_bytes()
    assert not w.edit_style(answers("", "êm", "", "", ""))
    assert path.read_bytes() == before
    assert "brief.aspect" in capsys.readouterr().out


def test_eof_cancels_the_editor(w, capsys):
    def ask(prompt):
        raise EOFError
    assert not w.edit_style(ask)
    assert "Đã huỷ" in capsys.readouterr().out
    assert not w.editing.is_set()


def test_p_shows_paths(w, capsys):
    w.on_key("P", None)
    out = capsys.readouterr().out
    assert str(w.scripts) in out and "config.yaml" in out


def test_q_idle_stops_at_once(w):
    w._stop = threading.Event()
    w.on_key("q", None)
    assert w._stop.is_set()


def test_q_during_build_waits_for_it(w, capsys):
    w._stop = threading.Event()
    w.current = Path("tap-nguc.txt")
    w.on_key("q", answers("n"))
    assert not w._stop.is_set()
    w.on_key("q", answers(""))
    assert w._stop.is_set()
    assert "Sẽ thoát sau khi dựng xong tap-nguc.txt" in capsys.readouterr().out


def test_no_build_starts_while_editing(w):
    w.prepare()
    (w.scripts / "a.txt").write_text("x", encoding="utf-8")
    w.settle = 0
    w.editing.set()
    built = []
    w.build = lambda p: built.append(p)
    stop = threading.Event()
    t = threading.Thread(target=w.loop, args=(stop,))
    t.start()
    time.sleep(0.6)
    assert built == []
    w.editing.clear()
    deadline = time.monotonic() + 5
    while not built and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(5)
    assert [p.name for p in built] == ["a.txt"]


class Keys:
    def __init__(self, *keys):
        self.keys = list(keys)

    def poll(self):
        return self.keys.pop(0) if self.keys else None


def test_listener_hands_keys_over_and_survives_a_broken_reader(w, capsys):
    stop = threading.Event()
    w._stop = stop
    listener = watch.KeyListener(w, Keys("p", "x", "q"), stop, ask=None, tick=0.01)
    listener.start()
    listener.join(5)
    assert stop.is_set() and "Đang dùng:" in capsys.readouterr().out

    class Broken:
        def poll(self):
            raise OSError("no console")
    stop2 = threading.Event()
    listener = watch.KeyListener(w, Broken(), stop2, tick=0.01)
    listener.start()
    listener.join(5)
    assert not listener.is_alive() and not stop2.is_set() and w.keys_on is False


def test_start_keys_unavailable_platform_keeps_watching(w, monkeypatch, capsys):
    from videoauto import keys
    monkeypatch.setattr(keys, "console_keys", lambda: None)
    w.keys_on = False
    watch._start_keys(w, threading.Event(), None, None)
    assert "không dùng được" in capsys.readouterr().out and w.keys_on is False
    monkeypatch.setattr(keys, "console_keys", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    watch._start_keys(w, threading.Event(), None, None)              # never crashes
    assert w.keys_on is False


def test_msvcrt_reader_drops_arrow_keys(monkeypatch):
    from videoauto import keys
    seq = ["\xe0", "H", "s"]

    class M:
        @staticmethod
        def kbhit():
            return bool(seq)

        @staticmethod
        def getwch():
            return seq.pop(0)
    r = keys.MsvcrtKeys.__new__(keys.MsvcrtKeys)
    r._m = M
    assert r.poll() is None and r.poll() == "s" and r.poll() is None


class SpyKeys:
    def __init__(self):
        self.polled = 0

    def poll(self):
        self.polled += 1
        return None


@pytest.mark.parametrize("argv,tty,listens", [([], True, True), (["--no-prompt"], True, False),
                                              ([], False, False)])
def test_listener_only_with_a_console(w, monkeypatch, capsys, argv, tty, listens):
    """A scheduled task (--no-prompt, or stdin that is not a console) gets no keyboard listener."""
    monkeypatch.setattr(watch, "check_prerequisites", lambda *a: w.env)
    monkeypatch.setattr(watch, "setup_paths", lambda no_prompt: {"footage": w.footage, "scripts": w.scripts,
                                                                 "output": w.output})
    monkeypatch.setattr(watch, "_stdin_is_tty", lambda: tty)
    spy, stop = SpyKeys(), threading.Event()
    threading.Timer(0.5, stop.set).start()
    assert watch.main(argv, stop=stop, keys=spy, poll=0.05) == 0
    out = capsys.readouterr().out
    assert (spy.polled > 0) is listens
    assert (watch.HINT in out) is listens
