"""Quality checks: ffmpeg blackdetect / freezedetect / silencedetect, OpenCV blur."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .crop import Crop, vf
from .tools import run

Interval = tuple[float, float]


@dataclass
class Detections:
    black: list[Interval] = field(default_factory=list)
    frozen: list[Interval] = field(default_factory=list)
    silent: list[Interval] = field(default_factory=list)


def _pair(starts: list[float], ends: list[float], duration: float) -> list[Interval]:
    out = []
    for i, s in enumerate(starts):
        out.append((s, ends[i] if i < len(ends) else duration))
    return out


def detect(path: Path, duration: float, has_audio: bool, silence_db: float,
           crop: Crop | None = None) -> Detections:
    args = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:v:0",
            "-vf", vf(crop, "scale=320:-2,blackdetect=d=0.2:pix_th=0.10,freezedetect=n=-60dB:d=1")]
    if has_audio:
        args += ["-map", "0:a:0", "-af", f"silencedetect=n={silence_db}dB:d=0.5"]
    args += ["-f", "null", "-"]
    err = run(args, file=path).stderr.decode("utf-8", "replace")

    num = r"(-?[\d.]+)"
    black = [(float(a), float(b)) for a, b in re.findall(rf"black_start:{num} black_end:{num}", err)]
    frozen = _pair([float(x) for x in re.findall(rf"freeze_start: {num}", err)],
                   [float(x) for x in re.findall(rf"freeze_end: {num}", err)], duration)
    if has_audio:
        silent = _pair([float(x) for x in re.findall(rf"silence_start: {num}", err)],
                       [float(x) for x in re.findall(rf"silence_end: {num}", err)], duration)
    else:
        silent = [(0.0, duration)]
    return Detections(black, frozen, silent)


def coverage(intervals: list[Interval], start: float, end: float) -> float:
    if end <= start:
        return 0.0
    covered = sum(max(0.0, min(b, end) - max(a, start)) for a, b in intervals)
    return min(1.0, covered / (end - start))


def is_blank(image_path: Path) -> bool:
    """Uniform dark frame (no title text, no detail): safe to caption locally."""
    img = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_GRAYSCALE)
    return img is None or (float(img.std()) < 3.0 and float(img.mean()) < 30.0)


def sharpness(image_path: Path) -> float:
    """Variance of Laplacian on the image resized to 480 px wide (resolution-independent)."""
    img = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    img = cv2.resize(img, (480, max(1, round(480 * h / w))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())
