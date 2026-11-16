"""Audio decoding (ffmpeg pipe) and loudness peaks."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .tools import run


def load_audio(path: Path, sr: int) -> np.ndarray:
    """Mono float32 at `sr`. Empty array if the file has no audio stream."""
    proc = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0?", "-vn",
                "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], file=path)
    return np.frombuffer(proc.stdout, np.float32).copy()


def rms_curve(y: np.ndarray, sr: int, hop_seconds: float) -> np.ndarray:
    """RMS per hop (window = 2 hops, centred), plain numpy: no librosa needed for video."""
    hop = max(1, int(round(sr * hop_seconds)))
    if len(y) < hop:
        return np.zeros(0, np.float32)
    padded = np.pad(y.astype(np.float64), hop)  # centre windows like librosa(center=True)
    sq = np.concatenate([[0.0], np.cumsum(padded ** 2)])
    n = len(y) // hop + 1
    starts = np.arange(n) * hop
    energy = (sq[starts + 2 * hop] - sq[starts]) / (2 * hop)
    return np.sqrt(np.maximum(energy, 0.0)).astype(np.float32)


def peak_times(y: np.ndarray, sr: int, max_peaks: int = 8, min_gap: float = 1.0) -> list[float]:
    """Loudest moments: local maxima of the RMS envelope, at least `min_gap` s apart."""
    hop_s = 0.05
    rms = rms_curve(y, sr, hop_s)
    if rms.size < 3 or float(rms.max()) < 1e-3:
        return []
    norm = rms / rms.max()
    gap = int(min_gap / hop_s)
    cand = [i for i in range(1, len(norm) - 1)
            if norm[i] >= norm[i - 1] and norm[i] >= norm[i + 1] and norm[i] > 0.3]
    cand.sort(key=lambda i: -norm[i])
    chosen: list[int] = []
    for i in cand:
        if all(abs(i - j) >= gap for j in chosen):
            chosen.append(i)
        if len(chosen) == max_peaks:
            break
    return sorted(round(i * hop_s, 2) for i in chosen)
