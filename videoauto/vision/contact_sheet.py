"""Labelled 3x3 contact sheet built with ffmpeg (concat + tile, cell numbers via drawtext)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..tools import ffmpeg_filter_path, run

GRID = 3
CELLS = GRID * GRID
CELL = 400

_FONT_CANDIDATES = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf",
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def _font() -> str:
    for p in _FONT_CANDIDATES:
        if p.exists():
            return f"fontfile='{ffmpeg_filter_path(p)}'"
    return "font='Sans'" if sys.platform != "win32" else "font='Arial'"


def build(images: list[Path], out: Path, labels: list[str] | None = None) -> Path:
    """Compose up to 9 images into one sheet; cell i (1-based) holds images[i-1].
    Cells are labelled 1..n unless `labels` (digits only: drawtext-safe) is given."""
    if not 1 <= len(images) <= CELLS:
        raise ValueError(f"contact sheet needs 1..{CELLS} images, got {len(images)}")
    labels = labels or [str(i + 1) for i in range(len(images))]
    if len(labels) != len(images) or not all(lbl.isdigit() for lbl in labels):
        raise ValueError("labels must be digit strings, one per image")
    args = ["ffmpeg", "-v", "error", "-y"]
    for img in images:
        args += ["-i", str(img)]
    for _ in range(CELLS - len(images)):
        args += ["-f", "lavfi", "-i", f"color=c=0x101010:s={CELL}x{CELL}:d=0.04"]
    font = _font()
    parts = []
    for i in range(CELLS):
        chain = (f"[{i}:v]scale={CELL}:{CELL}:force_original_aspect_ratio=decrease,"
                 f"pad={CELL}:{CELL}:(ow-iw)/2:(oh-ih)/2:color=0x101010,setsar=1,format=rgb24")
        if i < len(images):
            chain += (f",drawtext={font}:text='{labels[i]}':x=10:y=6:fontsize=64:"
                      "fontcolor=yellow:borderw=4:bordercolor=black")
        parts.append(chain + f",trim=end_frame=1,setpts=PTS-STARTPTS[c{i}]")
    concat = "".join(f"[c{i}]" for i in range(CELLS))
    graph = ";".join(parts) + f";{concat}concat=n={CELLS}:v=1:a=0,tile={GRID}x{GRID}:padding=4:color=white[out]"
    args += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(out)]
    run(args)
    return out
