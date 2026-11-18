"""How long the video is, whether the footage can fill it, how to cut it, and how long the render
will take.

Length: the end of the script's last timed beat ("12-15s | ...", "2:45-3:00 | ..."). The script is
what the user wrote for this video, so a 30-second script never runs with a 15-second config.
brief.duration in config.yaml is only the fallback, for a script without timestamps or when the
model writes the script. 5 to 180 seconds.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
from pathlib import Path

from .errors import UserError

log = logging.getLogger("videoauto")

MIN_SECONDS, MAX_SECONDS = 5, 180

_T = r"(?:\d{1,2}:\d{2}(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
# a timed beat at the start of a line: "0-3s", "12 - 15 giây", "0:00-0:03", "[1:30–1:45]", "2:45-3:00 |"
_BEAT = re.compile(rf"^\s*\[?\s*(?P<a>{_T})\s*(?:s|giây)?\s*[-–—]\s*(?P<b>{_T})\s*(?P<unit>s\b|giây|gi\b)?\s*(?P<after>.?)",
                   re.IGNORECASE)


def _seconds(t: str) -> float:
    t = t.replace(",", ".")
    if ":" in t:
        m, s = t.split(":", 1)
        return int(m) * 60 + float(s)
    return float(t)


def script_end(script: str) -> float | None:
    """End of the last timed beat, or None when the script has no timestamps. A range only counts
    as a beat when it is clearly a time: m:ss, a unit (s / giây), or followed by | ] or :."""
    ends = []
    for line in script.splitlines():
        m = _BEAT.match(line)
        if not m:
            continue
        clock = ":" in m.group("a") or ":" in m.group("b")
        if not (clock or m.group("unit") or m.group("after") in ("|", "]", ":")):
            continue
        a, b = _seconds(m.group("a")), _seconds(m.group("b"))
        if b > a:
            ends.append(b)
    return max(ends) if ends else None


def effective(script: str, fallback: float) -> tuple[float, str]:
    """(seconds, where it came from). Raises UserError outside 5-180 s."""
    end = script_end(script) if script else None
    if end is None:
        why = ("brief.duration trong config.yaml (kịch bản không có mốc thời gian)" if script
               else "brief.duration trong config.yaml (model tự viết kịch bản)")
        return fallback, why
    if not MIN_SECONDS <= end <= MAX_SECONDS:
        raise UserError(f"Kịch bản kết thúc ở giây {end:g}: video phải dài từ {MIN_SECONDS} đến {MAX_SECONDS} giây "
                        f"(3 phút). Sửa mốc thời gian của nhịp cuối trong kịch bản.")
    return end, "mốc thời gian nhịp cuối của kịch bản"


# ---------------------------------------------------------------- footage before any model call

def need_seconds(duration: float, ratio: float) -> float:
    return ratio * duration


def shortage(duration: float, ratio: float, have: float, what: str) -> str | None:
    need = need_seconds(duration, ratio)
    if have >= need:
        return None
    return (f"Không đủ footage: cần ít nhất {need:.0f} giây footage dùng được cho video {duration:.0f} giây, "
            f"hiện có {have:.0f} giây ({what}). Thêm clip vào thư mục video hoặc rút ngắn kịch bản.")


def raw_footage_seconds(paths: list[Path], enough: float) -> float:
    """Total length of the video files, stopping once `enough` is reached (one ffprobe per file)."""
    from .probe import probe
    total = 0.0
    for p in paths:
        try:
            total += probe(p).duration
        except UserError:
            continue
        if total >= enough:
            break
    return total


# ---------------------------------------------------------------- cutting rhythm

def cut_plan(duration: float) -> dict:
    """Average clip length, allowed range and clip count for a video of this length. Longer
    videos get longer average clips and a wider range: 60 equal cuts in 3 minutes do not watch."""
    if duration <= 20:
        avg, lo, hi = 2.5, 1.2, 5.0
    elif duration <= 45:
        avg, lo, hi = 3.0, 1.2, 6.0
    elif duration <= 90:
        avg, lo, hi = 3.5, 1.5, 8.0
    else:
        avg, lo, hi = 4.0, 1.5, 10.0
    n = duration / avg
    return {"avg": avg, "min": lo, "max": hi, "count": round(n),
            "count_lo": max(1, round(n * 0.75)), "count_hi": max(2, round(n * 1.3))}


def cut_rules(duration: float) -> str:
    c = cut_plan(duration)
    return (f"Clips {c['min']:g}-{c['max']:g} s long, about {c['count_lo']}-{c['count_hi']} clips in total "
            f"(average near {c['avg']:g} s).\n"
            "   Vary the length with the content: hold wide, establishing, slow or text-carrying shots longer;\n"
            "   cut action, detail and fast-moving shots shorter. NEVER give every clip the same length (for\n"
            "   example all 3.0 s): a run of equal cuts feels mechanical, and the longer the video the worse it gets.")


def monotony(lengths: list[float]) -> str | None:
    """Warning when the clips are (nearly) all the same length."""
    if len(lengths) < 5:
        return None
    spread = max(lengths) - min(lengths)
    if spread >= 0.5:
        return None
    return (f"{len(lengths)} clip dài gần bằng nhau ({min(lengths):.1f}-{max(lengths):.1f}s): nhịp cắt đều, đơn điệu; "
            "độ dài clip nên thay đổi theo cảnh")


# ---------------------------------------------------------------- render time

RENDER_LOG = "render-times.json"
DEFAULT_RATIO = 21.0          # measured: 314 s of render for a 15 s video with NVENC
KEEP = 20


def _records(data_dir: Path) -> list[dict]:
    try:
        rows = json.loads((data_dir / RENDER_LOG).read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("video", 0) > 0 and r.get("render", 0) > 0]
    except (OSError, ValueError, TypeError):
        return []


def render_ratio(data_dir: Path) -> tuple[float, str]:
    rows = [r for r in _records(data_dir) if r["video"] >= 5]        # 1 s checks are all start-up cost
    if not rows:
        return DEFAULT_RATIO, "ước lượng mặc định: 314 giây cho video 15 giây"
    return (statistics.median(r["render"] / r["video"] for r in rows),
            f"trung vị {len(rows)} lần render trước trên máy này")


def render_estimate(video_seconds: float, data_dir: Path) -> str:
    ratio, basis = render_ratio(data_dir)
    secs = ratio * video_seconds
    when = f"{secs / 60:.0f} phút" if secs >= 90 else f"{secs:.0f} giây"
    return (f"ước tính thời gian render: khoảng {when} cho video {video_seconds:g} giây "
            f"(~{ratio:.0f}× thời lượng, {basis})")


def record_render(data_dir: Path, video_seconds: float, render_seconds: float) -> None:
    rows = _records(data_dir)[-(KEEP - 1):] + [{"video": round(video_seconds, 2), "render": round(render_seconds, 1)}]
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = data_dir / (RENDER_LOG + ".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        os.replace(tmp, data_dir / RENDER_LOG)
    except OSError as e:
        log.debug("không ghi được %s: %s", RENDER_LOG, e)
