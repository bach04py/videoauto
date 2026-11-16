"""One decoding pass over a video (ffmpeg pipe, any codec) feeding:
shot detection (PySceneDetect ContentDetector), motion level and camera movement (OpenCV).

Camera movement: each sampled frame pair is split into a 3x3 grid and every tile's shift is
measured by phase correlation. A camera move shifts ALL tiles coherently (pan/tilt: same shift;
zoom: shifts radiating from the centre), so we fit  shift_i = t + s * pos_i  over the tiles and
only trust samples where most tiles agree with the fit. Clouds drifting in a timelapse, or one
subject moving in a static frame, move only some tiles and are not mistaken for a pan.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scenedetect import ContentDetector
from scenedetect.common import FrameTimecode

from .crop import Crop, vf
from .errors import UnreadableFile
from .probe import Probe
from .tools import which

ANALYSIS_WIDTH = 320
SAMPLE_FPS = 6.0
GRID = 3


@dataclass
class MotionSample:
    frame_a: int
    frame_b: int
    diff: float            # mean abs grey difference (0-255)
    tx: float = 0.0        # global translation, fraction of frame width
    ty: float = 0.0        # fraction of frame height
    zoom: float = 0.0      # scale change per sample (≈ log scale)
    coherent: bool = False


@dataclass
class FrameAnalysis:
    fps: float
    n_frames: int
    cuts: list[int]
    samples: list[MotionSample] = field(default_factory=list)


def _tile_fit(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float, bool]:
    h, w = a.shape
    th, tw = h // GRID, w // GRID
    win = cv2.createHanningWindow((tw, th), cv2.CV_32F)
    rows, obs = [], []
    for gy in range(GRID):
        for gx in range(GRID):
            ta = a[gy * th:(gy + 1) * th, gx * tw:(gx + 1) * tw]
            tb = b[gy * th:(gy + 1) * th, gx * tw:(gx + 1) * tw]
            if ta.std() < 3 and tb.std() < 3:
                continue   # flat tile (sky, black): no information
            (sx, sy), resp = cv2.phaseCorrelate(ta, tb, win)
            if resp < 0.1:
                continue
            cx, cy = (gx + 0.5) / GRID - 0.5, (gy + 0.5) / GRID - 0.5
            rows += [[1, 0, cx], [0, 1, cy]]
            obs += [sx / w, sy / h]
    if len(obs) < 8:   # fewer than 4 informative tiles
        return 0.0, 0.0, 0.0, False
    A, y = np.array(rows), np.array(obs)
    (tx, ty, s), *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = np.hypot(*(y - A @ np.array([tx, ty, s])).reshape(-1, 2).T)
    tol = max(0.004, 0.35 * math.hypot(tx, ty))
    coherent = float(np.mean(resid < tol)) >= 0.7
    return float(tx), float(ty), float(s), coherent


def analyse(path: Path, pr: Probe, crop: Crop | None, threshold: float, min_scene_len: int) -> FrameAnalysis:
    src_w, src_h = (crop.w, crop.h) if crop else (pr.width, pr.height)
    w = ANALYSIS_WIDTH
    h = max(2, int(round(w * src_h / src_w / 2)) * 2)
    cmd = [which("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:v:0", "-an",
           "-vf", vf(crop, f"scale={w}:{h}"), "-fps_mode", "cfr", "-r", str(pr.fps),
           "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = w * h * 3
    step = max(1, round(pr.fps / SAMPLE_FPS))
    detector = ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
    cuts: list[int] = []
    samples: list[MotionSample] = []
    prev: np.ndarray | None = None
    prev_idx = -1
    i = 0
    err = ""
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            cuts += [c.frame_num for c in detector.process_frame(FrameTimecode(i, fps=pr.fps), img)]
            if i % step == 0:
                grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
                if prev is not None:
                    diff = float(np.mean(np.abs(grey - prev)))
                    tx, ty, s, ok = _tile_fit(prev, grey)
                    samples.append(MotionSample(prev_idx, i, diff, tx, ty, s, ok))
                prev, prev_idx = grey, i
            i += 1
        cuts += [c.frame_num for c in detector.post_process(FrameTimecode(i, fps=pr.fps))]
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if i == 0:
        raise UnreadableFile(f"Không giải mã được khung hình nào từ '{path.name}'. {err.strip()[:160]}")
    cuts = sorted({c for c in cuts if 0 < c < i})
    return FrameAnalysis(pr.fps, i, cuts, samples)


def motion_level(samples: list[MotionSample]) -> float:
    """0-1. Mean grey-level change between samples (~6/s), squashed: 1 - exp(-d/10)."""
    if not samples:
        return 0.0
    d = float(np.mean([s.diff for s in samples]))
    return round(1.0 - math.exp(-d / 10.0), 2)


def camera_movement(samples: list[MotionSample], seconds: float) -> str:
    """static / pan / tilt / zoom / handheld. 'tracking' is not distinguishable here and
    is never emitted. Incoherent samples count as no camera motion."""
    if len(samples) < 2 or seconds <= 0:
        return "static"
    coh = [s for s in samples if s.coherent]
    if len(coh) < 0.5 * len(samples):
        return "static"
    dx = np.array([s.tx for s in coh])
    dy = np.array([s.ty for s in coh])
    dz = np.array([s.zoom for s in coh])
    rate_x, rate_y, rate_z = abs(dx.sum()) / seconds, abs(dy.sum()) / seconds, abs(dz.sum()) / seconds

    def consistent(a: np.ndarray) -> bool:
        moving = a[np.abs(a) > 1e-3]
        return len(moving) >= max(2, 0.4 * len(a)) and abs(np.sign(moving).mean()) >= 0.6

    if rate_z > 0.02 and consistent(dz):
        return "zoom"
    if rate_x > 0.03 and rate_x >= 1.5 * rate_y and consistent(dx):
        return "pan"
    if rate_y > 0.03 and rate_y >= 1.5 * rate_x and consistent(dy):
        return "tilt"
    if float(np.mean(np.hypot(dx, dy))) > 0.004:
        return "handheld"
    return "static"
