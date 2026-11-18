"""Configuration: defaults, overridden by videoauto.toml (cwd or --config)."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

from .errors import UserError

DEFAULTS: dict[str, Any] = {
    "whisper": {
        # off (default): no speech recognition, no model download. tiny | base | small | medium |
        # large-v3, or "auto": large-v3 when CUDA works at runtime, else medium. config.yaml's
        # `whisper:` section overrides this (videoauto/speech.py).
        "model": "off",
        "language": None,           # None = auto-detect
        "min_speech_seconds": 0.5,  # VAD gate: below this the file has no speech
    },
    "shots": {"threshold": 27.0, "min_scene_len": 15},
    # hwaccel: auto = NVDEC (-hwaccel cuda) for the one decode of a large source when an NVIDIA
    # driver loads and ffmpeg has cuda; off = always CPU (videoauto/proxy.py)
    "indexing": {"hwaccel": "auto"},
    "keyframes": {"second_frame_after": 8.0, "max_side": 768},
    "quality": {
        # blurry = sharpness < blur_threshold (absolute floor), OR, when the file has at least
        # blur_relative_min_others other non-black shots, sharpness < blur_relative x their median.
        # Sharpness = Laplacian variance of the sharpest 3x3 tile, 480 px wide. Blurry is a flag
        # only: it never removes a shot from usable_shots.
        "blur_threshold": 35.0,
        "blur_relative": 0.25,
        "blur_relative_min_others": 2,
        "black_cover": 0.5,          # fraction of a shot that must be black/frozen to flag it
        "frozen_cover": 0.5,
        "silent_cover": 0.9,
        "silence_db": -50,
    },
    "vision": {
        "provider": "claude",
        "max_retries": 2,            # retries for a malformed reply
        "concurrency": 3,            # contact sheets described at the same time (1 = one after another)
        "claude": {"command": "claude", "model": None, "timeout": 300},
        "openai_compatible": {
            "base_url": None,
            "model": None,
            "api_key_env": "VIDEOAUTO_API_KEY",
            "timeout": 120,
        },
    },
}


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if path is None:
        candidate = Path.cwd() / "videoauto.toml"
        path = candidate if candidate.exists() else None
    elif not path.exists():
        raise UserError(f"Không tìm thấy tệp cấu hình '{path}'.")
    if path is not None:
        try:
            with open(path, "rb") as f:
                _merge(cfg, tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            raise UserError(f"Tệp cấu hình '{path.name}' sai cú pháp TOML: {e}") from None
    if cfg["vision"]["provider"] not in ("claude", "openai-compatible"):
        raise UserError(
            f"vision.provider = '{cfg['vision']['provider']}' không hợp lệ. "
            "Chọn 'claude' hoặc 'openai-compatible'."
        )
    return cfg
