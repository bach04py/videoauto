"""Analysis proxy: decode a large source ONCE into a small file, then analyse that.

Indexing used to decode every 4K source three times in full (cropdetect, the frame pass for
shots/motion, black/freeze/silence detection), only to scale each frame down to 320 px. Now one
ffmpeg pass writes a 640 px wide proxy (H.264 ultrafast + the original audio, copied) and those
three passes read the proxy. Shot cuts, motion, black, freeze and silence do not need 4K.
Keyframes for the captions are still taken from the source (sharper images for the model).

NVDEC: with an NVIDIA card (nvcuda.dll / libcuda.so.1 loads and ffmpeg lists cuda) the one decode
runs on the GPU (-hwaccel cuda). If that fails once, the run continues in software.
Sources that are already small (long side <= 1280 px) are analysed directly.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .crop import Crop
from .probe import Probe, probe
from .tools import which

log = logging.getLogger("videoauto")

PROXY_WIDTH = 640
MIN_LONG_SIDE = 1280              # below this the source itself is cheap enough to analyse
CUDA_DRIVER = {"win32": "nvcuda.dll", "linux": "libcuda.so.1"}

_state: dict[str, object] = {}    # per process: whether -hwaccel cuda works


def nvdec_available(platform: str | None = None, loader=None, hwaccels: str | None = None) -> bool:
    """An NVIDIA driver is loadable and ffmpeg was built with cuda."""
    platform = platform or sys.platform
    lib = CUDA_DRIVER.get(platform)
    if not lib:
        return False
    try:
        (loader or ctypes.CDLL)(lib)
    except OSError:
        return False
    if hwaccels is None:
        try:
            hwaccels = subprocess.run([which("ffmpeg"), "-hide_banner", "-hwaccels"], capture_output=True,
                                      text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    return "cuda" in hwaccels.split()


def use_nvdec(mode: str) -> bool:
    """mode: auto | on | off (config [indexing] hwaccel)."""
    if mode == "off" or _state.get("nvdec_failed"):
        return False
    if "nvdec" not in _state:
        _state["nvdec"] = nvdec_available()
        if mode in ("auto", "on"):
            log.info("lập chỉ mục: giải mã GPU (NVDEC) %s", "có" if _state["nvdec"] else "không có, dùng CPU")
    return bool(_state["nvdec"])


def needs_proxy(pr: Probe) -> bool:
    return max(pr.width, pr.height) > MIN_LONG_SIDE


def _cmd(src: Path, out: Path, nvdec: bool) -> list[str]:
    cmd = [which("ffmpeg"), "-v", "error", "-y"]
    if nvdec:
        cmd += ["-hwaccel", "cuda"]
    return cmd + ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale={PROXY_WIDTH}:-2",
                  "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-g", "30",
                  "-c:a", "copy", str(out)]


def make_proxy(src: Path, pr: Probe, workdir: Path, hwaccel: str = "auto") -> tuple[Path, Probe] | None:
    """(proxy path, its probe with the source's fps/duration), or None to analyse the source."""
    if not needs_proxy(pr):
        return None
    out = workdir / f"{src.stem}.proxy.mkv"
    for nvdec in ([True, False] if use_nvdec(hwaccel) else [False]):
        proc = subprocess.run(_cmd(src, out, nvdec), capture_output=True)
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            try:
                pp = probe(out)
            except Exception:  # noqa: BLE001 - a bad proxy only means: analyse the source
                break
            return out, replace(pr, width=pp.width, height=pp.height)
        if nvdec:
            _state["nvdec_failed"] = True
            log.warning("lập chỉ mục: giải mã GPU (NVDEC) lỗi, chuyển sang CPU: %s",
                        proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or "")
    log.warning("lập chỉ mục: không tạo được bản thu nhỏ của %s; phân tích trực tiếp tệp gốc (chậm hơn)", src.name)
    out.unlink(missing_ok=True)
    return None


def to_source(crop: Crop | None, proxy: Probe, source: Probe) -> Crop | None:
    """A crop box measured on the proxy, in source pixels (even numbers, inside the frame)."""
    if crop is None:
        return None
    fx, fy = source.width / proxy.width, source.height / proxy.height

    def even(v: float) -> int:
        return max(0, int(round(v / 2)) * 2)

    w, h = min(even(crop.w * fx), source.width), min(even(crop.h * fy), source.height)
    x, y = min(even(crop.x * fx), source.width - w), min(even(crop.y * fy), source.height - h)
    return Crop(w, h, x, y)
