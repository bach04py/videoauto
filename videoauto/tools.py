"""Locating and running external tools (ffmpeg, ffprobe)."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .errors import ToolMissing, UnreadableFile

_INSTALL_HINT = "Cài bằng lệnh: winget install Gyan.FFmpeg, rồi mở terminal mới và chạy lại."


@lru_cache(maxsize=None)
def which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissing(f"Không tìm thấy {name} trong PATH. {_INSTALL_HINT}")
    return path


def check_ffmpeg() -> None:
    which("ffmpeg")
    which("ffprobe")


def run(args: list[str], *, file: Path | None = None, check: bool = True,
        input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run a tool; on failure raise UnreadableFile naming `file` (if given)."""
    exe = which(args[0])
    proc = subprocess.run([exe, *args[1:]], input=input, capture_output=True)
    if check and proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        if file is not None:
            reason = tail[0].replace(f"{file}: ", "").replace(str(file), file.name)
            raise UnreadableFile(
                f"Không đọc được tệp '{file.name}' ({reason[:160]}). "
                "Kiểm tra tệp có bị hỏng hoặc có đúng là video/âm thanh không."
            )
        raise RuntimeError(f"{args[0]} failed: {tail[0]}")
    return proc


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a path for use as a filter option value (e.g. drawtext fontfile)."""
    p = str(path).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")
