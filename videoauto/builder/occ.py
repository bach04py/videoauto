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


@dataclass
class Occ:
    cfg: OccConfig
    history: list[OccResult] = field(default_factory=list)

    def check(self) -> None:
        if not (self.cfg.repo / "cli" / "main.ts").exists():
            raise UserError(f"openchatcut.repo = '{self.cfg.repo}' không phải repo OpenChatCut "
                            "(thiếu cli/main.ts). Sửa đường dẫn trong config.yaml.")
        if not (self.cfg.repo / "node_modules" / "tsx").exists():
            raise UserError(f"Repo OpenChatCut chưa cài thư viện. Chạy `npm install` trong '{self.cfg.repo}'.")
        if not (shutil.which(self.cfg.node) or Path(self.cfg.node).exists()):
            raise ToolMissing(f"Không tìm thấy Node.js ('{self.cfg.node}'). Cài Node (bản trong .nvmrc của "
                              "OpenChatCut) hoặc đặt openchatcut.node trong config.yaml.")
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)

    def run(self, label: str, args: list[str], tool_args: dict | None = None,
            timeout: int = 300) -> OccResult:
        argv = [*args, "--json"]
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="videoauto_occ_") as tmp:
            if tool_args is not None:
                f = Path(tmp) / "args.json"
                f.write_text(json.dumps(tool_args, ensure_ascii=False), encoding="utf-8")
                argv = [*args, "--args", f"@{f}", "--json"]
            env = {**os.environ, "OPENCHATCUT_DATA_DIR": str(self.cfg.data_dir)}
            try:
                proc = subprocess.run([self.cfg.node, "--import", "tsx", "cli/main.ts", *argv],
                                      cwd=self.cfg.repo, env=env, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=timeout)
            except subprocess.TimeoutExpired:
                res = OccResult(label, args, False, time.monotonic() - t0,
                                error=f"quá thời gian {timeout}s", tool_args=tool_args)
                return self._done(res)
            except OSError as e:
                raise ToolMissing(f"Không chạy được Node.js ('{self.cfg.node}'): {e.strerror}.") from None
        secs = time.monotonic() - t0
        data, err = None, ""
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            pass
        ok = proc.returncode == 0 and data is not None
        if ok and tool_args is not None:
            r = (data.get("ops") or [{}])[0].get("result") or {}
            ok = bool(data.get("applied", True)) and r.get("ok", True) is not False
            if not ok:
                err = json.dumps(r, ensure_ascii=False)[:400]
        if not ok and not err:
            lines = [ln for ln in (proc.stderr + "\n" + proc.stdout).splitlines()
                     if ln.strip() and not ln.startswith("rendering ")]
            err = f"exit {proc.returncode}: " + " | ".join(lines[-6:])[:600]
        return self._done(OccResult(label, args, ok, secs, data, err, tool_args))

    def _done(self, res: OccResult) -> OccResult:
        self.history.append(res)
        if res.ok:
            log.info("  occ %-28s ok    %5.1fs", res.label, res.seconds)
        else:
            log.warning("  occ %-28s LỖI  %5.1fs  %s", res.label, res.seconds, res.error)
        return res
