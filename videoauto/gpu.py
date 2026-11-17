"""Hardware H.264 encoding for `occ render`: decide, tell OpenChatCut, verify afterwards.

What OpenChatCut does on its own (server/media-acceleration.ts, read 2026-11-17):
- `occ render` has NO encoder flag. For H.264 it picks the encoder itself: on Windows it tries
  h264_nvenc, h264_qsv, h264_amf (macOS: h264_videotoolbox; Linux: nvenc, qsv, vaapi), each with a
  one-frame trial encode, and uses the first that works AND that the renderer's ffmpeg (Remotion's
  compositor build) has compiled in; otherwise libx264. A hardware failure during the render falls
  back to libx264 as well.
- Two environment variables steer it: OPENCHATCUT_DISABLE_HARDWARE_ENCODING=1 forces libx264;
  OPENCHATCUT_H264_ENCODER=<name> limits the candidates to that one (still probed, still falling
  back to libx264).
- `occ render --json` does not report which encoder it used.

So videoauto makes the decision visible and controllable (openchatcut.gpu_encode):
    auto  a hardware encoder is used when it is listed by the renderer's ffmpeg AND a one-second
          trial encode succeeds; otherwise software, with the reason in the log
    on    the same check, but no usable encoder is an error before anything is built
    off   always libx264
and checks the finished file: libx264 writes its version string ("x264 - core …") into the
H.264 stream, hardware encoders do not. That is the evidence recorded as "used" in
<out>.build.json, so a mid-render fallback inside OpenChatCut is caught too.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .errors import UserError

log = logging.getLogger("videoauto")

SOFTWARE = "libx264"
CANDIDATES = {
    "win32": ("h264_nvenc", "h264_qsv", "h264_amf"),
    "darwin": ("h264_videotoolbox",),
    "linux": ("h264_nvenc", "h264_qsv", "h264_vaapi"),
}
LABELS = {"h264_nvenc": "NVIDIA NVENC", "h264_qsv": "Intel Quick Sync", "h264_amf": "AMD AMF",
          "h264_videotoolbox": "Apple VideoToolbox", "h264_vaapi": "VA-API", SOFTWARE: "CPU"}
TRIAL_SECONDS = 1
TRIAL_TIMEOUT = 30
X264_MARK = b"x264 - core"


def _exe(name: str) -> str:
    return name + (".exe" if sys.platform == "win32" else "")


# ---------------------------------------------------------------- the probe (faked in tests)

class Probe(Protocol):
    def listed(self, ffmpeg: str) -> set[str]: ...
    def trial(self, ffmpeg: str, encoder: str) -> str | None: ...      # None = ok, else the error


class FfmpegProbe:
    """Runs ffmpeg for real."""

    def listed(self, ffmpeg: str) -> set[str]:
        try:
            p = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return set()
        names = set()
        for ln in p.stdout.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and parts[0].startswith("V"):
                names.add(parts[1])
        return names

    def trial(self, ffmpeg: str, encoder: str) -> str | None:
        pre, vf, pix = [], [], "yuv420p"
        if encoder in ("h264_qsv", "h264_amf"):
            pix = "nv12"
        elif encoder == "h264_vaapi":
            pre = ["-vaapi_device", os.environ.get("OPENCHATCUT_VAAPI_DEVICE") or "/dev/dri/renderD128"]
            vf, pix = ["-vf", "format=nv12,hwupload"], "vaapi"
        argv = [ffmpeg, "-hide_banner", "-loglevel", "error", *pre,
                "-f", "lavfi", "-i", "testsrc2=size=256x256:rate=30", "-t", str(TRIAL_SECONDS),
                *vf, "-c:v", encoder, "-pix_fmt", pix, "-b:v", "2M", "-f", "null", "-"]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=TRIAL_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"thử mã hoá quá {TRIAL_TIMEOUT}s"
        except OSError as e:
            return f"không chạy được ffmpeg: {e.strerror or e}"
        if p.returncode == 0:
            return None
        rc = p.returncode - (1 << 32) if p.returncode >= 1 << 31 else p.returncode   # Windows: unsigned
        lines = [ln.strip().rstrip(".") for ln in p.stderr.splitlines() if ln.strip()]
        return f"exit {rc}: " + (" | ".join(lines[:2])[:300] if lines else "(không có thông báo)")


DEFAULT_PROBE: Probe = FfmpegProbe()


# ---------------------------------------------------------------- which ffmpeg OpenChatCut uses

def probe_ffmpeg(repo: Path) -> str | None:
    """The ffmpeg OpenChatCut probes encoders with (server/media-binaries.ts ffmpegBin)."""
    for var in ("OPENCHATCUT_FFMPEG", "FFMPEG_PATH"):
        if os.environ.get(var):
            return os.environ[var]
    static = repo / "node_modules" / "ffmpeg-static" / _exe("ffmpeg")
    if static.is_file():
        return str(static)
    return shutil.which("ffmpeg")


def renderer_ffmpeg(repo: Path) -> str | None:
    """The ffmpeg Remotion renders with (its compositor package): the encoder must be compiled
    in there too, or OpenChatCut renders with libx264 whatever the probe said."""
    for var in ("CC_REMOTION_HARDWARE_BINARIES_DIR", "CC_REMOTION_BINARIES_DIR"):
        if os.environ.get(var):
            f = Path(os.environ[var]) / _exe("ffmpeg")
            return str(f) if f.is_file() else None
    plat = {"win32": "win32", "darwin": "darwin", "linux": "linux"}.get(sys.platform, sys.platform)
    found = sorted((repo / "node_modules" / "@remotion").glob(f"compositor-{plat}-*/{_exe('ffmpeg')}"))
    return str(found[0]) if found else None


# ---------------------------------------------------------------- the decision

@dataclass
class EncoderChoice:
    mode: str                              # auto | on | off
    encoder: str                           # what OpenChatCut is told to use
    reason: str                            # Vietnamese, one line
    probes: list[dict] = field(default_factory=list)

    @property
    def hardware(self) -> bool:
        return self.encoder != SOFTWARE

    def env(self) -> dict[str, str]:
        """Environment for `occ render`. Overrides anything inherited from the user's shell."""
        if self.hardware:
            return {"OPENCHATCUT_H264_ENCODER": self.encoder, "OPENCHATCUT_DISABLE_HARDWARE_ENCODING": "0"}
        return {"OPENCHATCUT_DISABLE_HARDWARE_ENCODING": "1", "OPENCHATCUT_H264_ENCODER": ""}

    def label(self) -> str:
        return f"{self.encoder} ({LABELS.get(self.encoder, self.encoder)})"


_cache: dict[tuple, EncoderChoice] = {}


def choose(mode: str, repo: Path, probe: Probe | None = None, platform: str | None = None) -> EncoderChoice:
    """Decide once per process (cached): the trial encodes cost about a second each."""
    probe = probe or DEFAULT_PROBE
    platform = platform or sys.platform
    pf, rf = probe_ffmpeg(repo), renderer_ffmpeg(repo)
    key = (mode, str(repo), pf, rf, platform, id(probe))
    if key not in _cache:
        _cache[key] = _choose(mode, pf, rf, probe, platform)
        c = _cache[key]
        log.info("mã hoá video: %s, gpu_encode=%s. %s", c.label(), c.mode, c.reason)
        for p in c.probes:
            log.info("  thử %s: %s", p["encoder"], p["result"])
    c = _cache[key]
    if mode == "on" and not c.hardware:
        raise UserError("openchatcut.gpu_encode = on nhưng không có bộ mã hoá GPU nào dùng được: "
                        f"{c.reason} Đặt gpu_encode: auto (tự chuyển sang CPU) hoặc off, hoặc cài/cập nhật "
                        "driver GPU.")
    return c


def _choose(mode: str, pf: str | None, rf: str | None, probe: Probe, platform: str) -> EncoderChoice:
    if mode == "off":
        return EncoderChoice(mode, SOFTWARE, "gpu_encode=off: luôn mã hoá bằng CPU.")
    cands = CANDIDATES.get(platform, ())
    if not cands:
        return EncoderChoice(mode, SOFTWARE, f"chưa hỗ trợ mã hoá GPU trên {platform}.")
    if pf is None:
        return EncoderChoice(mode, SOFTWARE, "không tìm thấy ffmpeg để thử bộ mã hoá GPU.")
    have = probe.listed(pf)
    rhave = probe.listed(rf) if rf else None
    probes = []
    for enc in cands:
        if enc not in have:
            probes.append({"encoder": enc, "listed": False, "result": f"ffmpeg ({pf}) không có {enc}"})
            continue
        if rhave is not None and enc not in rhave:
            probes.append({"encoder": enc, "listed": False,
                           "result": f"ffmpeg dựng hình của OpenChatCut ({rf}) không có {enc}"})
            continue
        err = probe.trial(pf, enc)
        probes.append({"encoder": enc, "listed": True, "result": "ok" if err is None else err})
        if err is None:
            return EncoderChoice(mode, enc, f"{LABELS[enc]}: thử mã hoá {TRIAL_SECONDS}s thành công.", probes)
    tried = [p for p in probes if p["listed"]]
    if tried:
        why = "; ".join(f"{p['encoder']}: {p['result']}" for p in tried)
        reason = (f"ffmpeg có bộ mã hoá GPU nhưng thử mã hoá thất bại (không có GPU đó, thiếu driver hoặc "
                  f"driver hỏng): {why}.")
    else:
        reason = "ffmpeg không có bộ mã hoá GPU nào cho máy này."
    return EncoderChoice(mode, SOFTWARE, reason + " Dùng CPU (libx264).", probes)


# ---------------------------------------------------------------- after the render

def written_by_x264(video: Path) -> bool:
    """libx264 stores its version string in an SEI message of the first frame."""
    tail = b""
    try:
        with open(video, "rb") as f:
            while chunk := f.read(1 << 20):
                if X264_MARK in tail + chunk:
                    return True
                tail = chunk[-len(X264_MARK):]
    except OSError:
        return False
    return False


def verify(choice: EncoderChoice, video: Path) -> dict:
    """What actually encoded the file, for <out>.build.json and the run summary."""
    x264 = written_by_x264(video)
    if x264:
        used = SOFTWARE
    elif choice.hardware:
        used = choice.encoder
    else:
        used = "unknown"
    rec = {"mode": choice.mode, "requested": choice.encoder, "used": used,
           "hardware": used not in (SOFTWARE, "unknown"),
           "evidence": "có chuỗi 'x264 - core' trong luồng H.264" if x264
                       else "không có chuỗi x264 trong luồng H.264",
           "reason": choice.reason, "probes": choice.probes}
    if choice.hardware and x264:
        rec["fallback"] = (f"OpenChatCut đã chuyển sang libx264 trong lúc dựng dù {choice.encoder} thử "
                           "mã hoá được (xem log của occ render).")
    elif not choice.hardware and not x264:
        rec["fallback"] = "không xác định được bộ mã hoá: tệp không có dấu hiệu của libx264."
    return rec
