"""ffprobe metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .errors import UnreadableFile
from .tools import run


@dataclass
class Probe:
    duration: float
    fps: float
    width: int        # display width (rotation applied)
    height: int       # display height
    has_video: bool
    has_audio: bool
    rotation: int = 0  # display rotation tag in degrees (stream side data / rotate tag), 0 = none

    @property
    def orientation(self) -> str:
        if self.width == self.height:
            return "square"
        return "vertical" if self.height > self.width else "horizontal"


def _rate(s: str | None) -> float:
    try:
        f = float(Fraction(s)) if s else 0.0
    except (ValueError, ZeroDivisionError):
        f = 0.0
    return f


def probe(path: Path) -> Probe:
    proc = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
               file=path)
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    if duration <= 0 and video is not None:
        duration = float(video.get("duration") or 0.0)
    if duration <= 0:
        raise UnreadableFile(f"Không xác định được thời lượng của '{path.name}'. Tệp có thể bị hỏng.")

    if video is None:
        return Probe(duration, 0.0, 0, 0, False, audio is not None)

    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if not (0 < fps < 1000):
        fps = 30.0
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    rotation = int(video.get("tags", {}).get("rotate", rotation))
    if abs(rotation) % 180 == 90:
        w, h = h, w
    if w <= 0 or h <= 0:
        raise UnreadableFile(f"Không đọc được kích thước khung hình của '{path.name}'.")
    return Probe(duration, round(fps, 3), w, h, True, audio is not None, rotation)
