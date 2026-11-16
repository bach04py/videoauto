from __future__ import annotations

from .base import VisionProvider


def make_provider(vision_cfg: dict) -> VisionProvider:
    if vision_cfg["provider"] == "openai-compatible":
        from .openai_compat import OpenAICompatibleProvider
        return OpenAICompatibleProvider(vision_cfg["openai_compatible"])
    from .claude_cli import ClaudeCLIProvider
    return ClaudeCLIProvider(vision_cfg["claude"])
