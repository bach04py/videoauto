"""python -m videoauto.setup: every step on its own (with fake tools), the whole run on a clean
machine, and a real 1-second render through the OpenChatCut next to this checkout (skipped when
it is not installed)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO
from test_builder import FakeOcc
from test_planner import CATALOGUE
from videoauto import setup as su


def proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


# ---------------------------------------------------------------- 1. tools

def fake_tools(present: dict[str, str], logged_in=True):
    """present: tool -> version line. which() finds only those; `run` answers --version."""
    def which(name):
        return f"/bin/{name}" if name in present else None

    def run(cmd, **kw):
        name = Path(cmd[0]).name
        if name == "claude" and cmd[1:] == ["auth", "status"]:
            return proc(0, json.dumps({"loggedIn": logged_in}))
        return proc(0, present[name] + "\n") if name in present else proc(1)
    return which, run


def test_every_missing_tool_is_listed_at_once_with_its_install_command(monkeypatch, tmp_path):
    monkeypatch.setattr(su, "node_candidates", lambda which: [])
    monkeypatch.setattr(su, "_os", lambda: "windows")
    which, run = fake_tools({"ffprobe": "ffprobe version 9"})
    with pytest.raises(su.SetupFailed) as e:
        su.check_tools(tmp_path, "claude", run, which)
    m = e.value.message
    assert m.startswith("Thiếu 4 công cụ")
    for cmd in ("winget install Schniz.fnm", "winget install Gyan.FFmpeg", "winget install Git.Git",
                "irm https://claude.ai/install.ps1 | iex"):
        assert cmd in m, cmd
    assert "terminal MỚI" in m


def test_node_of_another_major_version_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(su, "node_candidates", lambda which: ["/bin/node"])
    (tmp_path / ".nvmrc").write_text("24.22.1\n")
    which, run = fake_tools({"node": "v26.13.1", "ffmpeg": "ffmpeg 9", "ffprobe": "ffprobe 9",
                             "git": "git version 2.50", "claude": "2.1 (Claude Code)"})
    with pytest.raises(su.SetupFailed) as e:
        su.check_tools(tmp_path, "claude", run, which)
    assert "cần Node 24 (>= 24.22.1), máy đang có v26.13.1" in e.value.message
    assert su.node_problem("v24.22.3") is None and su.node_problem("v24.1.0")


def test_node_24_from_fnm_is_used_when_path_has_a_newer_one(monkeypatch, tmp_path):
    """The machine this was found on: node 26 on PATH (or fnm's newest), fnm also has 24.22.1."""
    monkeypatch.setenv("FNM_DIR", str(tmp_path / "fnm"))
    exe = "node.exe" if sys.platform == "win32" else "bin/node"
    for v in ("v24.9.0", "v24.22.1", "v26.13.1"):
        f = tmp_path / "fnm" / "node-versions" / v / "installation" / exe
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")

    def run(cmd, **kw):
        if cmd[0] == "/usr/bin/node":
            return proc(0, "v26.13.1\n")
        return proc(0, Path(cmd[0]).parents[1 if sys.platform == "win32" else 2].name + "\n")
    found = su.node_candidates(lambda n: "/usr/bin/node")
    assert [Path(f).parents[1 if sys.platform == "win32" else 2].name for f in found[1:]] == \
        ["v26.13.1", "v24.22.1", "v24.9.0"]                     # numeric order, not text order
    node, v = su.find_node("24.22.1", run, which=lambda n: "/usr/bin/node")
    assert v == "v24.22.1" and "v24.22.1" in node


def test_claude_logged_out_is_reported_with_the_login_command(monkeypatch, tmp_path):
    monkeypatch.setattr(su, "node_candidates", lambda which: ["/bin/node"])
    which, run = fake_tools({"node": "v24.22.1", "ffmpeg": "ffmpeg 9", "ffprobe": "ffprobe 9",
                             "git": "git version 2.50", "claude": "2.1 (Claude Code)"}, logged_in=False)
    with pytest.raises(su.SetupFailed) as e:
        su.check_tools(tmp_path, "claude", run, which)
    assert "claude auth login" in e.value.message and "Thiếu 1 công cụ" in e.value.message
    # another vision provider: claude is not needed at all
    which, run = fake_tools({"node": "v24.22.1", "ffmpeg": "ffmpeg 9", "ffprobe": "ffprobe 9", "git": "git 2"})
    assert su.check_tools(tmp_path, "openai-compatible", run, which).node == "/bin/node"


# ---------------------------------------------------------------- 2. the pinned OpenChatCut

def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def fork(tmp_path):
    """A stand-in for the fork: branch videoauto with two commits; returns (url, first, second)."""
    src = tmp_path / "fork"
    src.mkdir()
    git("init", "-q", "-b", "videoauto", cwd=src)
    git("config", "user.email", "t@example.invalid", cwd=src)
    git("config", "user.name", "t", cwd=src)
    (src / "a.txt").write_text("1")
    git("add", ".", cwd=src)
    git("commit", "-q", "-m", "one", cwd=src)
    first = git("rev-parse", "HEAD", cwd=src)
    (src / "a.txt").write_text("2")
    git("commit", "-q", "-am", "two", cwd=src)
    second = git("rev-parse", "HEAD", cwd=src)
    return src.as_uri(), first, second


def test_pin_is_written_from_the_local_checkout_and_read_back(tmp_path, fork):
    url, first, second = fork
    repo = Path(url.removeprefix("file:///"))
    pin_file = tmp_path / "openchatcut.pin"
    pin = su.write_pin(repo, pin_file)
    assert pin.commit == second and pin.branch == "videoauto"
    assert su.read_pin(pin_file) == pin
    (repo / "a.txt").write_text("dirty")
    with pytest.raises(su.SetupFailed, match="chưa commit"):
        su.write_pin(repo, pin_file)


def test_the_repo_pin_names_the_fork_and_a_full_commit():
    pin = su.read_pin()
    assert pin.url == "https://github.com/bach04py/OpenChatCut" and pin.branch == "videoauto"
    assert len(pin.commit) == 40


def test_absent_engine_is_cloned_at_the_pinned_commit(tmp_path, fork):
    url, first, second = fork
    repo = tmp_path / "OpenChatCut"
    su.ensure_engine(repo, su.Pin(url, "videoauto", first), interactive=False)
    assert git("rev-parse", "HEAD", cwd=repo) == first
    su.ensure_engine(repo, su.Pin(url, "videoauto", first), interactive=False)      # again: nothing to do


def test_engine_at_another_commit_stops_without_prompt_and_switches_on_yes(tmp_path, fork):
    url, first, second = fork
    repo = tmp_path / "OpenChatCut"
    subprocess.run(["git", "clone", "-q", "--branch", "videoauto", url, str(repo)], check=True)
    pin = su.Pin(url, "videoauto", first)
    with pytest.raises(su.SetupFailed) as e:
        su.ensure_engine(repo, pin, interactive=False)
    assert first[:10] in e.value.message and second[:10] in e.value.message
    assert f"checkout --detach {first}" in e.value.message
    with pytest.raises(su.SetupFailed, match="Không chuyển"):
        su.ensure_engine(repo, pin, interactive=True, ask=lambda _: "n")
    assert git("rev-parse", "HEAD", cwd=repo) == second
    su.ensure_engine(repo, pin, interactive=True, ask=lambda _: "y")
    assert git("rev-parse", "HEAD", cwd=repo) == first


def test_a_folder_that_is_not_a_clone_is_not_touched(tmp_path, fork):
    repo = tmp_path / "OpenChatCut"
    repo.mkdir()
    (repo / "mine.txt").write_text("x")
    with pytest.raises(su.SetupFailed, match="không phải một git clone"):
        su.ensure_engine(repo, su.Pin(fork[0], "videoauto", fork[1]), interactive=True, ask=lambda _: "y")
    assert (repo / "mine.txt").read_text() == "x"


# ---------------------------------------------------------------- 3. npm ci and blocked install scripts

class FakeNpm:
    """npm ci / rebuild / node -e probes. `blocked`: packages whose install script never runs."""

    def __init__(self, repo: Path, blocked=()):
        self.repo, self.blocked, self.calls = repo, set(blocked), []

    def __call__(self, cmd, cwd=None, env=None, timeout=None):
        self.calls.append(cmd)
        name = Path(cmd[0]).name
        if name.startswith("npm") and cmd[1] == "ci":
            nm = self.repo / "node_modules"
            shutil.rmtree(nm, ignore_errors=True)
            (nm / "ffmpeg-static").mkdir(parents=True)
            (nm / "esbuild").mkdir()
            if "ffmpeg-static" not in self.blocked:
                (nm / "ffmpeg-static" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")).write_bytes(b"x")
            warn = ("npm warn install-scripts 2 packages have install scripts not yet covered by allowScripts:"
                    if self.blocked else "")
            return proc(0, "added 847 packages\n", warn)
        if name.startswith("npm") and cmd[1] == "rebuild":
            return proc(0, "rebuilt dependencies successfully")
        if cmd[1:2] == ["--version"]:
            return proc(0, "v24.22.1\n")
        if cmd[1] == "-e":
            return proc(1, "", "Error: The package \"@esbuild/win32-x64\" could not be found") \
                if "esbuild" in self.blocked else proc(0, "esbuild-ok")
        if "--import" in cmd:
            return proc(1, "", "esbuild missing") if "esbuild" in self.blocked else proc(0, "Usage: occ")
        raise AssertionError(cmd)


@pytest.fixture
def engine(tmp_path):
    repo = tmp_path / "OpenChatCut"
    repo.mkdir()
    (repo / "package.json").write_text("{}")
    (repo / "package-lock.json").write_text("{}")
    node = tmp_path / "node" / "node.exe"
    node.parent.mkdir()
    node.write_bytes(b"")
    (node.parent / ("npm.cmd" if sys.platform == "win32" else "npm")).write_bytes(b"")
    return repo, str(node), su.Pin("u", "videoauto", "0" * 40)


def test_blocked_install_scripts_are_found_and_the_approve_command_is_printed(engine):
    repo, node, pin = engine
    npm = FakeNpm(repo, blocked={"ffmpeg-static", "esbuild"})
    with pytest.raises(su.SetupFailed) as e:
        su.ensure_npm(repo, node, pin, npm)
    m = e.value.message
    assert "ffmpeg-static: thiếu ffmpeg" in m and "esbuild: không chạy được" in m
    assert f'npm --prefix "{repo}" install-scripts approve ffmpeg-static esbuild' in m
    assert any(c[1] == "rebuild" for c in npm.calls)          # an approval given since then is used
    assert not (repo / "node_modules" / su.NPM_STAMP).exists()


def test_npm_ci_runs_once_and_is_skipped_while_the_lock_is_unchanged(engine):
    repo, node, pin = engine
    npm = FakeNpm(repo)
    su.ensure_npm(repo, node, pin, npm)
    assert sum(c[1] == "ci" for c in npm.calls) == 1
    su.ensure_npm(repo, node, pin, npm)
    assert sum(c[1] == "ci" for c in npm.calls) == 1
    (repo / "package-lock.json").write_text('{"changed": 1}')
    su.ensure_npm(repo, node, pin, npm)
    assert sum(c[1] == "ci" for c in npm.calls) == 2


def test_install_scripts_ran_on_ci_but_the_binary_is_gone_is_still_caught(engine):
    repo, node, pin = engine
    npm = FakeNpm(repo)
    su.ensure_npm(repo, node, pin, npm)
    for f in (repo / "node_modules" / "ffmpeg-static").iterdir():
        f.unlink()
    with pytest.raises(su.SetupFailed, match="approve ffmpeg-static"):
        su.ensure_npm(repo, node, pin, npm)


# ---------------------------------------------------------------- the whole run, clean machine

@pytest.fixture
def machine(clean_machine, monkeypatch, tmp_path):
    """Clean clone; steps 1-3 stubbed (tested above); a vi_ok catalogue unless a test removes it."""
    monkeypatch.setattr(su, "ROOT", clean_machine)
    monkeypatch.setattr(su, "check_tools", lambda *a, **k: su.Tools(node="node", versions={}))
    monkeypatch.setattr(su, "read_pin", lambda *a: su.Pin("u", "videoauto", "0" * 40))
    monkeypatch.setattr(su, "ensure_engine", lambda *a, **k: None)
    monkeypatch.setattr(su, "ensure_npm", lambda *a, **k: None)
    folders = {k: tmp_path / k for k in ("footage", "scripts", "output")}
    folders["footage"].mkdir()
    shutil.copy(su.SAMPLE, folders["footage"] / "clip.mp4")
    return clean_machine, folders


def args_for(folders):
    return ["--no-prompt", "--footage", str(folders["footage"]), "--scripts", str(folders["scripts"]),
            "--output", str(folders["output"])]


def test_clean_machine_setup_ends_with_a_real_file_and_only_then_says_done(machine, capsys):
    root, folders = machine
    (root / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")       # vi_ok present
    out_dir = folders["output"]
    occ = FakeOcc(out_dir / ".setup-check" / "footage")
    assert su.main(args_for(folders), occ=occ) == 0
    out = capsys.readouterr().out
    assert "Cài đặt xong" in out and (out_dir / su.CHECK_NAME).stat().st_size > 0
    assert (root / "brand").is_dir() and "bỏ dấu # ở đầu dòng" in out
    assert (root / "videoauto.local.yaml").exists()
    labels = [r.label for r in occ.history]
    assert "fill clip 1" in labels and labels[-1] == "render"                # cropped, then rendered
    plan = json.loads((out_dir / ".setup-check" / "plan.json").read_text(encoding="utf-8"))
    assert plan["target"]["aspect"] == "9:16" and plan["overlays"][0]["fields"]
    assert su.CHECK_TEXT in plan["overlays"][0]["fields"].values()
    for n in range(1, 8):
        assert f"[{n}/7]" in out


def test_no_render_no_done(machine, capsys):
    root, folders = machine
    (root / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    occ = FakeOcc(folders["output"] / ".setup-check" / "footage", fail=("render",))
    assert su.main(args_for(folders), occ=occ) == su.EXIT_FAIL
    cap = capsys.readouterr()
    assert "Cài đặt xong" not in cap.out and "Chưa cài đặt xong" in cap.err and "Dựng thử thất bại" in cap.err


def test_clean_catalogue_without_vi_ok_stops_at_the_render_step_and_says_why(machine, capsys):
    root, folders = machine                       # clean_machine's catalogue: no vi_ok anywhere
    occ = FakeOcc(folders["output"] / ".setup-check" / "footage")
    assert su.main(args_for(folders), occ=occ) == su.EXIT_FAIL
    cap = capsys.readouterr()
    assert "Không có template chữ nào dùng được" in cap.err and "Cài đặt xong" not in cap.out
    assert not any(r.label == "render" for r in occ.history)


def test_no_prompt_without_saved_or_given_folders_names_the_flags(machine, capsys):
    root, folders = machine
    (root / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    assert su.main(["--no-prompt"]) == su.EXIT_FAIL
    assert "--footage <thư mục> --scripts <thư mục> --output <thư mục>" in capsys.readouterr().err


def test_interactive_setup_asks_the_three_folders(machine, monkeypatch, capsys):
    root, folders = machine
    (root / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    answers = iter([str(folders["footage"]), str(folders["scripts"]), "y", str(folders["output"])])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    occ = FakeOcc(folders["output"] / ".setup-check" / "footage")
    assert su.main([], occ=occ, ask=lambda p: "y") == 0
    from videoauto import watch
    assert watch.load_saved()["output"] == str(folders["output"])


# ---------------------------------------------------------------- the real thing

OCC = REPO.parent / "OpenChatCut"


@pytest.mark.skipif(not (OCC / "node_modules" / "tsx").exists(), reason="needs OpenChatCut next to videoauto: run python -m videoauto.setup first")
def test_real_render_of_the_check_video(tmp_path, real_catalogue):
    """Through the real OpenChatCut: a 1 s MP4, 1080x1920, no black bars left and right."""
    from videoauto.brief import load_project_config
    from videoauto.probe import probe
    cfg = tmp_path / "config.yaml"
    cfg.write_text((REPO / "config.yaml").read_text(encoding="utf-8")
                   .replace("repo: ../OpenChatCut", f"repo: {json.dumps(str(OCC))}")
                   .replace("data_dir: .occ-data", f"data_dir: {json.dumps(str(tmp_path / 'occ'))}")
                   .replace("catalogue: catalogue.json", f"catalogue: {json.dumps(str(REPO / 'catalogue.json'))}"),
                   encoding="utf-8")
    pcfg = load_project_config(cfg, problems=[])
    real_catalogue.ensure(pcfg.catalogue, pcfg.occ.repo, pcfg.occ.node)
    out = su.render_check(pcfg, tmp_path / "out")
    meta = probe(out)
    assert (meta.width, meta.height) == (1080, 1920) and abs(meta.duration - 1.0) < 0.15
    for x in (0, 1040):                    # the 40 px strips at the left and right edges
        edge = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                               "-vf", f"crop=40:1920:{x}:0,format=gray", "-f", "rawvideo", "-"],
                              capture_output=True, check=True).stdout
        mean = sum(edge) / len(edge)
        assert mean > 30, f"edge at x={x} is black (mean {mean:.0f}): the clip was not cropped to fill"
