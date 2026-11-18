"""Clips with a rotation tag, made upright once: <index dir>/normalized/<sha256>.mp4

A phone or camera that records portrait often stores landscape pixels plus a rotation in the
stream's side data. Whether the picture comes out upright then depends on every decoder on the way
reading that tag. One ordinary re-encode by ffmpeg, which applies the tag itself (no
-noautorotate, no transpose guessed here), stores upright pixels and no tag, so nothing downstream
has to read it. (Measured 2026-11-18: the pinned OpenChatCut did render tagged H.264 clips upright,
a synthetic one and a tagged Sony RX10M3 4K clip; this is a safeguard. Footage recorded portrait
with NO tag, as that camera does, is not touched: there is nothing to apply. README, Troubleshooting.)
The indexer analyses the copy and the builder imports it; the original stays the clip's identity
(its sha256 names the copy, keys the cache and is what the plan checks).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .errors import UnreadableFile
from .probe import Probe, probe
from .tools import run

log = logging.getLogger("videoauto")

DIR = "normalized"


def rotation(pr: Probe) -> int:
    """The tag in degrees, 0 when there is none (or a full turn)."""
    return pr.rotation % 360


def path_for(index_dir: Path, sha: str) -> Path:
    return index_dir / DIR / f"{sha}.mp4"


def ensure(src: Path, sha: str, index_dir: Path, pr: Probe | None = None) -> Path:
    """The upright copy of `src`, made once (cached by the original's sha256)."""
    out = path_for(index_dir, sha)
    if out.is_file() and out.stat().st_size > 0:
        return out
    pr = pr or probe(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    log.info("  chuẩn hoá %s: thẻ xoay %d° → %s (encode lại một lần thành hình đứng thẳng, không còn thẻ xoay)",
             src.name, pr.rotation, out.relative_to(index_dir))
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(tmp)]
    try:
        run(args, file=src)
        got = probe(tmp)
        if got.rotation % 360 or (got.width, got.height) != (pr.width, pr.height):
            raise UnreadableFile(f"Chuẩn hoá '{src.name}' (thẻ xoay {pr.rotation}°) cho ra {got.width}x{got.height} "
                                 f"xoay {got.rotation}°, không phải {pr.width}x{pr.height} đứng thẳng. "
                                 "Thử cập nhật ffmpeg (winget upgrade Gyan.FFmpeg).")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out
