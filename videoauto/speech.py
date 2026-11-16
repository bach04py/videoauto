"""Speech: VAD gate first, Whisper only when there is speech."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("videoauto")
SR = 16000


@dataclass
class Segment:
    start: float
    end: float
    text: str


def speech_seconds(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    from faster_whisper.vad import get_speech_timestamps
    ts = get_speech_timestamps(audio, sampling_rate=SR)
    return sum(t["end"] - t["start"] for t in ts) / SR


def pick_model(setting: str) -> tuple[str, str, str]:
    """(model, device, compute_type). 'auto': large-v3 on CUDA, medium on CPU."""
    import ctranslate2
    cuda = ctranslate2.get_cuda_device_count() > 0
    device, compute = ("cuda", "float16") if cuda else ("cpu", "int8")
    model = setting if setting != "auto" else ("large-v3" if cuda else "medium")
    return model, device, compute


class Transcriber:
    """Loads the Whisper model lazily, once per run."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None
        self.loads = 0

    def _get(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            name, device, compute = pick_model(self.cfg["model"])
            log.info("whisper: tải model %s (%s, %s)", name, device, compute)
            self._model = WhisperModel(name, device=device, compute_type=compute)
            self.loads += 1
        return self._model

    def transcribe(self, audio: np.ndarray) -> list[Segment]:
        if speech_seconds(audio) < self.cfg["min_speech_seconds"]:
            return []
        segments, _info = self._get().transcribe(
            audio, language=self.cfg.get("language"), vad_filter=True, beam_size=5)
        return [Segment(round(s.start, 2), round(s.end, 2), s.text.strip())
                for s in segments if s.text.strip()]
