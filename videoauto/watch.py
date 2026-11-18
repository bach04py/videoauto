"""CLI: python -m videoauto.watch

Start once from a terminal: it checks the prerequisites, asks for three folders (footage, scripts,
output), then keeps running and builds one video for every new script (.txt) dropped into the
scripts folder. Footage is a standing library: it is indexed once (cached) and reused.

Inside the output folder, next to the videos, it keeps:
    processing/   the script currently being built (at most one)
    done/         scripts that produced a video
    failed/       scripts that failed, each with <name>.loi.txt saying why
                  (a video from a failed build stays next to the others as <name>.loi.mp4)
    .index/       index cache shared by every run
    .logs/        runs.log (one line per run) plus one full log per run

While it runs in a console window, single keys work (never under --no-prompt or without a console):
    S  edit the planner style (context, tone, pace, prefer_luts) and save it into config.yaml
    P  show the folders in use
    Q  quit (after the current build, if one is running)
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .build import keep_failed_video
from .cli import setup_io
from .errors import LimitReached, PlanRejected, UserError

log = logging.getLogger("videoauto")

LOCAL_FILE = "videoauto.local.yaml"
SETTLE_SECONDS = 3.0          # a script must keep the same size this long before it is touched
POLL_SECONDS = 0.5
RESCAN_SECONDS = 10.0         # fallback scan in case a file system event was missed (network drives)
EXIT_SETUP = 2

QUESTIONS = (
    ("footage", "Thư mục video nguồn"),
    ("scripts", "Thư mục kịch bản"),
    ("output", "Thư mục xuất video"),
)
KEYS = {"footage": "footage_dir", "scripts": "scripts_dir", "output": "output_dir"}
HINT = "[S] sửa phong cách   [P] xem đường dẫn   [Q] thoát"
STAGE_VI = {"read": "đọc kịch bản", "index": "lập chỉ mục video nguồn", "plan": "lập kế hoạch dựng",
            "build": "dựng video bằng OpenChatCut", "": "chuẩn bị"}


class SetupExit(Exception):
    """Stop before watching; the message is already Vietnamese."""

    def __init__(self, message: str, code: int = EXIT_SETUP):
        super().__init__(message)
        self.message, self.code = message, code


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class _Console:
    """While the style editor owns the terminal, status lines from the build wait and are printed
    when it closes, so they never land in the middle of a question."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: list[str] | None = None

    def emit(self, text: str) -> None:
        with self._lock:
            if self._held is not None:
                self._held.append(text)
                return
            print(text, flush=True)

    @staticmethod
    def direct(text: str) -> None:
        print(text, flush=True)

    def hold(self) -> None:
        with self._lock:
            self._held = []

    def release(self) -> None:
        with self._lock:
            lines, self._held = self._held or [], None
            for ln in lines:
                print(ln, flush=True)


console = _Console()


def say(msg: str) -> None:
    console.emit(f"[{now()}] {msg}")


# ---------------------------------------------------------------- paths: cleaning and validation

def clean_path(raw: str) -> str:
    """What a Windows user pastes: surrounding spaces and quotes (drag-and-drop), a trailing
    separator. Returns "" for an empty answer."""
    s = raw.strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    s = os.path.expandvars(os.path.expanduser(s))
    if not s:
        return ""
    t = s.rstrip("\\/")
    if not t:                       # "/" stays the root
        return s[:1]
    if t.endswith(":"):             # "D:\" stays the drive root ("D:" alone means D's current dir)
        return t + os.sep
    return t


def _resolve(s: str) -> Path:
    return Path(s).resolve()


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _inside(child: Path, parent: Path) -> bool:
    """child is parent or below it."""
    c, p = os.path.normcase(str(child)), os.path.normcase(str(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def has_video(folder: Path) -> bool:
    from .indexer import VIDEO_EXT
    try:
        return any(p.suffix.lower() in VIDEO_EXT and p.is_file() for p in folder.rglob("*"))
    except OSError:
        return False


WORK_DIRS = ("processing", "done", "failed", ".index", ".logs")
OUTPUT_SUFFIXES = (".mp4", ".mp4.build.json", ".plan.json")


def work_root(output: Path) -> Path:
    """Where processing/, done/, failed/, .index/ and .logs/ live: inside the output folder the
    user chose, never anywhere they did not name."""
    return output


def check_footage(p: Path) -> str | None:
    if not p.exists():
        return f"Thư mục '{p}' không tồn tại."
    if not p.is_dir():
        return f"'{p}' là một tệp, không phải thư mục."
    if not has_video(p):
        return f"Thư mục '{p}' có tồn tại nhưng không có tệp video nào (.mp4, .mov, …)."
    return None


def check_scripts(p: Path, footage: Path) -> str | None:
    if p.exists() and not p.is_dir():
        return f"'{p}' là một tệp, không phải thư mục."
    if _same(p, footage):
        return "Thư mục kịch bản phải khác thư mục video nguồn."
    if _inside(p, footage):
        return ("Thư mục kịch bản không được nằm trong thư mục video nguồn: kịch bản sẽ bị coi là "
                "tệp nguồn. Chọn một thư mục bên ngoài.")
    return None


def check_output(p: Path, footage: Path, scripts: Path) -> str | None:
    if p.exists() and not p.is_dir():
        return f"'{p}' là một tệp, không phải thư mục."
    if _same(p, footage) or _same(p, scripts):
        return "Thư mục xuất video phải khác thư mục video nguồn và thư mục kịch bản."
    if _inside(p, footage):
        return ("Thư mục xuất video không được nằm trong thư mục video nguồn: video đã dựng sẽ bị "
                "lập chỉ mục như cảnh quay.")
    root = work_root(p)
    for name in WORK_DIRS:
        if _inside(scripts, root / name):
            return (f"Thư mục kịch bản nằm trong thư mục làm việc '{root / name}' của thư mục xuất. "
                    "Chọn thư mục xuất khác.")
    return None


def ensure_output(p: Path) -> str | None:
    """Create the output folder if needed and prove it is writable."""
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".videoauto-ghi-thu-{os.getpid()}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        return f"Không tạo được hoặc không ghi được vào '{p}': {e.strerror or e}."
    return None


# ---------------------------------------------------------------- saved paths (videoauto.local.yaml)

def local_file() -> Path:
    return Path.cwd() / LOCAL_FILE


def load_saved() -> dict[str, str]:
    from . import yamlio
    f = local_file()
    if not f.exists():
        return {}
    try:
        raw = yamlio.load(f.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yamlio.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: str(raw[v]) for k, v in KEYS.items() if raw.get(v)}


def save(paths: dict[str, Path]) -> Path:
    from . import yamlio
    f = local_file()
    data = {KEYS[k]: str(paths[k]) for k, _ in QUESTIONS}
    text = ("# Đường dẫn của python -m videoauto.watch trên máy này (không đưa lên git).\n"
            "# Xoá: python -m videoauto.watch --reset\n"
            + yamlio.dump(data))
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, f)
    return f


# ---------------------------------------------------------------- interactive setup

def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise SetupExit("Dừng: không còn dữ liệu nhập (stdin đã đóng).") from None


def _yes(prompt: str, ask=None, default: bool = True, out=print) -> bool:
    ask = ask or _ask
    while True:
        a = ask(f"{prompt} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
        if a == "":
            return default
        if a in ("y", "yes", "c", "co", "có"):
            return True
        if a in ("n", "no", "k", "khong", "không"):
            return False
        out("  Trả lời Y (có) hoặc n (không).")


def ask_paths(saved: dict[str, str]) -> dict[str, Path]:
    got: dict[str, Path] = {}
    for i, (key, label) in enumerate(QUESTIONS, 1):
        default = saved.get(key)
        prompt = f"{i}. {label}" + (f" [{default}]" if default else "") + ": "
        while True:
            answer = clean_path(_ask(prompt))
            if not answer:
                if not default:
                    print("  Chưa nhập đường dẫn. Dán hoặc kéo thả thư mục vào đây rồi nhấn Enter.")
                    continue
                answer = clean_path(default)
            p = _resolve(answer)
            err = _validate_one(key, p, got, interactive=True)
            if err:
                print(f"  {err}")
                continue
            got[key] = p
            break
    return got


def _validate_one(key: str, p: Path, got: dict[str, Path], interactive: bool) -> str | None:
    if key == "footage":
        return check_footage(p)
    if key == "scripts":
        err = check_scripts(p, got["footage"])
        if err:
            return err
        if not p.exists():
            if not interactive:
                return f"Thư mục kịch bản '{p}' không tồn tại."
            if not _yes(f"  Thư mục '{p}' chưa có. Tạo mới?"):
                return "Chọn thư mục kịch bản khác."
            try:
                p.mkdir(parents=True)
            except OSError as e:
                return f"Không tạo được '{p}': {e.strerror or e}."
        return None
    return check_output(p, got["footage"], got["scripts"]) or ensure_output(p)


def print_summary(paths: dict[str, Path]) -> None:
    print("Tóm tắt:")
    for key, label in QUESTIONS:
        print(f"  {label + ':':24} {paths[key]}")
    print(f"  Trong thư mục xuất còn có: {', '.join(WORK_DIRS)}")


def setup_paths(no_prompt: bool) -> dict[str, Path]:
    saved = load_saved()
    if no_prompt:
        return _saved_paths_or_exit(saved)
    print("Thiết lập thư mục (Enter = giữ giá trị trong ngoặc vuông).")
    paths = ask_paths(saved)
    print_summary(paths)
    if _yes("Lưu lại cho lần sau?"):
        print(f"  Đã lưu vào {save(paths)}")
    else:
        print("  Không lưu: lần sau sẽ hỏi lại.")
    return paths


def _saved_paths_or_exit(saved: dict[str, str]) -> dict[str, Path]:
    hint = "Hãy chạy python -m videoauto.watch một lần trong cửa sổ terminal để chọn và lưu thư mục."
    got: dict[str, Path] = {}
    for key, label in QUESTIONS:
        raw = clean_path(saved.get(key, ""))
        if not raw:
            raise SetupExit(f"Lỗi: chưa lưu '{label}' trong {LOCAL_FILE}. {hint}")
        p = _resolve(raw)
        err = _validate_one(key, p, got, interactive=False)
        if err:
            raise SetupExit(f"Lỗi: '{label}' đã lưu ({p}) không dùng được: {err} {hint}")
        got[key] = p
    return got


# ---------------------------------------------------------------- prerequisites

@dataclass
class Env:
    pcfg: object
    vcfg: dict
    provider: object
    occ: object


def check_prerequisites(config: Path | None, vision_config: Path | None, provider=None, occ=None) -> Env:
    """ffmpeg, Node + OpenChatCut repo, catalogue.json (generated if missing or stale), the logo,
    model access. Every problem is reported in one message, not one per run."""
    from .brief import load_project_config
    from .features import no_template_cause
    from .planner.planner import overlay_templates
    from .preflight import Problems, check_tools
    problems = Problems()
    try:
        pcfg = load_project_config(config, problems=problems.items)
        env = check_tools(pcfg, problems, occ=occ, provider=provider, vision_config=vision_config, say=print)
        problems.raise_if_any()
    except UserError as e:
        raise SetupExit(f"Lỗi: {e.message}") from None
    if not overlay_templates(pcfg, env.cat):
        # each script is checked when it arrives; say it once now so nobody is surprised later
        print(f"Chú ý: kịch bản có dòng TEXT: sẽ bị từ chối (khung {pcfg.brief.aspect} không có template "
              f"chữ nào dùng được). {no_template_cause(pcfg, env.cat)}")
    return Env(pcfg, env.vcfg, env.provider, env.occ)


def first_setup(config: Path | None) -> str | None:
    """Why the folders must be asked BEFORE the environment check, or None. A fresh clone has no
    videoauto.local.yaml, and its config.yaml still holds the sample values marked CHANGE: checking
    those first would fail on paths the user was never asked for, and exit before the questions."""
    if not local_file().exists():
        return f"chưa có {LOCAL_FILE}"
    # footage_dir / script_file are run's; the watcher takes its folders from videoauto.local.yaml
    sample = [s for s in changed_sample_lines(config or Path.cwd() / "config.yaml")
              if not s.startswith(("footage_dir:", "script_file:"))]
    if sample:
        return "config.yaml còn giá trị mẫu (CHANGE): " + ", ".join(sample)
    return None


def changed_sample_lines(path: Path) -> list[str]:
    """`key: value` lines marked `# CHANGE` whose value is a path that does not exist here."""
    import re
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if "CHANGE" not in line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"\s*([A-Za-z_]+)\s*:\s*([^#]+?)\s*(#|$)", line)
        if not m:
            continue
        value = m.group(2).strip().strip("\"'")
        p = Path(value)
        if not p.is_absolute():
            p = path.parent / p
        if value and not p.exists():
            out.append(f"{m.group(1)}: {value}")
    return out


# ---------------------------------------------------------------- the watcher

def _handler(inbox: "queue.SimpleQueue[Path]"):
    """watchdog handler: only hands paths to the main thread; never does work itself."""
    from watchdog.events import FileSystemEventHandler

    class Events(FileSystemEventHandler):
        def dispatch(self, event) -> None:
            if event.is_directory:
                return
            for attr in ("dest_path", "src_path"):
                p = getattr(event, attr, None)
                if p:
                    inbox.put(Path(os.fsdecode(p)))

    return Events()


def is_script(p: Path) -> bool:
    return p.suffix.lower() == ".txt" and not p.name.startswith((".", "~"))


def free_stem(stem: str, taken) -> str:
    """stem, or stem-2, stem-3, … : the first one for which taken(candidate) is false."""
    n, cand = 1, stem
    while taken(cand):
        n += 1
        cand = f"{stem}-{n}"
    return cand


def unique(folder: Path, stem: str, suffix: str = ".txt") -> Path:
    """folder/stem.txt, or stem-2.txt, stem-3.txt, … when that name (or its .loi.txt) is taken."""
    return folder / (free_stem(stem, lambda c: (folder / f"{c}{suffix}").exists()
                               or (folder / f"{c}.loi.txt").exists()) + suffix)


def output_stem(output: Path, stem: str) -> str:
    """A name none of whose files (video, .mp4.build.json, .plan.json) exist yet: a second
    tap-nguc.txt gives tap-nguc-2.mp4, never an overwritten tap-nguc.mp4."""
    return free_stem(stem, lambda c: any((output / f"{c}{x}").exists() for x in OUTPUT_SUFFIXES))


class Watcher:
    def __init__(self, paths: dict[str, Path], env: Env, settle: float = SETTLE_SECONDS,
                 poll: float = POLL_SECONDS):
        self.footage, self.scripts, self.output = paths["footage"], paths["scripts"], paths["output"]
        root = work_root(self.output)
        self.processing, self.done, self.failed = root / "processing", root / "done", root / "failed"
        self.index_dir, self.logs = root / ".index", root / ".logs"
        self.env, self.settle, self.poll = env, settle, poll
        self.inbox: "queue.SimpleQueue[Path]" = queue.SimpleQueue()
        self.seen: dict[str, tuple[int, int, float]] = {}       # name -> (size, mtime_ns, stable since)
        self.locked: set[str] = set()
        self.current: Path | None = None
        self.runs = 0
        self.editing = threading.Event()        # style editor open: no new build starts meanwhile
        self.keys_on = False                    # a key listener is running: show the hint line
        self._stop: threading.Event | None = None
        self._observer = None
        self._last_scan = 0.0
        self._flog = logging.getLogger("videoauto_watch_runlog")     # file only: no console handler
        self._flog.propagate = False
        self._flog.setLevel(logging.DEBUG)

    # -- setup / teardown
    def prepare(self) -> None:
        for d in (self.processing, self.done, self.failed, self.index_dir, self.logs, self.output):
            d.mkdir(parents=True, exist_ok=True)
        self._clean_tmp()
        self._recover()

    def _recovered_file(self) -> Path:
        return self.logs / "recovered.txt"

    def _recovered(self) -> set[str]:
        f = self._recovered_file()
        return set(f.read_text(encoding="utf-8").split("\n")) - {""} if f.exists() else set()

    def _set_recovered(self, names: set[str]) -> None:
        f = self._recovered_file()
        if names:
            f.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
        elif f.exists():
            f.unlink()

    def _recover(self) -> None:
        """A script left in processing/ means the last run died (crash, power, out of memory).
        It goes back once; if it is found there a second time it goes to failed/."""
        recovered = self._recovered()
        for p in sorted(self.processing.iterdir()):
            if not p.is_file():
                continue
            if p.name in recovered:
                recovered.discard(p.name)
                self._to_failed(p, "khôi phục", "Lần dựng kịch bản này bị gián đoạn hai lần liên tiếp "
                                "(máy tắt, hết bộ nhớ hoặc chương trình bị đóng).",
                                "Kiểm tra bộ nhớ trống và đóng bớt ứng dụng, rồi chép kịch bản vào lại "
                                "thư mục kịch bản.", started=None)
                continue
            dest = self.scripts / p.name
            if dest.exists():
                dest = unique(self.scripts, p.stem)
            os.replace(p, dest)
            recovered.add(dest.name)
            say(f"Khôi phục {dest.name}: lần trước bị gián đoạn khi đang dựng, sẽ dựng lại một lần.")
        self._set_recovered(recovered)

    def _clean_tmp(self) -> None:
        for p in self.index_dir.glob("*.tmp"):
            try:
                p.unlink()
            except OSError:
                pass

    def start_observer(self) -> None:
        from watchdog.observers import Observer
        self._observer = Observer()
        self._observer.schedule(_handler(self.inbox), str(self.scripts), recursive=False)
        self._observer.start()

    def stop_observer(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    # -- discovery
    def _scan(self) -> None:
        try:
            for p in self.scripts.iterdir():
                self.inbox.put(p)
        except OSError:
            pass
        self._last_scan = time.monotonic()

    def _drain(self) -> None:
        while True:
            try:
                p = self.inbox.get_nowait()
            except queue.Empty:
                return
            if not _same(p.parent.resolve(), self.scripts) or not is_script(p) or p.name in self.seen:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if not p.is_file():
                continue
            self.seen[p.name] = (st.st_size, st.st_mtime_ns, time.monotonic())
            say(f"Thấy kịch bản mới: {p.name}")

    def ready(self) -> list[Path]:
        """Scripts whose size and mtime have not changed for `settle` seconds, oldest first."""
        if time.monotonic() - self._last_scan >= RESCAN_SECONDS:
            self._scan()
        self._drain()
        t = time.monotonic()
        out = []
        for name, (size, mtime, since) in list(self.seen.items()):
            p = self.scripts / name
            try:
                st = p.stat()
            except OSError:
                del self.seen[name]          # removed or renamed before it settled
                continue
            if (st.st_size, st.st_mtime_ns) != (size, mtime):
                self.seen[name] = (st.st_size, st.st_mtime_ns, t)
                continue
            if t - since >= self.settle:
                out.append(p)
        return out

    def waiting(self) -> int:
        return len(self.seen)

    # -- one run
    def claim(self, p: Path) -> Path | None:
        """Move the script to processing/ at once, so nothing can pick it up twice."""
        dest = self.processing / p.name
        try:
            os.replace(p, dest)
        except FileNotFoundError:
            self.seen.pop(p.name, None)
            return None
        except OSError:
            if p.name not in self.locked:
                self.locked.add(p.name)
                say(f"{p.name} đang bị chương trình khác giữ (đang mở để sửa?); sẽ thử lại.")
            return None
        self.seen.pop(p.name, None)
        self.locked.discard(p.name)
        return dest

    def build(self, script: Path) -> bool:
        """Build one claimed script. Never raises except KeyboardInterrupt (handled by the caller
        through `abandon`)."""
        from .run import PipelineResult, run_pipeline
        from .indexer import collect

        self.current = script
        self.runs += 1
        stem = output_stem(self.output, script.stem)
        video = self.output / f"{stem}.mp4"
        plan_path = self.output / f"{stem}.plan.json"
        self._stem = stem
        started = time.time()
        t0 = time.monotonic()
        res = PipelineResult(stage="read")
        logfile = self.logs / f"{datetime.now():%Y%m%d-%H%M%S}-{stem}.log"
        handler = logging.FileHandler(logfile, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        self._flog.addHandler(handler)
        self._started, self._res, self._t0 = started, res, t0
        self._drain()
        rest = self.waiting()
        say(f"Bắt đầu dựng {script.name}" + (f" (còn {rest} kịch bản đang chờ)" if rest else ""))
        try:
            text = self._read_script(script)
            pcfg = self.env.pcfg       # this build keeps this object even if S saves a new config
            from .features import check_text_overlays
            from .planner.catalogue import Catalogue
            check_text_overlays(pcfg, Catalogue.load(pcfg.catalogue), text)   # before indexing, not after render
            pcfg.footage_dir, pcfg.script_file = self.footage, script
            if stem != script.stem:
                say(f"  {script.stem}.mp4 đã có: video mới đặt tên {video.name} (không ghi đè)")
            sources = collect(self.footage, self.index_dir)
            log.info("kịch bản %s (%d ký tự), %d tệp nguồn", script.name, len(text), len(sources))
            run_pipeline(pcfg, self.env.vcfg, self.env.provider, self.env.occ, sources, self.index_dir,
                         plan_path, video, result=res,
                         on_stage=lambda k: say(f"  {STAGE_VI[k][:1].upper() + STAGE_VI[k][1:]}…"),
                         on_note=lambda m: say(f"  {m[:1].upper() + m[1:]}"))
        except KeyboardInterrupt:
            self.abandon()
            raise
        except Exception as e:  # noqa: BLE001 - one script must never stop the watcher
            # the traceback goes to the run's log file only, never to the console
            self._flog.error("lỗi ở bước %s: %s", res.stage, getattr(e, "message", e),
                             exc_info=not isinstance(e, UserError))
            what, todo = describe_failure(e, res.stage, logfile)
            # never delete the video: a failed check may have refused a complete render
            kept = keep_failed_video(video) or getattr(e, "kept", None)
            if kept is not None and str(kept) not in what:
                what += f" Video đã dựng được giữ lại để xem: {kept}"
            self._to_failed(script, STAGE_VI.get(res.stage, res.stage), what, todo, started=started,
                            logfile=logfile, calls=res.model_calls, t0=t0, stem=stem, kept=kept)
            return False
        else:
            secs = time.monotonic() - t0
            dest = self._archive(script, self.done, stem)
            enc = f", mã hoá {res.encoder['used']}" if res.encoder else ""
            say(f"XONG {script.name} → {video} trong {secs:.0f}s, {res.model_calls} lượt gọi model{enc} "
                f"(kịch bản chuyển vào {dest.parent.name}/)")
            self._runlog(started, script.name, "OK", secs, res.model_calls)
            return True
        finally:
            log.removeHandler(handler)
            self._flog.removeHandler(handler)
            handler.close()
            self._forget_recovered(script.name)
            self.current = None

    def _read_script(self, script: Path) -> str:
        try:
            text = script.read_text(encoding="utf-8-sig").strip()
        except UnicodeDecodeError:
            raise UserError("Kịch bản không phải văn bản UTF-8. Mở bằng Notepad, chọn Lưu thành… với "
                            "mã hoá UTF-8 rồi chép lại.") from None
        except OSError as e:
            raise UserError(f"Không đọc được kịch bản: {e.strerror or e}.") from None
        if not text:
            raise UserError("Kịch bản trống. Viết nội dung kịch bản vào tệp rồi chép lại.")
        return text

    def abandon(self) -> None:
        """Ctrl+C during a run: put the script back where it came from (it is rebuilt next start),
        free the output name and drop any half-written cache file. A video already written is
        kept as <name>.loi.mp4, never deleted."""
        script = self.current
        if script is None:
            return
        _ignore_sigint()
        kept = keep_failed_video(self.output / f"{self._stem}.mp4")
        if kept is not None:
            say(f"Video đang dựng dở được giữ lại: {kept}")
        for x in OUTPUT_SUFFIXES:                # free the name again: the rebuild reuses it
            if x != ".mp4":
                self._remove(self.output / f"{self._stem}{x}")
        self._clean_tmp()
        dest = self.scripts / script.name
        if dest.exists():
            dest = unique(self.scripts, script.stem)
        try:
            os.replace(script, dest)
        except OSError:
            pass
        self._forget_recovered(script.name)
        self._runlog(self._started, script.name, "BỎ DỞ (Ctrl+C)", time.monotonic() - self._t0,
                     self._res.model_calls)
        say(f"Đã bỏ dở {script.name}: kịch bản trả về thư mục kịch bản, lần chạy sau sẽ dựng lại.")
        self.current = None

    # -- bookkeeping
    def _forget_recovered(self, name: str) -> None:
        names = self._recovered()
        if name in names:
            names.discard(name)
            self._set_recovered(names)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _archive(self, script: Path, folder: Path, stem: str | None = None) -> Path:
        """Archived under the video's name when possible (tap-nguc-2.txt <-> tap-nguc-2.mp4)."""
        dest = unique(folder, stem or script.stem)
        os.replace(script, dest)
        return dest

    def _to_failed(self, script: Path, stage: str, what: str, todo: str, started: float | None,
                   logfile: Path | None = None, calls: int = 0, t0: float | None = None,
                   stem: str | None = None, kept: Path | None = None) -> None:
        dest = self._archive(script, self.failed, stem)
        reason = dest.with_name(dest.stem + ".loi.txt")
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"Thời gian: {when}", f"Kịch bản: {script.name}", f"Bước lỗi: {stage}",
                 f"Lỗi: {what}", f"Cách xử lý: {todo}"]
        if kept:
            lines.append(f"Video lỗi (giữ lại): {kept}")
        if logfile:
            lines.append(f"Log đầy đủ: {logfile}")
        lines.append("Sửa xong, chép kịch bản từ thư mục failed vào lại thư mục kịch bản để dựng lại.")
        reason.write_text("\n".join(lines) + "\n", encoding="utf-8")
        bar = "=" * 72
        console.emit("\n".join(["", bar, f"!! LỖI  {when}  {script.name}", f"   Bước lỗi:   {stage}",
                                f"   Lỗi:        {what}", f"   Cách xử lý: {todo}",
                                f"   Kịch bản đã chuyển vào: {dest}", f"   Lý do ghi tại:          {reason}",
                                bar, ""]))
        secs = time.monotonic() - t0 if t0 is not None else 0.0
        self._runlog(datetime.now().timestamp() if started is None else started, script.name,
                     f"LỖI ({stage})", secs, calls)

    def _runlog(self, started: float, name: str, result: str, secs: float, calls: int) -> None:
        line = (f"{datetime.fromtimestamp(started):%Y-%m-%d %H:%M:%S} | {name} | {result} | "
                f"{secs:.0f}s | {calls} lượt gọi model\n")
        with open(self.logs / "runs.log", "a", encoding="utf-8") as f:
            f.write(line)

    # -- main loop
    def loop(self, stop: threading.Event) -> None:
        self._stop = stop
        self._scan()
        idle_said = False
        while not stop.is_set():
            if self.editing.is_set():           # the next build must see what is being typed now
                stop.wait(self.poll)
                continue
            claimed = next((c for c in map(self.claim, self.ready()) if c is not None), None)
            if claimed is None:
                if not idle_said and not self.seen:
                    say(f"Đang chờ kịch bản mới trong {self.scripts} (Ctrl+C để dừng)")
                    self.hint()
                    idle_said = True
                stop.wait(self.poll)
                continue
            self.build(claimed)
            idle_said = False

    # -- keys (called from the key listener thread)
    def hint(self) -> None:
        if self.keys_on:
            console.emit(f"           {HINT}")

    def on_key(self, ch: str, ask) -> None:
        k = ch.lower()
        if k == "s":
            self.edit_style(ask)
        elif k == "p":
            self.show_paths()
        elif k == "q":
            self.request_quit(ask)

    def show_paths(self) -> None:
        lines = ["Đang dùng:"]
        for key, label in QUESTIONS:
            lines.append(f"  {label + ':':24} {getattr(self, key)}")
        lines.append(f"  {'Cấu hình:':24} {self.env.pcfg.path}")
        lines.append(f"  Trong thư mục xuất còn có: {', '.join(WORK_DIRS)}")
        console.emit("\n".join(lines))
        if self.current is None:
            self.hint()

    def request_quit(self, ask) -> None:
        if self._stop is None:
            return
        building = self.current
        if building is None:
            say("Thoát theo yêu cầu (Q).")
            self._stop.set()
            return
        console.hold()
        try:
            if _yes(f"Đang dựng {building.name}. Thoát sau khi dựng xong?", ask=ask, out=console.direct):
                self._stop.set()
                console.direct(f"Sẽ thoát sau khi dựng xong {building.name}.")
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            console.release()

    def edit_style(self, ask) -> bool:
        """Key S. Never touches a running build: it keeps the config it started with; the saved
        values are used from the next build on. Returns True when config.yaml was saved."""
        from . import style_edit
        from .brief import load_project_config
        out = console.direct
        building = self.current
        self.editing.set()
        console.hold()
        try:
            if building is not None:
                out(f"Đang dựng {building.name}. Phong cách mới chỉ áp dụng từ kịch bản sau; "
                    "bản đang dựng giữ phong cách cũ.")
                if not _yes("Tiếp tục sửa?", ask=ask, out=out):
                    return False
            path = self.env.pcfg.path
            snap = style_edit.snapshot(path, self.env.pcfg.catalogue)
            new = style_edit.edit(snap, ask, out)
            if new is None:
                return False
            if not _yes(f"Lưu vào {path.name}?", ask=ask, out=out):
                out(f"Không lưu: {path.name} giữ nguyên.")
                return False
            try:
                style_edit.save(snap, new, validate=load_project_config)
            except style_edit.ChangedOnDisk:
                out(f"{path.name} đã bị sửa ở nơi khác sau khi mở trình sửa này.")
                if not _yes("Vẫn ghi 4 giá trị này vào bản mới nhất? (các thay đổi khác trong tệp được giữ)",
                            ask=ask, default=False, out=out):
                    out(f"Không lưu: {path.name} giữ nguyên.")
                    return False
                style_edit.save(snap, new, overwrite_changed=True, validate=load_project_config)
            self.env.pcfg = load_project_config(path)     # a new object: a running build keeps its own
            when = "từ kịch bản sau" if self.current is not None else "từ kịch bản tiếp theo"
            out(f"Đã lưu vào {path}. Áp dụng {when}.")
            return True
        except UserError as e:
            out(f"Lỗi: {e.message} ({self.env.pcfg.path.name} giữ nguyên.)")
            return False
        except (EOFError, KeyboardInterrupt):
            out("\nĐã huỷ sửa phong cách.")
            return False
        finally:
            console.release()
            self.editing.clear()
            if self.current is None:
                self.hint()


class KeyListener(threading.Thread):
    """Polls the key reader (never blocks the watch loop) and hands keys to the watcher. A broken
    reader turns the keys off; it never stops the watcher."""

    def __init__(self, watcher: Watcher, reader, stop: threading.Event, ask=input, tick: float = 0.1):
        super().__init__(name="videoauto-keys", daemon=True)
        self.watcher, self.reader, self.stop, self.ask, self.tick = watcher, reader, stop, ask, tick

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                ch = self.reader.poll()
                if ch:
                    self.watcher.on_key(ch, self.ask)
                    continue
            except Exception as e:  # noqa: BLE001
                log.warning("phím tắt bị tắt do lỗi: %s", e)
                self.watcher.keys_on = False
                return
            self.stop.wait(self.tick)


def describe_failure(e: BaseException, stage: str, logfile: Path) -> tuple[str, str]:
    """(what failed, what to do), Vietnamese, no traceback."""
    if isinstance(e, LimitReached):
        return e.message, ("Chờ đến khi hết giới hạn" + (f" ({e.retry_hint})" if e.retry_hint else "")
                           + ", rồi chép kịch bản vào lại thư mục kịch bản. Video đã lập chỉ mục không "
                             "tốn lượt gọi nào.")
    if isinstance(e, PlanRejected):
        return e.message, ("Thêm cảnh quay phù hợp vào thư mục video nguồn hoặc sửa kịch bản cho khớp "
                           "với cảnh đang có.")
    if isinstance(e, UserError):
        if stage == "build":
            return e.message, f"Xem tệp .build.json cạnh video trong thư mục xuất và log {logfile.name}."
        return e.message, "Làm theo hướng dẫn trong thông báo lỗi ở trên."
    return (f"Lỗi không mong đợi: {type(e).__name__}: {e}",
            f"Có thể là lỗi của videoauto. Gửi tệp log {logfile} khi báo lỗi.")


# ---------------------------------------------------------------- entry point

def main(argv: list[str] | None = None, provider=None, occ=None, stop: threading.Event | None = None,
         settle: float = SETTLE_SECONDS, poll: float = POLL_SECONDS, keys=None, ask=None) -> int:
    """keys/ask: a key reader and an input function (tests). By default the console's own."""
    ap = argparse.ArgumentParser(prog="python -m videoauto.watch",
                                 description="Chạy liên tục: dựng một video cho mỗi kịch bản (.txt) mới "
                                             "trong thư mục kịch bản.")
    ap.add_argument("--no-prompt", action="store_true",
                    help="không hỏi gì, dùng đường dẫn đã lưu (khi chạy lúc đăng nhập / dạng dịch vụ)")
    ap.add_argument("--reset", action="store_true", help=f"quên các đường dẫn đã lưu ({LOCAL_FILE})")
    ap.add_argument("--paths", action="store_true", help="in các đường dẫn đã lưu rồi thoát")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (mặc định: trong thư mục hiện tại)")
    ap.add_argument("--vision-config", type=Path, default=None, help="videoauto.toml (tuỳ chọn)")
    ap.add_argument("-v", "--verbose", action="store_true", help="in cả log chi tiết ra màn hình")
    args = ap.parse_args(argv)
    setup_io(args.verbose)
    if not args.verbose:      # the console shows status lines; details go to .logs/
        for h in logging.getLogger().handlers:
            if type(h) is logging.StreamHandler:
                h.setLevel(logging.WARNING)

    if args.paths:
        return _print_paths()
    if args.reset:
        f = local_file()
        if f.exists():
            f.unlink()
            print(f"Đã quên các đường dẫn đã lưu ({f}).")
        else:
            print("Chưa có đường dẫn nào được lưu.")

    no_prompt = args.no_prompt or not _stdin_is_tty()
    try:
        why = None if no_prompt else first_setup(args.config)
        if why:
            # fresh clone: ask the folders first (they are what a new user has to give), then check
            print(f"Thiết lập lần đầu ({why}).")
            paths = setup_paths(no_prompt)
            env = check_prerequisites(args.config, args.vision_config, provider, occ)
        else:
            env = check_prerequisites(args.config, args.vision_config, provider, occ)
            paths = setup_paths(no_prompt)
    except SetupExit as e:
        print(e.message, file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("\nĐã dừng.", file=sys.stderr)
        return 130

    w = Watcher(paths, env, settle=settle, poll=poll)
    stop = stop or threading.Event()
    try:
        w.prepare()
        w.start_observer()
        say(f"Bắt đầu theo dõi. Mỗi kịch bản .txt mới trong {w.scripts} sẽ thành một video trong {w.output}.")
        if not no_prompt:                       # a scheduled task / service has no keyboard
            _start_keys(w, stop, keys, ask)
        w.loop(stop)
    except KeyboardInterrupt:
        _ignore_sigint()
        print("", flush=True)
        say("Đang dừng…")
        w.abandon()
    finally:
        w.stop_observer()
    say("Đã dừng theo dõi.")
    return 0


def _start_keys(w: Watcher, stop: threading.Event, keys, ask) -> None:
    if keys is None:
        from .keys import console_keys
        try:
            keys = console_keys()
        except Exception:  # noqa: BLE001 - keys are a convenience, never a reason to stop
            keys = None
    if keys is None:
        say("Phím tắt (S/P/Q) không dùng được trên hệ điều hành này; vẫn theo dõi bình thường. "
            "Sửa phong cách trực tiếp trong config.yaml.")
        return
    w.keys_on = True
    KeyListener(w, keys, stop, ask=ask or input).start()


def _stdin_is_tty() -> bool:
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform == "win32" and sys.stdin is sys.__stdin__:
        # NUL (Task Scheduler, services, `< nul`) also claims isatty(): ask for a real console
        import ctypes
        import msvcrt
        try:
            handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        except (OSError, ValueError):
            return False
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def _ignore_sigint() -> None:
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)       # a second Ctrl+C must not break the cleanup
    except ValueError:                                       # not the main thread (tests)
        pass


def _print_paths() -> int:
    saved = load_saved()
    if not saved:
        print(f"Chưa lưu đường dẫn nào ({local_file()}). Chạy python -m videoauto.watch để thiết lập.")
        return 0
    print(f"Đường dẫn đã lưu ({local_file()}):")
    for key, label in QUESTIONS:
        print(f"  {label + ':':24} {saved.get(key, '(chưa có)')}")
    if saved.get("output"):
        print(f"  Trong thư mục xuất còn có: {', '.join(WORK_DIRS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
