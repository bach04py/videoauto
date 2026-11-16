"""Music files: duration, BPM, beat times, RMS energy curve. Local only: never calls a model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .audio import load_audio, rms_curve
from .errors import UnreadableFile, UserError
from .probe import probe
from .schema import SCHEMA_VERSION

SR = 22050
ENERGY_HOP = 0.1   # seconds per energy value


def index_music(path: Path, sha: str) -> dict[str, Any]:
    try:
        import librosa
    except ImportError as e:
        if "Application Control" in str(e):
            raise UserError(
                "Windows Smart App Control chặn một thư viện của librosa (thường do numpy quá mới). "
                "Chạy: pip install \"numpy>=2.0,<2.3\" rồi thử lại. Không cần tắt Smart App Control."
            ) from None
        raise UserError(
            f"Chưa cài phần phân tích nhạc nên bỏ qua '{path.name}'. "
            "Chạy: pip install -e .[music]  (video vẫn lập chỉ mục bình thường)."
        ) from None

    pr = probe(path)
    if not pr.has_audio:
        raise UnreadableFile(f"'{path.name}' nằm trong thư mục music/ nhưng không có luồng âm thanh.")
    y = load_audio(path, SR)
    if y.size < SR // 2:
        raise UnreadableFile(f"'{path.name}' quá ngắn hoặc không giải mã được âm thanh.")

    tempo, beats = librosa.beat.beat_track(y=y, sr=SR, units="time")
    bpm = float(np.atleast_1d(tempo)[0])
    beats = [round(float(t), 3) for t in beats]

    rms = rms_curve(y, SR, ENERGY_HOP)
    peak = float(rms.max()) if rms.size else 0.0
    energy = [round(float(v), 3) for v in (rms / peak if peak > 1e-6 else rms * 0)]

    return {
        "schema_version": SCHEMA_VERSION,
        "file": path.name,
        "sha256": sha,
        "kind": "music",
        "duration": round(pr.duration, 3),
        # no detectable pulse (silence, ambient): bpm null, no beats
        "bpm": round(bpm, 1) if bpm > 0 and len(beats) >= 2 else None,
        "beats": beats if bpm > 0 and len(beats) >= 2 else [],
        "energy": {"hop_seconds": ENERGY_HOP, "values": energy},
    }
