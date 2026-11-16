"""Keyframe extraction with ffmpeg: one per shot, a second for shots longer than 8 s."""

from __future__ import annotations

from pathlib import Path

from .crop import Crop, vf
from .tools import run


def keyframe_times(start: float, end: float, second_after: float) -> list[float]:
    length = end - start
    if length > second_after:
        times = [start + length / 3, start + 2 * length / 3]
    else:
        times = [start + length / 2]
    return [round(t, 3) for t in times]


def for_shot(path: Path, span, kcfg: dict, fdir: Path, crop: Crop | None) -> tuple[list[float], list[Path]]:
    """Keyframe times for a shot and their extracted images (<fdir>/<shot id>_<n>.jpg)."""
    times = keyframe_times(span.start, span.end, kcfg["second_frame_after"])
    paths = [extract(path, t, fdir / f"{span.id}_{i}.jpg", kcfg["max_side"], crop)
             for i, t in enumerate(times)]
    return times, paths


def extract(path: Path, t: float, out: Path, max_side: int, crop: Crop | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    scale = vf(crop, f"scale='if(gt(iw,ih),min({max_side},iw),-2)':"
                     f"'if(gt(iw,ih),-2,min({max_side},ih))'")
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", scale, "-q:v", "3", str(out)], file=path)
    if not out.exists():
        # Seeking to the very end can yield no frame; step back a little.
        run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.5", "-i", str(path),
             "-frames:v", "1", "-vf", scale, "-q:v", "3", str(out)], file=path)
    return out
