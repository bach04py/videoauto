"""Run the OpenChatCut `occ` CLI as a subprocess. Nothing from OpenChatCut is imported.

Every call: `node --import tsx cli/main.ts <args> --json`, cwd = the repo (so tsx resolves),
OPENCHATCUT_DATA_DIR = the one configured data dir. Tool arguments go through a temp file
(`--args @file.json`) so JSON never has to survive Windows command-line quoting.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..brief import OccConfig
from ..errors import ToolMissing, UserError

log = logging.getLogger("videoauto")


@dataclass
class OccResult:
    label: str
    argv: list[str]
    ok: bool
    seconds: float
    data: Any = None
    error: str = ""
    tool_args: dict | None = None
    returncode: int | None = None       # None: timed out

    def record(self) -> dict:
        return {"label": self.label, "argv": self.argv, "args": self.tool_args, "ok": self.ok,
                "seconds": round(self.seconds, 2), "error": self.error or None,
                "result": _summary(self.data)}

    def tool_result(self) -> dict:
        return ((self.data or {}).get("ops") or [{}])[0].get("result") or {}


def _summary(data: Any) -> Any:
    if isinstance(data, dict) and "ops" in data:
        return [op.get("result") for op in data["ops"]]
    return data


def parse_stdout(stdout: str) -> Any:
    """The JSON document occ printed with --json, or None.

    `occ render` prints progress lines ("rendering 120/450") before its JSON, so the whole
    stdout is not one JSON document: the JSON is the first line that opens a value and parses
    to the end of the output (one line or pretty-printed)."""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.lstrip()[:1] not in ("{", "["):
            continue
        try:
            return json.loads("\n".join(lines[i:]))
        except ValueError:
            continue
    return None


@dataclass
class Occ:
    cfg: OccConfig
    history: list[OccResult] = field(default_factory=list)

    def problems(self) -> list[str]:
        """Everything that stops occ from running, all at once (empty list: ready)."""
        out = []
        if not (self.cfg.repo / "cli" / "main.ts").exists():
            out.append(f"openchatcut.repo = '{self.cfg.repo}' không phải repo OpenChatCut (thiếu cli/main.ts). "
                       "Chạy python -m videoauto.setup (tự tải đúng bản OpenChatCut cạnh videoauto) hoặc sửa openchatcut.repo "
                       "trong config.yaml.")
        elif not (self.cfg.repo / "node_modules" / "tsx").exists():
            out.append(f"Repo OpenChatCut tại '{self.cfg.repo}' chưa cài thư viện. Chạy: python -m videoauto.setup")
        if not (shutil.which(self.cfg.node) or Path(self.cfg.node).exists()):
            out.append(f"Không tìm thấy Node.js ('{self.cfg.node}'). Cài Node (bản trong .nvmrc của "
                       "OpenChatCut) hoặc đặt openchatcut.node trong config.yaml.")
        return out

    def check(self) -> None:
        found = self.problems()
        if found:
            if any(m.startswith("Không tìm thấy Node.js") for m in found) and len(found) == 1:
                raise ToolMissing(found[0])
            raise UserError(" ".join(found))
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)

    def run(self, label: str, args: list[str], tool_args: dict | None = None,
            timeout: int = 300, read: bool = False, env: dict[str, str] | None = None,
            json_required: bool = True) -> OccResult:
        """read=True for read-only tools (read_timeline): they report applied=false and no
        "ok" key, which is success for a read; only an "error" in the result fails it.
        env: extra environment variables for this call only (the render's encoder choice).
        json_required=False (render): exit 0 is enough here; the caller judges the result by the
        file it wrote, never by whether stdout parsed."""
        argv = [*args, "--json"]
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="videoauto_occ_") as tmp:
            if tool_args is not None:
                f = Path(tmp) / "args.json"
                f.write_text(json.dumps(tool_args, ensure_ascii=False), encoding="utf-8")
                argv = [*args, "--args", f"@{f}", "--json"]
            full_env = {**os.environ, **(env or {}), "OPENCHATCUT_DATA_DIR": str(self.cfg.data_dir)}
            try:
                proc = subprocess.run([self.cfg.node, "--import", "tsx", "cli/main.ts", *argv],
                                      cwd=self.cfg.repo, env=full_env, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=timeout)
            except subprocess.TimeoutExpired:
                res = OccResult(label, args, False, time.monotonic() - t0,
                                error=f"quá thời gian {timeout}s", tool_args=tool_args, returncode=None)
                return self._done(res)
            except OSError as e:
                raise ToolMissing(f"Không chạy được Node.js ('{self.cfg.node}'): {e.strerror}.") from None
        secs = time.monotonic() - t0
        data, err = parse_stdout(proc.stdout), ""
        ok = proc.returncode == 0 and (data is not None or not json_required)
        if ok and tool_args is not None and isinstance(data, dict):
            r = (data.get("ops") or [{}])[0].get("result") or {}
            if read:
                ok = "error" not in r
            else:
                ok = bool(data.get("applied", True)) and r.get("ok", True) is not False
            if not ok:
                err = json.dumps(r, ensure_ascii=False)[:400]
        if not ok and not err:
            lines = [ln for ln in (proc.stderr + "\n" + proc.stdout).splitlines()
                     if ln.strip() and not ln.startswith("rendering ")]
            err = f"exit {proc.returncode}: " + " | ".join(lines[-6:])[:600]
            if proc.returncode == 0 and data is None:
                err += " (occ thoát mã 0 nhưng không in ra JSON kết quả)"
        return self._done(OccResult(label, args, ok, secs, data, err, tool_args, proc.returncode))

    def _done(self, res: OccResult) -> OccResult:
        self.history.append(res)
        if res.ok:
            log.info("  occ %-28s ok    %5.1fs", res.label, res.seconds)
        else:
            log.warning("  occ %-28s LỖI  %5.1fs  %s", res.label, res.seconds, res.error)
        return res
