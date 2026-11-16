"""Bring-your-own-key provider: any OpenAI-compatible /chat/completions vision endpoint."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from ..errors import ProviderUnavailable
from .base import ProviderTransient, VisionProvider, classify, limit_error


class OpenAICompatibleProvider(VisionProvider):
    name = "openai-compatible"

    def __init__(self, cfg: dict):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.model = cfg.get("model")
        self.key_env = cfg.get("api_key_env") or "VIDEOAUTO_API_KEY"
        self.timeout = cfg.get("timeout", 120)

    def check(self) -> None:
        if not self.base_url or not self.model:
            raise ProviderUnavailable(
                "Thiếu cấu hình cho provider openai-compatible: cần vision.openai_compatible.base_url "
                "và vision.openai_compatible.model trong videoauto.toml."
            )

    def image_reference(self, image: Path) -> str:
        return "The attached image"

    def describe(self, image: Path, prompt: str) -> str:
        self.check()
        b64 = base64.b64encode(image.read_bytes()).decode()
        headers = {}
        key = os.environ.get(self.key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
        }
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers,
                           timeout=self.timeout)
        except httpx.TimeoutException:
            raise ProviderTransient("request timed out") from None
        except httpx.HTTPError as e:
            raise ProviderUnavailable(
                f"Không kết nối được tới {self.base_url} ({type(e).__name__}). "
                "Kiểm tra mạng và base_url trong videoauto.toml."
            ) from None

        if r.status_code == 429:
            hint = None
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                hint = f"khoảng {datetime.now() + timedelta(seconds=int(ra)):%H:%M}"
            raise limit_error(self.base_url, hint)
        if r.status_code in (401, 403):
            raise ProviderUnavailable(
                f"API key bị từ chối (HTTP {r.status_code}). Đặt key đúng vào biến môi trường {self.key_env}."
            )
        if r.status_code >= 400:
            err = classify(self.base_url, r.text)
            if err:
                raise err
            raise ProviderTransient(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderTransient("unexpected response shape") from None
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")
