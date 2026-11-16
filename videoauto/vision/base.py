"""Vision provider interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from ..errors import LimitReached, ProviderUnavailable


class ProviderTransient(Exception):
    """A failed attempt worth retrying (timeout, 5xx, unparseable envelope)."""


class VisionProvider(ABC):
    name: str = "base"

    def check(self) -> None:
        """Raise ProviderUnavailable if the provider cannot possibly work (fail fast)."""

    @abstractmethod
    def image_reference(self, image: Path) -> str:
        """How the prompt should refer to the image."""

    @abstractmethod
    def describe(self, image: Path, prompt: str) -> str:
        """Send one image + prompt, return the model's raw text reply.
        Raises LimitReached, ProviderUnavailable or ProviderTransient."""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Text-only request (no image, no tools), return the raw text reply. Same errors."""


_LIMIT = re.compile(r"usage limit|limit reached|hit your limit|rate.?limit|too many requests|\b429\b"
                    r"|quota|out of (extra )?usage", re.I)
_AUTH = re.compile(r"not logged in|/login|invalid api key|authenticat|\b401\b|oauth|unauthori[sz]ed"
                   r"|\b403\b|permission denied|credit balance", re.I)


def retry_hint_from_text(text: str) -> str | None:
    m = re.search(r"\|(\d{10})\b", text)
    if m:
        t = datetime.fromtimestamp(int(m.group(1)))
        return f"khoảng {t:%H:%M} ngày {t:%d/%m}"
    m = re.search(r"resets?\s+(?:at\s+)?([^\n.·|]{2,40})", text, re.I)
    if m:
        return f"sau thời điểm \"{m.group(1).strip()}\" (theo thông báo của nhà cung cấp)"
    return None


def limit_error(provider: str, hint: str | None) -> LimitReached:
    when = hint or "vài giờ nữa (giới hạn gói Claude thường đặt lại sau 5 giờ)"
    return LimitReached(
        f"Đã chạm giới hạn sử dụng/tốc độ của {provider}. Đã dừng an toàn: các tệp đã xử lý xong "
        f"được lưu vào cache, tệp đang dở không được ghi. Hãy chạy lại lệnh {when}; "
        "các tệp đã có trong cache sẽ không tốn lượt gọi nào.",
        retry_hint=hint,
    )


def classify(provider: str, text: str) -> Exception | None:
    """Map an error text to LimitReached / ProviderUnavailable, or None if unknown."""
    if _LIMIT.search(text):
        return limit_error(provider, retry_hint_from_text(text))
    if _AUTH.search(text):
        return ProviderUnavailable(
            f"Không có quyền truy cập {provider}: {text.strip()[:160]}. "
            + ("Chạy `claude` trong terminal rồi gõ /login để đăng nhập."
               if provider == "Claude" else "Kiểm tra API key và base_url trong videoauto.toml.")
        )
    return None
