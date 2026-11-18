"""python -m videoauto.setup: a new machine, from `git clone` to a rendered video.

On a machine with Node 24, Python 3.12, ffmpeg, git and a logged-in Claude Code:

    git clone https://github.com/bach04py/videoauto
    cd videoauto
    pip install -e .
    python -m videoauto.setup

Steps, each printed, each safe to run again:
  1. tools: node, python, ffmpeg, git, claude (+ logged in), with versions; everything missing is
     listed at once with the install command for this OS, then it stops.
  2. the video engine (OpenChatCut): the fork and commit pinned in openchatcut.pin, cloned next to
     videoauto when absent; a clone at another commit is reported (and switched only when the user
     says yes). The pin is not optional: a catalogue from another OpenChatCut version makes Remotion
     die with exit 1 in readFile.
  3. npm ci in it, then a real check that what install scripts fetch is there (ffmpeg-static's
     binary, esbuild running). A machine whose policy blocks install scripts gets the exact
     `npm install-scripts approve <pkg>` commands.
  4. catalogue.json, with the Vietnamese-safe (vi_ok) template counts.
  5. brand/ for the logo.
  6. the three folders (footage, scripts, output) into videoauto.local.yaml.
  7. a REAL 1-second render of the bundled sample clip, 16:9 into 9:16 (crop) with one TEXT line.
     "Cài đặt xong" is printed only when that MP4 exists and is right.

--no-prompt: no questions (folders from --footage/--scripts/--output or the saved ones; a clone at
the wrong commit stops instead of being switched). --pin: write openchatcut.pin from the
OpenChatCut checked out here (git rev-parse HEAD), for the maintainer.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import UserError

ROOT = Path(__file__).resolve().parent.parent            # the videoauto checkout
PIN_FILE = ROOT / "openchatcut.pin"
SAMPLE = Path(__file__).resolve().parent / "assets" / "setup-check-16x9.mp4"
CHECK_TEXT = "KIỂM TRA CÀI ĐẶT"                          # stacked marks (Ể, Ặ): the font fix is exercised
CHECK_NAME = "kiem-tra-cai-dat.mp4"
NPM_STAMP = ".videoauto-npm.json"                         # in node_modules: what `npm ci` was run for
STEPS = 7
EXIT_FAIL = 2

Run = Callable[..., subprocess.CompletedProcess]


class SetupFailed(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
         timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _tail(proc, n: int = 8) -> str:
    lines = [ln for ln in (proc.stderr + "\n" + proc.stdout).splitlines() if ln.strip()]
    return "\n      ".join(lines[-n:])


def step(n: int, title: str) -> None:
    print(f"\n[{n}/{STEPS}] {title}", flush=True)


def ok(msg: str) -> None:
    print(f"  OK  {msg}", flush=True)


def note(msg: str) -> None:
    print(f"      {msg}", flush=True)


# ---------------------------------------------------------------- 1. tools

def _os() -> str:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")


INSTALL = {
    "windows": {
        "node": "winget install Schniz.fnm   rồi   fnm install 24.22.1   rồi   fnm default 24.22.1",
        "python": "winget install Python.Python.3.12",
        "ffmpeg": "winget install Gyan.FFmpeg",
        "git": "winget install Git.Git",
        "claude": "irm https://claude.ai/install.ps1 | iex      (trong PowerShell)",
    },
    "mac": {
        "node": "brew install fnm && fnm install 24.22.1 && fnm default 24.22.1",
        "python": "brew install python@3.12",
        "ffmpeg": "brew install ffmpeg",
        "git": "brew install git",
        "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    },
    "linux": {
        "node": "curl -fsSL https://fnm.vercel.app/install | bash && fnm install 24.22.1 && fnm default 24.22.1",
        "python": "sudo apt install python3.12 python3.12-venv",
        "ffmpeg": "sudo apt install ffmpeg",
        "git": "sudo apt install git",
        "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    },
}
REOPEN = "Cài xong thì mở cửa sổ terminal MỚI (để PATH được cập nhật) rồi chạy lại: python -m videoauto.setup"


@dataclass
class Tools:
    node: str | None = None
    versions: dict[str, str] = field(default_factory=dict)


def _version(exe: str, args: list[str], run: Run) -> str | None:
    try:
        p = run([exe, *args], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    first = (p.stdout or p.stderr).strip().splitlines()
    return first[0].strip() if first else "?"


def node_problem(version: str, want: str = "24.22.1") -> str | None:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return f"không đọc được phiên bản Node ('{version}')"
    got = tuple(map(int, m.groups()))
    need = tuple(map(int, want.split(".")))
    if got[0] != need[0] or got < need:
        return f"cần Node {need[0]} (>= {want}), máy đang có {version}"
    return None


def node_candidates(which=shutil.which) -> list[str]:
    """`node` on PATH first, then every fnm-installed Node, newest first."""
    out = [p for p in [which("node")] if p]
    root = Path(os.environ.get("FNM_DIR") or Path(os.environ.get("APPDATA", "")) / "fnm") / "node-versions"
    exe = "node.exe" if sys.platform == "win32" else "bin/node"

    def key(d: Path):
        return tuple(int(x) for x in re.findall(r"\d+", d.name)[:3])
    for d in sorted(root.glob("v*"), key=key, reverse=True) if root.is_dir() else []:
        if (d / "installation" / exe).is_file():
            out.append(str(d / "installation" / exe))
    return out


def find_node(want: str, run: Run = _run, which=shutil.which) -> tuple[str | None, str | None]:
    """(path, version) of the first Node that fits, else (None, version of the one found first)."""
    first = None
    for c in node_candidates(which):
        v = _version(c, ["--version"], run)
        if v is None:
            continue
        if node_problem(v, want) is None:
            return c, v
        first = first or v
    return None, first


def check_tools(occ_repo: Path, vision_provider: str, run: Run = _run, which=shutil.which) -> Tools:
    """Every missing tool at once, each with its install command. Raises SetupFailed."""
    cmds = INSTALL[_os()]
    out, missing = Tools(), []

    want = "24.22.1"
    nvmrc = occ_repo / ".nvmrc"
    if nvmrc.is_file():
        want = nvmrc.read_text(encoding="utf-8").strip().lstrip("v") or want
    node, v = find_node(want, run, which)
    if node is None and v is None:
        missing.append(("Node.js 24", "không tìm thấy lệnh node", cmds["node"]))
    elif node is None:
        missing.append(("Node.js 24", node_problem(v, want), cmds["node"].replace("24.22.1", want)))
    else:
        out.node, out.versions["node"] = node, v

    py = sys.version_info
    if (py.major, py.minor) != (3, 12):
        missing.append(("Python 3.12", f"đang chạy Python {py.major}.{py.minor}", cmds["python"]))
    else:
        out.versions["python"] = f"{py.major}.{py.minor}.{py.micro}"

    for name, exe, args in (("ffmpeg", "ffmpeg", ["-version"]), ("ffprobe", "ffprobe", ["-version"]),
                            ("git", "git", ["--version"])):
        path = which(exe)
        v = _version(path, args, run) if path else None
        if v is None:
            missing.append((name, f"không tìm thấy lệnh {exe}", cmds["ffmpeg" if name == "ffprobe" else name]))
        else:
            out.versions[name] = v

    if vision_provider == "claude":
        path = which("claude")
        v = _version(path, ["--version"], run) if path else None
        if v is None:
            missing.append(("Claude Code", "không tìm thấy lệnh claude", cmds["claude"]
                            + "   rồi   claude auth login"))
        else:
            out.versions["claude"] = v
            logged = _claude_logged_in(path, run)
            if logged is False:
                missing.append(("Claude Code đăng nhập", "claude chưa đăng nhập", "claude auth login"))

    # one line per tool, the same order every time
    for name in ("node", "python", "ffmpeg", "ffprobe", "git", "claude"):
        if name in out.versions:
            ok(f"{name:8} {out.versions[name]}")
    if missing:
        lines = []
        for what, why, cmd in missing:
            lines.append(f"  - {what}: {why}.\n      Cài: {cmd}")
        raise SetupFailed(f"Thiếu {len(missing)} công cụ (sửa hết rồi chạy lại một lần):\n" + "\n".join(lines)
                          + f"\n  {REOPEN}")
    return out


def _claude_logged_in(exe: str, run: Run) -> bool | None:
    """True/False from `claude auth status`; None when this Claude Code cannot say."""
    try:
        p = run([exe, "auth", "status"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return bool(json.loads(p.stdout).get("loggedIn"))
    except (ValueError, AttributeError):
        return None if p.returncode != 0 else True


# ---------------------------------------------------------------- 2. the video engine (OpenChatCut)

@dataclass
class Pin:
    url: str
    branch: str
    commit: str


def read_pin(path: Path = PIN_FILE) -> Pin:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise SetupFailed(f"Thiếu tệp {path.name} trong thư mục videoauto. Tải lại videoauto: git pull") from None
    kv = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", kv.get("commit", "")) or not kv.get("url"):
        raise SetupFailed(f"{path.name} hỏng (cần url=..., branch=..., commit=<40 ký tự hex>). "
                          "Tải lại videoauto: git checkout -- openchatcut.pin")
    return Pin(kv["url"], kv.get("branch", "videoauto"), kv["commit"])


def write_pin(repo: Path, path: Path = PIN_FILE, run: Run = _run) -> Pin:
    """Maintainer: pin the OpenChatCut checked out here (HEAD), on the branch it is on."""
    head = run(["git", "rev-parse", "HEAD"], cwd=repo)
    if head.returncode != 0:
        raise SetupFailed(f"'{repo}' không phải một git clone của OpenChatCut: {_tail(head, 2)}")
    commit = head.stdout.strip()
    old = read_pin(path) if path.exists() else None
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    if branch in ("", "HEAD"):
        branch = old.branch if old else "videoauto"
    url = old.url if old else "https://github.com/bach04py/OpenChatCut"
    dirty = [ln for ln in run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo).stdout.splitlines()
             if ln.strip() and not ln.endswith("package-lock.json")]
    if dirty:
        raise SetupFailed(f"OpenChatCut tại '{repo}' có thay đổi chưa commit ({', '.join(d[3:] for d in dirty[:5])}); "
                          "commit và push chúng trước khi ghim, nếu không máy khác sẽ không có đúng mã này.")
    path.write_text("# OpenChatCut that this videoauto is tested with. python -m videoauto.setup clones and checks\n"
                    "# exactly this commit. Update (maintainer): python -m videoauto.setup --pin\n"
                    f"url={url}\nbranch={branch}\ncommit={commit}\n", encoding="utf-8", newline="\n")
    return Pin(url, branch, commit)


def _git_head(repo: Path, run: Run) -> str | None:
    p = run(["git", "rev-parse", "HEAD"], cwd=repo)
    return p.stdout.strip() if p.returncode == 0 else None


def ensure_engine(repo: Path, pin: Pin, interactive: bool, ask=None, run: Run = _run) -> None:
    """Clone the pinned fork when `repo` is absent; refuse (or, if the user agrees, switch) a
    checkout at another commit. Raises SetupFailed."""
    if not repo.exists() or (repo.is_dir() and not any(repo.iterdir())):
        note(f"Chưa có {repo}: tải OpenChatCut ({pin.url}, nhánh {pin.branch}) về đó…")
        p = run(["git", "clone", "--filter=blob:none", "--branch", pin.branch, pin.url, str(repo)], timeout=1800)
        if p.returncode != 0:
            raise SetupFailed(f"Không clone được OpenChatCut từ {pin.url}:\n      {_tail(p)}\n"
                              "  Kiểm tra mạng, rồi chạy lại: python -m videoauto.setup")
        if _git_head(repo, run) != pin.commit:
            _checkout(repo, pin, run)
        ok(f"OpenChatCut đã tải về {repo} (commit {pin.commit[:10]})")
        return
    if not (repo / ".git").exists():
        raise SetupFailed(f"'{repo}' đã có nhưng không phải một git clone của OpenChatCut. Đổi tên hoặc xoá thư "
                          "mục đó rồi chạy lại python -m videoauto.setup (nó sẽ tự tải đúng bản), hoặc sửa "
                          "openchatcut.repo trong config.yaml.")
    head = _git_head(repo, run)
    if head == pin.commit:
        ok(f"OpenChatCut tại {repo}: đúng bản đã ghim ({pin.commit[:10]})")
        return
    msg = (f"OpenChatCut tại {repo} đang ở commit {(head or '?')[:10]}, khác bản videoauto đã ghim "
           f"({pin.commit[:10]}, nhánh {pin.branch} của {pin.url}). Bản khác có thể thiếu bản vá font tiếng "
           "Việt hoặc làm hỏng catalogue/render.")
    if not interactive:
        raise SetupFailed(msg + f"\n  Chuyển sang bản đã ghim: chạy python -m videoauto.setup (không có --no-prompt) "
                                f"và trả lời Y, hoặc: git -C \"{repo}\" fetch {pin.url} {pin.branch} && "
                                f"git -C \"{repo}\" checkout --detach {pin.commit}")
    print(f"  {msg}")
    from .watch import _yes
    if not _yes(f"  Chuyển {repo} sang bản đã ghim {pin.commit[:10]}?", ask=ask):
        raise SetupFailed("Không chuyển: videoauto chỉ chạy với bản OpenChatCut đã ghim. Chạy lại "
                          "python -m videoauto.setup khi sẵn sàng chuyển.")
    _checkout(repo, pin, run)
    ok(f"OpenChatCut đã chuyển sang {pin.commit[:10]}")


def _checkout(repo: Path, pin: Pin, run: Run) -> None:
    has = run(["git", "cat-file", "-e", f"{pin.commit}^{{commit}}"], cwd=repo).returncode == 0
    if not has:
        f = run(["git", "fetch", "--filter=blob:none", pin.url, pin.branch], cwd=repo, timeout=1800)
        if f.returncode != 0:
            raise SetupFailed(f"Không tải được nhánh {pin.branch} từ {pin.url}:\n      {_tail(f)}")
    c = run(["git", "checkout", "--detach", pin.commit], cwd=repo, timeout=600)
    if c.returncode != 0:
        raise SetupFailed(f"Không chuyển được OpenChatCut sang {pin.commit[:10]}:\n      {_tail(c)}\n"
                          f"  Nếu git báo có thay đổi cục bộ: git -C \"{repo}\" stash   rồi chạy lại "
                          "python -m videoauto.setup")


# ---------------------------------------------------------------- 3. npm ci + what install scripts fetch

def npm_for(node: str, which=shutil.which) -> str | None:
    d = Path(node).parent
    for name in (["npm.cmd", "npm.exe"] if sys.platform == "win32" else ["npm"]):
        if (d / name).is_file():
            return str(d / name)
    return which("npm")


def _lock_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for name in ("package.json", "package-lock.json"):
        f = repo / name
        h.update(f.read_bytes().replace(b"\r\n", b"\n") if f.is_file() else b"")
    return h.hexdigest()


def _node_env(node: str) -> dict:
    # npm's own scripts call `node`: the one found must come first on PATH
    return {"PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
            "ELECTRON_SKIP_BINARY_DOWNLOAD": "1",    # videoauto never starts the desktop app
            "npm_config_fund": "false", "npm_config_audit": "false", "npm_config_update_notifier": "false"}


def script_problems(repo: Path, node: str, run: Run = _run) -> dict[str, str]:
    """{package: what is wrong} for what install scripts should have produced."""
    out = {}
    pkg = repo / "node_modules" / "ffmpeg-static"
    exe = pkg / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not pkg.is_dir():
        out["ffmpeg-static"] = "thư viện chưa được cài"
    elif not exe.is_file() or exe.stat().st_size == 0:
        out["ffmpeg-static"] = f"thiếu {exe.name} (script cài đặt của nó không chạy)"
    probe = run([node, "-e", "require('esbuild').transformSync('let a: number = 1', {loader: 'ts'});"
                             "process.stdout.write('esbuild-ok')"], cwd=repo, env=_node_env(node), timeout=120)
    if "esbuild-ok" not in probe.stdout:
        out["esbuild"] = "không chạy được (" + (_tail(probe, 2).replace("\n      ", " ")[:200] or "lỗi") + ")"
    tsx = run([node, "--import", "tsx", "cli/main.ts", "--help"], cwd=repo, env=_node_env(node), timeout=180)
    if tsx.returncode != 0 and "esbuild" not in out:
        out["tsx"] = "occ không khởi động được: " + _tail(tsx, 3).replace("\n      ", " ")[:300]
    return out


def ensure_npm(repo: Path, node: str, pin: Pin, run: Run = _run, which=shutil.which) -> None:
    npm = npm_for(node, which)
    if npm is None:
        raise SetupFailed(f"Không tìm thấy npm cạnh {node}. Cài lại Node.js 24: {INSTALL[_os()]['node']}")
    stamp_file = repo / "node_modules" / NPM_STAMP
    want = {"lock": _lock_hash(repo), "node": run([node, "--version"]).stdout.strip(), "commit": pin.commit}
    try:
        have = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = None
    env = _node_env(node)
    if have != want:
        note("npm ci (cài thư viện của OpenChatCut, thường dưới 2 phút)…")
        t0 = time.monotonic()
        p = run([npm, "ci", "--no-audit", "--no-fund"], cwd=repo, env=env, timeout=3600)
        if p.returncode != 0:
            raise SetupFailed(f"npm ci trong {repo} thất bại (exit {p.returncode}):\n      {_tail(p, 12)}\n"
                              "  Kiểm tra mạng / proxy công ty rồi chạy lại: python -m videoauto.setup")
        ok(f"npm ci xong trong {time.monotonic() - t0:.0f}s")
        if "not yet covered by allowScripts" in p.stdout + p.stderr:
            note("npm báo có install script chưa được cho phép (allowScripts); kiểm tra kết quả của chúng…")
    else:
        ok("thư viện OpenChatCut đã cài, không đổi từ lần trước")
    bad = script_problems(repo, node, run)
    if bad:                      # an approval given since the last run takes effect with a rebuild
        fixable = [p for p in bad if p in ("ffmpeg-static", "esbuild")]
        if fixable:
            note(f"npm rebuild {' '.join(fixable)}…")
            run([npm, "rebuild", *fixable], cwd=repo, env=env, timeout=1800)
            bad = script_problems(repo, node, run)
    if bad:
        stamp_file.unlink(missing_ok=True)
        lines = "\n".join(f"  - {k}: {v}" for k, v in bad.items())
        approve = [p for p in bad if p in ("ffmpeg-static", "esbuild")]
        fix = ""
        if approve:
            fix = ("\n  Máy này chặn install script của npm. Cho phép đúng các gói cần thiết:\n"
                   f"      npm --prefix \"{repo}\" install-scripts approve {' '.join(approve)}\n"
                   "  rồi chạy lại: python -m videoauto.setup   (nó tự chạy npm rebuild cho các gói đó)")
        raise SetupFailed(f"Thư viện của OpenChatCut cài chưa đủ:\n{lines}{fix}")
    stamp_file.write_text(json.dumps(want), encoding="utf-8")
    ok("ffmpeg-static có binary, esbuild chạy được, occ khởi động được")


# ---------------------------------------------------------------- 4-5. catalogue, brand

def ensure_catalogue(pcfg) -> None:
    from . import occ_catalogue
    occ_catalogue.ensure(pcfg.catalogue, pcfg.occ.repo, pcfg.occ.node, say=note)
    ok(occ_catalogue.summary(pcfg.catalogue))


def ensure_brand(root: Path) -> Path:
    d = root / "brand"
    d.mkdir(exist_ok=True)
    ok(f"thư mục logo: {d}")
    note("Không có logo vẫn chạy được. Muốn có logo: đặt ảnh PNG nền trong suốt vào thư mục trên (ví dụ")
    note("brand/logo.png), rồi trong config.yaml bỏ dấu # ở đầu dòng  # logo: brand/logo.png")
    return d


# ---------------------------------------------------------------- 6. folders

def ensure_paths(args, interactive: bool) -> dict[str, Path]:
    from . import watch
    given = {"footage": args.footage, "scripts": args.scripts, "output": args.output}
    if any(given.values()):
        missing = [watch.KEYS[k] for k, v in given.items() if v is None]
        if missing:
            raise SetupFailed("Truyền đủ cả ba thư mục: --footage, --scripts, --output.")
        got: dict[str, Path] = {}
        for key, _ in watch.QUESTIONS:
            p = watch._resolve(watch.clean_path(str(given[key])))
            if key == "scripts":
                p.mkdir(parents=True, exist_ok=True)
            err = watch._validate_one(key, p, got, interactive=False)
            if err:
                raise SetupFailed(f"--{key}: {err}")
            got[key] = p
        f = watch.save(got)
        ok(f"đã lưu vào {f}")
        return got
    if not interactive:
        try:
            got = watch._saved_paths_or_exit(watch.load_saved())
        except watch.SetupExit as e:
            raise SetupFailed(e.message + " Hoặc truyền --footage <thư mục> --scripts <thư mục> "
                                          "--output <thư mục>.") from None
        ok(f"dùng thư mục đã lưu trong {watch.local_file()}")
        return got
    saved = watch.load_saved()
    print("  Ba thư mục (Enter = giữ giá trị trong ngoặc vuông; có thể kéo thả thư mục vào đây):")
    try:
        got = watch.ask_paths(saved)
    except watch.SetupExit as e:
        raise SetupFailed(e.message) from None
    f = watch.save(got)
    ok(f"đã lưu vào {f}")
    return got


# ---------------------------------------------------------------- 7. the real render

def check_plan(pcfg, cat, duration: float = 1.0) -> dict:
    """1 s of the sample clip into a 9:16 frame (cropped to fill) with one TEXT overlay."""
    from .cache import sha256_of
    from .planner import overlays as ov
    from .planner.planner import overlay_templates
    tpls = overlay_templates(pcfg, cat)
    if not tpls:
        from .features import no_template_cause
        raise SetupFailed("Không có template chữ nào dùng được cho khung 9:16 để dựng thử. "
                          + no_template_cause(pcfg, cat))
    tpl = tpls[0]
    keys = ov.text_params(tpl)
    fields = {k: "" for k in keys}
    fields[keys[0]] = CHECK_TEXT
    return {"version": 1, "target": {"duration": duration, "aspect": "9:16", "fps": pcfg.brief.fps},
            "confidence": 1.0, "tier": "basic",
            "clips": [{"src": SAMPLE.name, "in": 0.0, "out": duration, "track": "V1", "effects": []}],
            "transitions": [], "fades": {"in": 0, "out": 0},
            "overlays": [{"template": tpl["assetId"], "at": 0.0, "duration": duration, "fields": fields}],
            "text_lines": [CHECK_TEXT], "script": f"TEXT: {CHECK_TEXT}", "script_source": "user",
            "warnings": [], "sources": {SAMPLE.name: {"sha256": sha256_of(SAMPLE), "duration": 2.0,
                                                      "orientation": "horizontal"}}}


def render_check(pcfg, output_dir: Path, occ=None) -> Path:
    """Build and render the check video for real. Returns its path; raises SetupFailed."""
    from .builder.builder import Builder
    from .planner.catalogue import Catalogue
    work = output_dir / ".setup-check"
    footage = work / "footage"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    footage.mkdir(parents=True)
    shutil.copyfile(SAMPLE, footage / SAMPLE.name)
    brief = dataclasses.replace(pcfg.brief, aspect="9:16", fit="fill")
    cfg = dataclasses.replace(pcfg, brief=brief, footage_dir=footage)
    cat = Catalogue.load(cfg.catalogue)
    plan = check_plan(cfg, cat)
    out = output_dir / CHECK_NAME
    out.unlink(missing_ok=True)
    note(f"dựng thật 1 giây: clip mẫu 16:9 → khung 9:16 (cắt cho đầy khung) + chữ \"{CHECK_TEXT}\"…")
    rep = Builder(cfg, occ).build(plan, out)
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "build.json").write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    if rep.error is not None:
        raise SetupFailed(f"Dựng thử thất bại: {rep.error.message}\n  Chi tiết từng lệnh: {work / 'build.json'}")
    labels = {c["label"]: c["ok"] for c in rep.to_json()["commands"]}
    missing = [what for label, what in (("fill clip 1", "cắt khung (crop)"), ("render", "xuất video"))
               if not labels.get(label)]
    if not rep.overlays or not all(o.get("ok") for o in rep.overlays):
        missing.append("chữ (TEXT)")
    if missing or not out.is_file() or out.stat().st_size == 0:
        raise SetupFailed(f"Dựng thử chưa đủ ({', '.join(missing) or 'không có tệp mp4'}). "
                          f"Chi tiết: {work / 'build.json'}")
    shutil.rmtree(footage, ignore_errors=True)
    ok(f"đã xuất {out} ({out.stat().st_size / 1e3:.0f} KB, {rep.seconds:.0f}s): mở ra xem chữ và khung hình")
    return out


# ---------------------------------------------------------------- entry point

def main(argv: list[str] | None = None, run: Run = _run, occ=None, ask=None) -> int:
    from .cli import setup_io
    ap = argparse.ArgumentParser(prog="python -m videoauto.setup",
                                 description="Cài đặt videoauto trên máy mới, kết thúc bằng một lần dựng thật.")
    ap.add_argument("--no-prompt", action="store_true", help="không hỏi gì (máy không có người ngồi)")
    ap.add_argument("--footage", type=Path, help="thư mục video nguồn (không hỏi)")
    ap.add_argument("--scripts", type=Path, help="thư mục kịch bản (không hỏi)")
    ap.add_argument("--output", type=Path, help="thư mục xuất video (không hỏi)")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (mặc định: trong thư mục hiện tại)")
    ap.add_argument("--pin", action="store_true",
                    help="(người bảo trì) ghi openchatcut.pin từ OpenChatCut đang có ở máy này rồi thoát")
    args = ap.parse_args(argv)
    setup_io(False)
    from .watch import _stdin_is_tty
    interactive = not args.no_prompt and (ask is not None or _stdin_is_tty())
    try:
        return _main(args, interactive, run, occ, ask)
    except SetupFailed as e:
        print(f"\nDỪNG: {e.message}", file=sys.stderr, flush=True)
        print("Chưa cài đặt xong.", file=sys.stderr, flush=True)
        return EXIT_FAIL
    except UserError as e:
        print(f"\nDỪNG: {e.message}", file=sys.stderr, flush=True)
        print("Chưa cài đặt xong.", file=sys.stderr, flush=True)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\nĐã dừng. Chưa cài đặt xong.", file=sys.stderr)
        return 130


def _main(args, interactive: bool, run: Run, occ, ask) -> int:
    from .brief import load_project_config
    from .config import load_config
    config = args.config or Path.cwd() / "config.yaml"
    if not config.is_file():
        raise SetupFailed(f"Không thấy config.yaml trong {Path.cwd()}. Chạy lệnh này trong thư mục videoauto: "
                          f"cd \"{ROOT}\"")
    logo_problems: list[str] = []          # never fails on the logo: step 5 reports it
    pcfg = load_project_config(config, problems=logo_problems)
    repo = pcfg.occ.repo.resolve()

    if args.pin:
        pin = write_pin(repo)
        print(f"Đã ghi {PIN_FILE}: {pin.url} nhánh {pin.branch} commit {pin.commit}")
        return 0

    t0 = time.monotonic()
    print("videoauto: cài đặt (chạy lại bao nhiêu lần cũng được; bước đã xong sẽ được bỏ qua nhanh)")
    step(1, "Công cụ trên máy")
    vision = load_config(None)["vision"]["provider"]
    tools = check_tools(repo, vision, run)

    step(2, "Bộ dựng video OpenChatCut (bản đã ghim)")
    pin = read_pin()
    ensure_engine(repo, pin, interactive, ask=ask, run=run)

    step(3, "Thư viện của bộ dựng (npm)")
    ensure_npm(repo, tools.node, pin, run)
    pcfg.occ.node = tools.node                              # found here, maybe through fnm

    step(4, "Danh mục template / hiệu ứng (catalogue.json)")
    ensure_catalogue(pcfg)

    step(5, "Logo (tuỳ chọn)")
    ensure_brand(ROOT)
    for m in logo_problems:
        note(f"Chú ý (logo sẽ bị bỏ qua cho tới khi sửa): {m}")

    step(6, "Thư mục làm việc")
    paths = ensure_paths(args, interactive)

    step(7, "Dựng thử một video thật")
    out = render_check(pcfg, paths["output"], occ)

    print(f"\nCài đặt xong ({time.monotonic() - t0:.0f}s). Video kiểm tra: {out}")
    print("Chạy hằng ngày:  python -m videoauto.watch")
    print(f"  rồi thả tệp kịch bản .txt vào {paths['scripts']}; video ra ở {paths['output']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
