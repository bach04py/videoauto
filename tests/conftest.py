from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from videoauto.config import load_config
from videoauto.vision.base import VisionProvider

FIXTURES = Path(__file__).parent / "fixtures"


def _find(*patterns: str) -> Path:
    for pat in patterns:
        hits = sorted(FIXTURES.glob(pat))
        if hits:
            return hits[0]
    pytest.skip(f"fixture missing: {patterns}")


@pytest.fixture(scope="session")
def gym() -> Path:
    """Silent vertical gym clip, pillarboxed in 1920x1080, with a black title card."""
    return _find("gym.mp4", "澄澈回响*.mp4")


@pytest.fixture(scope="session")
def sunset() -> Path:
    """10 s sunset timelapse with music, no speech, single shot."""
    return _find("sunset.mp4", "Stunning Sunset*.mp4")


@pytest.fixture(autouse=True, scope="session")
def _need_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")


@pytest.fixture
def cfg() -> dict:
    return load_config()


class FakeProvider(VisionProvider):
    """Answers like a well-behaved model; `script` can inject failures per call (1-based)."""

    name = "fake"

    def __init__(self, script: dict[int, object] | None = None):
        self.calls = 0
        self.script = script or {}

    def image_reference(self, image: Path) -> str:
        return f"The image {image.name}"

    def describe(self, image: Path, prompt: str) -> str:
        self.calls += 1
        action = self.script.get(self.calls)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, str):
            return action
        cells = [int(c) for c in re.findall(r"^- cells? (\d+)", prompt, re.M)]
        return json.dumps([{"cell": c, "caption": f"Cảnh thử {c}, máy quay đứng yên",
                            "subjects": ["thing"], "shot_size": "medium"} for c in cells],
                          ensure_ascii=False)
