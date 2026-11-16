"""Letterbox / pillarbox detection (ffmpeg cropdetect). Analysis runs on the picture area only,
so black bars neither dilute shot detection nor skew black/blur/motion measurements."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tools import run


@dataclass(frozen=True)
class Crop:
    w: int
    h: int
    x: int
    y: int

    @property
    def filter(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


def detect_crop(path: Path, width: int, height: int) -> Crop | None:
    """Union of the non-black area over the whole video (reset=0). None if no real bars."""
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:v:0",
               "-vf", "fps=2,cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
              file=path).stderr.decode("utf-8", "replace")
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", err)
    if not found:
        return None
    w, h, x, y = map(int, found[-1])
    if w <= 0 or h <= 0:
        return None   # entirely black video: leave it to blackdetect
    if w * h > 0.95 * width * height:
        return None
    if w < 0.2 * width or h < 0.2 * height:
        return None   # implausible: probably a mostly-black video with a small bright detail
    return Crop(w, h, x, y)


def vf(crop: Crop | None, rest: str) -> str:
    return f"{crop.filter},{rest}" if crop else rest
