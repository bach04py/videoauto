"""Makes the committed test clips with ffmpeg alone (no third-party media):

    python tests/fixtures/make_fixtures.py

gym.mp4     474x900 (vertical), 15.54 s, 30 fps, one continuous take, no audio stream.
            Moving test pattern, slightly softened: sharp enough not to be "blurry", softer
            than sunset.mp4 (the planner tests rank it 2/2).
sunset.mp4  640x360 (horizontal), 9.38 s, one take, with a music-like soundtrack (a tone
            pulsing at 120 BPM), no speech. Crisper pattern.

The files are deterministic for one ffmpeg build; tests only check properties (size, length,
one shot, sharpness order), never hashes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make(out: Path, args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-map_metadata", "-1", "-fflags", "+bitexact",
                    "-flags:v", "+bitexact", str(out)], check=True)
    print(f"{out.name}: {out.stat().st_size / 1e3:.0f} KB")


def main() -> int:
    make(HERE / "gym.mp4", [
        "-f", "lavfi", "-i", "testsrc2=size=474x900:rate=30:duration=15.54",
        "-vf", "gblur=sigma=0.8",
        "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p", "-an"])
    make(HERE / "sunset.mp4", [
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=9.38",
        "-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*220*t)*(0.3+0.7*exp(-8*mod(t,0.5)))':s=44100:d=9.38",
        "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "48k", "-shortest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
