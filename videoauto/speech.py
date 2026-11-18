"""Speech: VAD gate first, Whisper only when there is speech. Optional and OFF by default.

Speech recognition is a nice-to-have for the planner (what is said in a clip), never a reason to
fail a build:
  - whisper.model: off (default) | tiny | base | small | medium | large-v3 | auto, set in
    config.yaml (or videoauto.toml). The first use downloads the model once (README, Prerequisites).
  - CUDA is used only when ctranslate2 sees a GPU AND the CUDA libraries it needs load
    (cuBLAS 12: cublas64_12.dll on Windows); otherwise the CPU. compute_type is "auto", so a card
    without efficient float16 gets a type it supports instead of a ValueError.
  - Any error while loading the model or transcribing is logged once as a warning, speech
    recognition is switched off for the rest of the run, and the clip gets transcript: null.
"""

from __future__ import annotations

import copy
import ctypes
import logging
import sys
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("videoauto")
SR = 16000
OFF = ("off", "none", "false", "no", "0", "")
MODELS = ("tiny", "base", "small", "medium", "large-v3", "auto")
# approximate one-time download (Hugging Face, faster-whisper CTranslate2 conversions)
DOWNLOAD = {"tiny": "75 MB", "base": "145 MB", "small": "485 MB", "medium": "1,5 GB", "large-v3": "3,1 GB"}


@dataclass
class Segment:
    start: float
    end: float
    text: str


def model_setting(value) -> str:
    """whisper.model normalised; YAML reads a bare `off` as False."""
    if value is None or value is False:
        return "off"
    v = str(value).strip().lower()
    return "off" if v in OFF else v


def merge_whisper(vcfg: dict, section) -> dict:
    """vcfg with config.yaml's `whisper:` section on top (config.yaml is what users edit)."""
    if not section:
        return vcfg
    out = copy.deepcopy(vcfg)
    out.setdefault("whisper", {}).update(section)
    return out


def speech_seconds(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    from faster_whisper.vad import get_speech_timestamps
    ts = get_speech_timestamps(audio, sampling_rate=SR)
    return sum(t["end"] - t["start"] for t in ts) / SR


CUDA_LIBS = {"win32": ("cublas64_12.dll",), "linux": ("libcublas.so.12",)}


def cuda_libs_missing(platform: str | None = None, loader=None) -> list[str]:
    """CUDA libraries CTranslate2 needs that cannot be loaded (empty = all present)."""
    platform = platform or sys.platform
    loader = loader or ctypes.CDLL
    missing = []
    for name in CUDA_LIBS.get(platform, ()):
        try:
            loader(name)
        except OSError:
            missing.append(name)
    return missing


def cuda_device_count() -> int:
    import ctranslate2
    return ctranslate2.get_cuda_device_count()


def pick_model(setting: str) -> tuple[str, str, str]:
    """(model, device, compute_type). 'auto': large-v3 on CUDA, medium on CPU."""
    cuda = False
    try:
        cuda = cuda_device_count() > 0
    except Exception as e:  # noqa: BLE001 - a broken CUDA install must not stop anything
        log.warning("whisper: không dò được GPU (%s); dùng CPU", e)
    if cuda:
        missing = cuda_libs_missing()
        if missing:
            log.warning("whisper: có GPU NVIDIA nhưng thiếu thư viện CUDA %s; nhận giọng nói chạy bằng CPU "
                        "(cài CUDA 12 + cuDNN 9 nếu muốn dùng GPU)", ", ".join(missing))
            cuda = False
    device = "cuda" if cuda else "cpu"
    model = setting if setting != "auto" else ("large-v3" if cuda else "medium")
    return model, device, "auto"


class Transcriber:
    """Loads the Whisper model lazily, once per run. transcribe() never raises."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.setting = model_setting(cfg.get("model"))
        self.enabled = self.setting != "off"
        self._model = None
        self.loads = 0
        self.failed: str | None = None

    def _get(self, device: str | None = None):
        if self._model is None:
            from faster_whisper import WhisperModel
            name, auto_device, compute = pick_model(self.setting)
            device = device or auto_device
            log.info("whisper: tải model %s (%s, %s)%s", name, device, compute,
                     f"; lần đầu tải về khoảng {DOWNLOAD[name]}" if name in DOWNLOAD else "")
            self._model = WhisperModel(name, device=device, compute_type=compute)
            self.loads += 1
        return self._model

    def transcribe(self, audio: np.ndarray) -> list[Segment] | None:
        """Segments, [] for no speech, or None when speech recognition is off or failed."""
        if not self.enabled or self.failed:
            return None
        try:
            if speech_seconds(audio) < self.cfg.get("min_speech_seconds", 0.5):
                return []
            return self._run(audio)
        except Exception as e:  # noqa: BLE001 - optional feature: never stops the build
            self.failed = f"{type(e).__name__}: {e}"
            self._model = None
            log.warning("whisper: nhận giọng nói lỗi, tắt cho lần chạy này (transcript = null): %s", self.failed)
            return None

    def _decode(self, model, audio: np.ndarray) -> list:
        segments, _info = model.transcribe(audio, language=self.cfg.get("language"), vad_filter=True,
                                           beam_size=5)
        return list(segments)                  # decoding is lazy: CUDA errors surface here

    def _run(self, audio: np.ndarray) -> list[Segment]:
        try:
            segments = self._decode(self._get(), audio)
        except RuntimeError as e:
            text = str(e).lower()
            if "cuda" not in text and "cublas" not in text and "cudnn" not in text:
                raise
            log.warning("whisper: lỗi CUDA (%s); thử lại bằng CPU", e)
            self._model = None
            segments = self._decode(self._get(device="cpu"), audio)
        return [Segment(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments if s.text.strip()]
