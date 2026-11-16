"""Default provider: shells out to `claude -p --output-format json` (uses the Claude subscription)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..errors import ProviderUnavailable
from .base import ProviderTransient, VisionProvider, classify


class ClaudeCLIProvider(VisionProvider):
    name = "claude"

    def __init__(self, cfg: dict):
        self.command = cfg.get("command") or "claude"
        self.model = cfg.get("model")
        self.timeout = cfg.get("timeout", 300)
        self._exe: str | None = None

    def check(self) -> None:
        self._exe = shutil.which(self.command)
        if not self._exe:
            raise ProviderUnavailable(
                f"Không tìm thấy lệnh `{self.command}`. Cài Claude Code rồi chạy `claude` và gõ /login, "
                "hoặc đặt vision.provider = \"openai-compatible\" trong videoauto.toml."
            )

    def image_reference(self, image: Path) -> str:
        return f"The image file {image.name} in the current directory (open it with the Read tool)"

    def describe(self, image: Path, prompt: str) -> str:
        return self._run(["--tools", "Read", "--allowedTools", "Read"], prompt, image.parent)

    def complete(self, prompt: str) -> str:
        # no tools at all; run in an empty dir so no project CLAUDE.md is picked up
        with tempfile.TemporaryDirectory(prefix="videoauto_plan_") as d:
            return self._run(["--tools", ""], prompt, Path(d))

    def _run(self, tool_flags: list[str], prompt: str, cwd: Path) -> str:
        if self._exe is None:
            self.check()
        cmd = [self._exe, "-p", "--output-format", "json", *tool_flags,
               "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ProviderTransient(f"claude timed out after {self.timeout}s") from None

        out, err = proc.stdout.strip(), proc.stderr.strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            text = f"{out}\n{err}"
            raise classify("Claude", text) or ProviderTransient(f"claude exit {proc.returncode}: {text[:200]}")

        result = data.get("result") or ""
        if data.get("is_error") or data.get("subtype") not in (None, "success"):
            text = f"{result}\n{data.get('api_error_status', '')}\n{err}"
            raise classify("Claude", text) or ProviderTransient(f"claude error: {text.strip()[:200]}")
        return str(result)
