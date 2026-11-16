"""config.yaml: the brief (target duration, aspect, platform) plus paths for M2/M3.

Relative paths are resolved against the directory that holds config.yaml.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
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


LOGO_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
LOGO_MODES = ("always", "end")
LOGO_END_SECONDS = 3.0


@dataclass
class Brand:
    name: str = ""
    tagline: str = ""
    context: str = ""
    logo: Path | None = None
    logo_size: tuple[int, int] | None = None     # (width, height) in pixels, read at load time
    logo_position: str = "bottom-right"
    logo_scale: float = 0.22
    logo_opacity: float = 0.85
    logo_margin: float = 0.04
    logo_mode: str = "always"

    @property
    def has_text(self) -> bool:
        return bool(self.name or self.tagline or self.context)


@dataclass
class ProjectConfig:
    path: Path
    brief: Brief
    footage_dir: Path | None
    catalogue: Path
    min_material_ratio: float
    occ: OccConfig
    brand: Brand = field(default_factory=Brand)
    script_file: Path | None = None

    def script_text(self) -> str:
        """The user's script, or "" when script_file is unset, missing or empty."""
        if self.script_file is None or not self.script_file.is_file():
            return ""
        try:
            return self.script_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise UserError(f"Không đọc được script_file '{self.script_file}' ({e}). Lưu tệp dạng văn bản UTF-8.") from None


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
        # utf-8-sig: Windows Notepad may save config.yaml with a BOM
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
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
    footage = raw.get("footage_dir") or raw.get("media_dir")          # media_dir: older name
    return ProjectConfig(path=path, brief=brief, footage_dir=_rel(base, footage),
                         catalogue=catalogue,
                         min_material_ratio=float(raw.get("min_material_ratio", 0.6)), occ=occ,
                         brand=_brand(base, raw.get("brand")),
                         script_file=_rel(base, raw.get("script_file")))


def _num(section: dict, key: str, default: float, lo: float, hi: float, hint: str = "") -> float:
    value = section.get(key, default)
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not lo <= v <= hi:
        raise UserError(f"brand.{key} = {value!r} không hợp lệ: phải là số trong khoảng {lo:g}–{hi:g}.{hint}")
    return v


def _brand(base: Path, raw) -> Brand:
    if not raw:
        return Brand()
    if not isinstance(raw, dict):
        raise UserError("brand trong config.yaml phải là một bảng khoá: giá trị (xem README, mục Brand).")
    text = {k: str(raw.get(k) or "").strip() for k in ("name", "tagline", "context")}
    b = Brand(**text)
    if not raw.get("logo"):
        return b
    b.logo = _rel(base, raw["logo"])
    if not b.logo.is_file():
        raise UserError(f"Không tìm thấy logo '{b.logo}' (brand.logo trong config.yaml). Kiểm tra đường dẫn, "
                        "hoặc xoá dòng logo nếu không cần logo.")
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(str(b.logo), np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or b.logo.suffix.lower() != ".png":
        raise UserError(f"Logo '{b.logo.name}' phải là tệp PNG đọc được (nên có nền trong suốt).")
    b.logo_size = (img.shape[1], img.shape[0])
    b.logo_position = str(raw.get("logo_position", b.logo_position))
    if b.logo_position not in LOGO_POSITIONS:
        raise UserError(f"brand.logo_position = '{b.logo_position}' không hợp lệ. Chọn một trong: "
                        f"{', '.join(LOGO_POSITIONS)}.")
    b.logo_mode = str(raw.get("logo_mode", b.logo_mode))
    if b.logo_mode not in LOGO_MODES:
        raise UserError(f"brand.logo_mode = '{b.logo_mode}' không hợp lệ. Chọn 'always' (suốt video) hoặc "
                        f"'end' ({LOGO_END_SECONDS:g} giây cuối).")
    b.logo_scale = _num(raw, "logo_scale", b.logo_scale, 0.05, 0.5,
                        " Đây là tỉ lệ chiều rộng logo so với khung hình (0.22 = 22%).")
    b.logo_opacity = _num(raw, "logo_opacity", b.logo_opacity, 0.05, 1.0)
    b.logo_margin = _num(raw, "logo_margin", b.logo_margin, 0.0, 0.25)
    return b
