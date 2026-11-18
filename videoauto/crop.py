"""Two kinds of crop.

1. Letterbox / pillarbox detection in the SOURCE (ffmpeg cropdetect), used while indexing: analysis
   runs on the picture area only, so black bars neither dilute shot detection nor skew
   black/blur/motion measurements.
2. Filling the TARGET frame (fill_scale), used by the builder: 16:9 footage in a 9:16 video is
   scaled up until it covers the frame and cropped at the centre, instead of being shown small
   between black bars (brief.fit, README)."""

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


def fill_scale(src_w: int, src_h: int, dst_w: int, dst_h: int) -> float:
    """OpenChatCut transform.scale that turns its default fit (whole picture visible, 'contain',
    bars where the shapes differ) into a fill ('cover', centre crop, no bars). Measured on occ:
    a 1920x1080 clip in a 1080x1920 project with scale (16/9)^2 = 3.16 fills the frame and loses
    its left and right edges. 1.0 when the shapes match."""
    contain = min(dst_w / src_w, dst_h / src_h)
    cover = max(dst_w / src_w, dst_h / src_h)
    return cover / contain


def cropped_away(src_w: int, src_h: int, dst_w: int, dst_h: int) -> float:
    """Fraction of the source picture a fill cuts off (0 when the shapes match)."""
    return 1.0 - 1.0 / fill_scale(src_w, src_h, dst_w, dst_h)
