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
        pytest.skip(f"fixture missing: tests/fixtures/{name}; make it with: python tests/fixtures/make_fixtures.py")
    return p


@pytest.fixture(scope="session")
def gym() -> Path:
    """Synthetic (make_fixtures.py): vertical 474x900, ~15.5 s, one take, no audio."""
    return _find("gym.mp4")


@pytest.fixture(scope="session")
def sunset() -> Path:
    """Synthetic (make_fixtures.py): horizontal 640x360, 9.38 s, one take, music-like tone, no speech."""
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


FAKE_RENDERS: dict[str, float] = {}     # path -> seconds, written by FakeOcc's fake render


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No test probes this machine's GPU or reads its keyboard unless it asks to. A fake render
    (FakeOcc) is not a real video: its length comes from FAKE_RENDERS, real files are probed."""
    from videoauto import gpu, keys
    from videoauto.builder import builder
    monkeypatch.setattr(gpu, "DEFAULT_PROBE", NoGpuProbe())
    monkeypatch.setattr(gpu, "_cache", {})
    monkeypatch.setattr(keys, "console_keys", lambda: NoKeys())
    from videoauto import occ_catalogue
    # tests bring their own catalogue.json and a fake OpenChatCut: no dump unless a test asks
    # (fixture real_catalogue)
    monkeypatch.setattr(occ_catalogue, "ensure", lambda *a, **k: False)
    real = builder.media_duration
    monkeypatch.setattr(builder, "media_duration",
                        lambda p: FAKE_RENDERS.get(str(Path(p).resolve())) or real(p))


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


def make_clip(path: Path, seconds: float, size: str = "270x480", audio: bool = False) -> Path:
    """A synthetic clip (ffmpeg lavfi): moving test pattern, optionally a sine tone."""
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-shortest"]
    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def synth_media(tmp_path_factory) -> Path:
    """Two short vertical clips that need no third-party fixture: a.mp4 (8 s), b.mp4 (6 s)."""
    media = tmp_path_factory.mktemp("synth") / "media"
    make_clip(media / "a.mp4", 8)
    make_clip(media / "b.mp4", 6)
    return media


REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_machine(tmp_path, monkeypatch):
    """A fresh clone on a new machine: config.yaml exactly as committed, no videoauto.local.yaml,
    and a catalogue.json dumped by an OpenChatCut that has no Vietnamese review (no vi_ok)."""
    from test_planner import CATALOGUE
    root = tmp_path / "videoauto"
    root.mkdir()
    shutil.copy(REPO / "config.yaml", root / "config.yaml")
    cat = json.loads(json.dumps(CATALOGUE))
    for it in cat["items"]:
        for k in [k for k in it if k.startswith("vi_")]:
            del it[k]
    (root / "catalogue.json").write_text(json.dumps(cat), encoding="utf-8")
    monkeypatch.chdir(root)
    assert not (root / "videoauto.local.yaml").exists()
    return root


@pytest.fixture
def real_catalogue(monkeypatch):
    """Undo _hermetic's stub: catalogue.json is generated (or checked) for real."""
    import importlib
    from videoauto import occ_catalogue
    fresh = importlib.reload(occ_catalogue)
    return fresh
