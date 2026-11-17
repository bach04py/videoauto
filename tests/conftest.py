from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from videoauto.config import load_config
from videoauto.vision.base import VisionProvider

FIXTURES = Path(__file__).parent / "fixtures"


def _find(name: str) -> Path:
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"fixture missing: tests/fixtures/{name} (videos are not committed)")
    return p


@pytest.fixture(scope="session")
def gym() -> Path:
    """Real source clip: vertical 474x900, ~15.5 s, no text, no edits."""
    return _find("gym.mp4")


@pytest.fixture(scope="session")
def sunset() -> Path:
    """10 s sunset timelapse with music, no speech, single shot."""
    return _find("sunset.mp4")


@pytest.fixture(autouse=True, scope="session")
def _need_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")


class NoGpuProbe:
    """The default in tests: ffmpeg lists no hardware encoder (no real probe ever runs)."""

    def listed(self, ffmpeg):
        return {"libx264"}

    def trial(self, ffmpeg, encoder):
        return "not listed"


class NoKeys:
    def poll(self):
        return None


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No test probes this machine's GPU or reads its keyboard unless it asks to."""
    from videoauto import gpu, keys
    monkeypatch.setattr(gpu, "DEFAULT_PROBE", NoGpuProbe())
    monkeypatch.setattr(gpu, "_cache", {})
    monkeypatch.setattr(keys, "console_keys", lambda: NoKeys())


@pytest.fixture
def cfg() -> dict:
    return load_config()


class FakeProvider(VisionProvider):
    """Answers like a well-behaved model; `script` can inject failures per call (1-based)."""

    name = "fake"

    def __init__(self, script: dict[int, object] | None = None):
        self.calls = 0
        self.script = script or {}
        self.describe_prompts: list[str] = []      # caption prompts, to check what they contain

    def image_reference(self, image: Path) -> str:
        return f"The image {image.name}"

    def describe(self, image: Path, prompt: str) -> str:
        self.calls += 1
        self.describe_prompts.append(prompt)
        action = self.script.get(self.calls)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, str):
            return action
        cells = [int(c) for c in re.findall(r"^- cells? (\d+)", prompt, re.M)]
        return json.dumps([{"cell": c, "caption": f"Cảnh thử {c}, máy quay đứng yên",
                            "subjects": ["thing"], "shot_size": "medium"} for c in cells],
                          ensure_ascii=False)

    def complete(self, prompt: str) -> str:
        """Text call (planner). `reply` is returned verbatim; script entries can override."""
        self.calls += 1
        self.prompts.append(prompt)
        action = self.script.get(self.calls)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, str):
            return action
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)

    reply: object = "{}"

    @property
    def prompts(self) -> list[str]:
        if not hasattr(self, "_prompts"):
            self._prompts = []
        return self._prompts
