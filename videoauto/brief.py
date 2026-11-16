"""config.yaml: the brief (target duration, aspect, platform) plus paths for M2/M3.

Relative paths are resolved against the directory that holds config.yaml.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import UserError

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
           "4:5": (1080, 1350), "4:3": (1440, 1080), "3:4": (1080, 1440)}
FPS = (24, 25, 30, 50, 60)


@dataclass
class Brief:
    duration: float
    aspect: str
    fps: int
    platform: str
    notes: str

    @property
    def size(self) -> tuple[int, int]:
        return ASPECTS[self.aspect]

    @property
    def orientation(self) -> str:
        w, h = self.size
        return "square" if w == h else ("vertical" if h > w else "horizontal")


@dataclass
class OccConfig:
    repo: Path
    data_dir: Path
    node: str
    resolution: str
    timeout: int
    max_fail_ratio: float
    video_bitrate_mbps: float = 10.0


@dataclass
class ProjectConfig:
    path: Path
    brief: Brief
    media_dir: Path | None
    catalogue: Path
    min_material_ratio: float
    occ: OccConfig


def _rel(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(value))))
    return p if p.is_absolute() else (base / p).resolve()


def _find_node(repo: Path) -> str | None:
    """`node` on PATH, else the fnm-managed version named in the repo's .nvmrc."""
    found = shutil.which("node")
    if found:
        return found
    want = (repo / ".nvmrc").read_text(encoding="utf-8").strip() if (repo / ".nvmrc").exists() else ""
    root = Path(os.environ.get("FNM_DIR") or Path(os.environ.get("APPDATA", "")) / "fnm") / "node-versions"
    candidates = sorted(root.glob("v*/installation/node.exe"), reverse=True)
    for c in candidates:
        if want and c.parts[-3].lstrip("v") == want.lstrip("v"):
            return str(c)
    return str(candidates[0]) if candidates else None


def _bitrate(value) -> float:
    try:
        mbps = float(value)
    except (TypeError, ValueError):
        mbps = -1
    if not 0.5 <= mbps <= 100:
        raise UserError(f"openchatcut.video_bitrate_mbps = {value!r} không hợp lệ: dùng một số trong "
                        "khoảng 0.5–100 (gợi ý 8–12 cho video dọc 1080x1920).")
    return mbps


def _node_setting(base: Path, value: str | None, repo: Path) -> str:
    if not value:
        return _find_node(repo) or "node"
    value = str(value)
    return str(_rel(base, value)) if ("/" in value or "\\" in value) else value   # bare name: PATH


def load_project_config(path: Path | None = None) -> ProjectConfig:
    try:
        import yaml
    except ImportError:
        raise UserError("Thiếu thư viện PyYAML. Chạy: pip install -e .") from None
    if path is None:
        path = Path.cwd() / "config.yaml"
    if not path.exists():
        raise UserError(f"Không tìm thấy '{path}'. Tạo config.yaml (xem README, mục M2) hoặc dùng --config.")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise UserError(f"config.yaml sai cú pháp YAML: {e}") from None
    if not isinstance(raw, dict):
        raise UserError("config.yaml phải là một bảng khoá: giá trị.")
    base = path.resolve().parent

    b = raw.get("brief") or {}
    try:
        brief = Brief(float(b.get("duration", 15)), str(b.get("aspect", "9:16")), int(b.get("fps", 30)),
                      str(b.get("platform", "tiktok")), str(b.get("notes") or ""))
    except (TypeError, ValueError):
        raise UserError("brief trong config.yaml không hợp lệ: duration phải là số giây, fps là số nguyên.") from None
    if brief.aspect not in ASPECTS:
        raise UserError(f"brief.aspect = '{brief.aspect}' không hỗ trợ. Chọn một trong: {', '.join(ASPECTS)}.")
    if brief.fps not in FPS:
        raise UserError(f"brief.fps = {brief.fps} không hỗ trợ. Chọn một trong: {', '.join(map(str, FPS))}.")
    if not 1 <= brief.duration <= 600:
        raise UserError("brief.duration phải nằm trong khoảng 1–600 giây.")

    o = raw.get("openchatcut") or {}
    repo = _rel(base, o.get("repo")) or Path()
    occ = OccConfig(
        repo=repo,
        data_dir=_rel(base, o.get("data_dir") or ".occ-data"),
        node=_node_setting(base, o.get("node"), repo),
        resolution=str(o.get("resolution", "1080p")),
        timeout=int(o.get("timeout", 1800)),
        max_fail_ratio=float(o.get("max_fail_ratio", 0.30)),
        video_bitrate_mbps=_bitrate(o.get("video_bitrate_mbps", 10)),
    )
    catalogue = _rel(base, raw.get("catalogue") or "catalogue.json")
    return ProjectConfig(path=path, brief=brief, media_dir=_rel(base, raw.get("media_dir")),
                         catalogue=catalogue,
                         min_material_ratio=float(raw.get("min_material_ratio", 0.6)), occ=occ)
