"""openchatcut.gpu_encode: the hardware path is tested with a faked probe (this laptop's GPU cannot
encode); the software fallback also runs for real against this machine's ffmpeg."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from test_builder import setup  # noqa: F401 - fixture
from videoauto import gpu
from videoauto.brief import gpu_mode, load_project_config
from videoauto.errors import UserError


class FakeProbe:
    """listed: encoders ffmpeg reports, per binary ('*' = all binaries); trials: encoder -> error or None."""

    def __init__(self, listed=("h264_nvenc", "h264_qsv", "h264_amf"), trials=None, renderer=None):
        self.have, self.renderer, self.trials = set(listed) | {"libx264"}, renderer, trials or {}
        self.calls: list[tuple] = []

    def listed(self, ffmpeg):
        self.calls.append(("listed", ffmpeg))
        if self.renderer is not None and "remotion" in ffmpeg:
            return set(self.renderer) | {"libx264"}
        return set(self.have)

    def trial(self, ffmpeg, encoder):
        self.calls.append(("trial", encoder))
        return self.trials.get(encoder, "Cannot load nvcuda.dll")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake OpenChatCut checkout with both ffmpeg binaries where occ looks for them."""
    for var in ("OPENCHATCUT_FFMPEG", "FFMPEG_PATH", "CC_REMOTION_HARDWARE_BINARIES_DIR",
                "CC_REMOTION_BINARIES_DIR"):
        monkeypatch.delenv(var, raising=False)
    r = tmp_path / "OpenChatCut"
    exe = gpu._exe("ffmpeg")
    for d in (r / "node_modules" / "ffmpeg-static", r / "node_modules" / "@remotion" / "compositor-win32-x64-msvc",
              r / "node_modules" / "@remotion" / "compositor-darwin-arm64",
              r / "node_modules" / "@remotion" / "compositor-linux-x64-gnu"):
        d.mkdir(parents=True)
        (d / exe).write_bytes(b"")
    return r


def test_auto_uses_the_first_encoder_whose_trial_passes(repo):
    probe = FakeProbe(trials={"h264_qsv": None})
    c = gpu.choose("auto", repo, probe, platform="win32")
    assert c.encoder == "h264_qsv" and c.hardware
    assert c.env() == {"OPENCHATCUT_H264_ENCODER": "h264_qsv", "OPENCHATCUT_DISABLE_HARDWARE_ENCODING": "0"}
    assert [p["encoder"] for p in c.probes] == ["h264_nvenc", "h264_qsv"]          # stops at the first
    assert c.probes[0]["result"] == "Cannot load nvcuda.dll"


def test_listed_but_broken_driver_falls_back_silently_with_the_reason(repo, caplog):
    caplog.set_level("INFO", logger="videoauto")
    probe = FakeProbe(trials={"h264_amf": "CreateComponent(AMFVideoEncoderVCE_AVC) failed with error 30"})
    c = gpu.choose("auto", repo, probe, platform="win32")                  # no exception: silent
    assert c.encoder == "libx264" and not c.hardware
    assert c.env() == {"OPENCHATCUT_DISABLE_HARDWARE_ENCODING": "1", "OPENCHATCUT_H264_ENCODER": ""}
    assert "thử mã hoá thất bại" in c.reason and "error 30" in c.reason and "nvcuda" in c.reason
    assert "error 30" in caplog.text                                       # and logged


def test_encoder_missing_from_the_renderer_is_not_even_tried(repo):
    """Remotion's own ffmpeg renders: an encoder it lacks would end up libx264 inside occ anyway."""
    probe = FakeProbe(trials={"h264_amf": None}, renderer={"h264_nvenc"})
    c = gpu.choose("auto", repo, probe, platform="win32")
    assert c.encoder == "libx264"
    assert ("trial", "h264_amf") not in probe.calls
    amf = next(p for p in c.probes if p["encoder"] == "h264_amf")
    assert amf["listed"] is False and "dựng hình" in amf["result"]


def test_on_fails_loudly_when_nothing_works(repo):
    with pytest.raises(UserError, match="gpu_encode = on"):
        gpu.choose("on", repo, FakeProbe(), platform="win32")


def test_on_with_a_working_gpu(repo):
    c = gpu.choose("on", repo, FakeProbe(trials={"h264_nvenc": None}), platform="win32")
    assert c.encoder == "h264_nvenc"


def test_off_never_probes(repo):
    probe = FakeProbe(trials={"h264_nvenc": None})
    c = gpu.choose("off", repo, probe, platform="win32")
    assert c.encoder == "libx264" and probe.calls == []
    assert c.env()["OPENCHATCUT_DISABLE_HARDWARE_ENCODING"] == "1"


def test_probe_runs_once_per_process(repo):
    probe = FakeProbe(trials={"h264_nvenc": None})
    gpu.choose("auto", repo, probe, platform="win32")
    n = len(probe.calls)
    gpu.choose("auto", repo, probe, platform="win32")
    assert len(probe.calls) == n


def test_platform_candidates(repo):
    c = gpu.choose("auto", repo, FakeProbe(listed={"h264_videotoolbox"}, trials={"h264_videotoolbox": None}),
                   platform="darwin")
    assert c.encoder == "h264_videotoolbox"
    assert gpu.choose("auto", repo, FakeProbe(), platform="sunos").encoder == "libx264"


def test_gpu_encode_setting_accepts_yaml_on_off():
    assert gpu_mode(True) == "on" and gpu_mode(False) == "off" and gpu_mode(None) == "auto"
    assert gpu_mode("OFF") == "off"
    with pytest.raises(UserError, match="gpu_encode"):
        gpu_mode("nvenc")


def test_bare_on_in_config_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text("openchatcut:\n  repo: x\n  node: node\n  gpu_encode: on\n",
                                          encoding="utf-8")
    assert load_project_config(tmp_path / "config.yaml").occ.gpu_encode == "on"


# ---------------------------------------------------------------- after the render

def test_verify_reads_the_encoder_from_the_file(tmp_path):
    hw = gpu.EncoderChoice("auto", "h264_nvenc", "ok")
    sw = gpu.EncoderChoice("auto", "libx264", "no gpu")
    x264 = tmp_path / "a.mp4"
    x264.write_bytes(b"\0" * (1 << 20) + b"x264 - core 164" + b"\0" * 10)    # across a chunk boundary
    plain = tmp_path / "b.mp4"
    plain.write_bytes(b"\0" * 4096)

    r = gpu.verify(sw, x264)
    assert (r["used"], r["hardware"], r.get("fallback")) == ("libx264", False, None)
    r = gpu.verify(hw, plain)
    assert (r["used"], r["hardware"], r.get("fallback")) == ("h264_nvenc", True, None)
    r = gpu.verify(hw, x264)                     # occ fell back during the render
    assert r["used"] == "libx264" and "chuyển sang libx264" in r["fallback"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_real_probe_on_this_machine(tmp_path, caplog):
    """Runs the real probe (no GPU encode works on the development laptop). Whatever the machine,
    the choice must be either a hardware encoder whose trial passed or libx264 with a reason."""
    caplog.set_level("INFO", logger="videoauto")
    real = gpu.FfmpegProbe()
    c = gpu._choose("auto", shutil.which("ffmpeg"), None, real, "win32" if gpu.sys.platform == "win32"
                    else gpu.sys.platform)
    if c.hardware:
        assert c.probes[-1]["result"] == "ok"
    else:
        assert c.reason and all(p["result"] != "ok" for p in c.probes)
    sample = tmp_path / "sw.mp4"                 # and a real libx264 file carries the x264 mark
    import subprocess
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=128x128:rate=30", "-t", "0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(sample)], check=True)
    assert gpu.written_by_x264(sample)


# ---------------------------------------------------------------- through the builder

def test_build_records_gpu_encoder(setup_gpu, monkeypatch):
    from test_builder import FakeOcc
    from videoauto.builder.builder import Builder
    from videoauto.build import finish_build
    tmp, media, plan = setup_gpu
    monkeypatch.setattr(gpu, "DEFAULT_PROBE", FakeProbe(trials={"h264_nvenc": None}))
    monkeypatch.setitem(gpu.CANDIDATES, gpu.sys.platform, ("h264_nvenc",))
    occ = FakeOcc(media)
    out = tmp / "out" / "v.mp4"
    rep = Builder(load_project_config(tmp / "config.yaml"), occ).build(plan, out)
    assert rep.error is None
    render_env = occ.envs[[r.label for r in occ.history].index("render")]
    assert render_env["OPENCHATCUT_H264_ENCODER"] == "h264_nvenc"
    report = json.loads(finish_build(rep, out).read_text(encoding="utf-8"))
    assert report["encoder"]["used"] == "h264_nvenc" and report["encoder"]["hardware"] is True
    assert report["encoder"]["mode"] == "auto"


def test_build_records_software_fallback(setup_gpu):
    from test_builder import FakeOcc
    from videoauto.builder.builder import Builder
    from videoauto.build import finish_build
    tmp, media, plan = setup_gpu                         # conftest's default probe: no hardware encoder
    occ = FakeOcc(media)
    out = tmp / "out" / "v.mp4"
    rep = Builder(load_project_config(tmp / "config.yaml"), occ).build(plan, out)
    report = json.loads(finish_build(rep, out).read_text(encoding="utf-8"))
    assert report["encoder"]["used"] == "libx264" and report["encoder"]["hardware"] is False
    assert report["encoder"]["reason"]
    assert not any("mã hoá" in w for w in report["warnings"])


def test_on_mode_fails_when_occ_falls_back_mid_render(setup_gpu, monkeypatch):
    from test_builder import FakeOcc
    from videoauto.builder.builder import Builder
    tmp, media, plan = setup_gpu
    (tmp / "config.yaml").write_text((tmp / "config.yaml").read_text(encoding="utf-8")
                                     + "  gpu_encode: on\n", encoding="utf-8")
    monkeypatch.setattr(gpu, "DEFAULT_PROBE", FakeProbe(trials={"h264_nvenc": None}))
    monkeypatch.setitem(gpu.CANDIDATES, gpu.sys.platform, ("h264_nvenc",))

    class FallingBack(FakeOcc):
        def run(self, label, args, tool_args=None, timeout=300, read=False, env=None):
            res = super().run(label, args, tool_args, timeout, read, env)
            if args[0] == "render":
                Path(args[args.index("--out") + 1]).write_bytes(b"x264 - core 164")
            return res
    rep = Builder(load_project_config(tmp / "config.yaml"), FallingBack(media)).build(plan, tmp / "v.mp4")
    assert rep.error is not None and "gpu_encode = on" in rep.error.message
    assert rep.encoder["used"] == "libx264"


@pytest.fixture
def setup_gpu(setup):
    """test_builder's `setup` (needs the video fixtures): config.yaml ends inside openchatcut:."""
    return setup
