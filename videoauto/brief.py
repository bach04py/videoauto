"""config.yaml: the brief (target duration, aspect, platform) plus paths for M2/M3.

Relative paths are resolved against the directory that holds config.yaml.
"""

from __future__ import annotations

import os
import re
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
    gpu_encode: str = "auto"          # auto | on | off (see videoauto/gpu.py)


GPU_MODES = ("auto", "on", "off")
PACES = ("fast", "medium", "calm")


@dataclass
class Style:
    """How the edit should feel; goes into the planning prompt only. Editable from the watcher (S)."""
    tone: str = ""                     # one line, free text
    pace: str = ""                     # "" (not set) | fast | medium | calm
    prefer_luts: list[str] = field(default_factory=list)   # LUT ids from catalogue.json

    @property
    def is_set(self) -> bool:
        return bool(self.tone or self.pace or self.prefer_luts)


LOGO_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
LOGO_MODES = ("always", "end")
LOGO_END_SECONDS = 3.0
COLOR_ROLES = ("accent", "ink", "on_accent")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


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
    colors: dict[str, str] = field(default_factory=dict)   # role -> "#RRGGBB" (COLOR_ROLES), only those set

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
    style: Style = field(default_factory=Style)
    whisper: dict = field(default_factory=dict)     # config.yaml `whisper:` (speech recognition)

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


def load_project_config(path: Path | None = None, problems: list[str] | None = None) -> ProjectConfig:
    """problems: when given, a brand.logo that cannot be used is recorded there (and the logo left
    out) instead of raised, so the startup check can report it together with everything else."""
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
        gpu_encode=gpu_mode(o.get("gpu_encode", "auto")),
    )
    catalogue = _rel(base, raw.get("catalogue") or "catalogue.json")
    footage = raw.get("footage_dir") or raw.get("media_dir")          # media_dir: older name
    return ProjectConfig(path=path, brief=brief, footage_dir=_rel(base, footage),
                         catalogue=catalogue,
                         min_material_ratio=float(raw.get("min_material_ratio", 0.6)), occ=occ,
                         brand=_brand(base, raw.get("brand"), problems),
                         script_file=_rel(base, raw.get("script_file")),
                         style=parse_style(raw.get("style")),
                         whisper=parse_whisper(raw.get("whisper")))


def parse_whisper(raw) -> dict:
    """config.yaml `whisper:` {model, language}. Absent: speech recognition stays off."""
    from .speech import DOWNLOAD, MODELS, model_setting
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UserError("whisper trong config.yaml phải là một bảng khoá: giá trị, ví dụ `model: off`.")
    out = {}
    if "model" in raw:
        m = model_setting(raw["model"])
        if m != "off" and m not in MODELS:
            sizes = ", ".join(f"{k} ({v})" for k, v in DOWNLOAD.items())
            raise UserError(f"whisper.model = {raw['model']!r} không hợp lệ. Chọn off (tắt, mặc định) hoặc một "
                            f"model: {sizes}, hoặc auto.")
        out["model"] = m
    if raw.get("language"):
        out["language"] = str(raw["language"]).strip()
    return out


def gpu_mode(value) -> str:
    """openchatcut.gpu_encode. YAML 1.1 reads a bare on/off as true/false: accept both spellings."""
    if value is None:
        v = "auto"
    elif isinstance(value, bool):
        v = "on" if value else "off"
    else:
        v = str(value).strip().lower()
    if v not in GPU_MODES:
        raise UserError(f"openchatcut.gpu_encode = {value!r} không hợp lệ. Chọn auto (dùng GPU khi thử mã hoá "
                        "được), on (bắt buộc GPU, lỗi nếu không có) hoặc off (luôn mã hoá bằng CPU).")
    return v


def split_luts(value) -> list[str]:
    """prefer_luts as a YAML list, or a comma-separated string (what the watcher editor accepts)."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    out = []
    for it in items:
        t = str(it if it is not None else "").strip()
        if t and t not in out:
            out.append(t)
    return out


def parse_style(raw) -> Style:
    if not raw:
        return Style()
    if not isinstance(raw, dict):
        raise UserError("style trong config.yaml phải là một bảng khoá: giá trị (tone, pace, prefer_luts).")
    tone = " ".join(str(raw.get("tone") or "").split())
    pace = str(raw.get("pace") or "").strip().lower()
    if pace and pace not in PACES:
        raise UserError(f"style.pace = {raw.get('pace')!r} không hợp lệ. Chọn một trong: {', '.join(PACES)} "
                        "(hoặc để trống).")
    luts = raw.get("prefer_luts")
    if luts is not None and not isinstance(luts, (list, str)):
        raise UserError("style.prefer_luts phải là danh sách id LUT, ví dụ [builtin:look-teal-orange].")
    return Style(tone=tone, pace=pace, prefer_luts=split_luts(luts))


def _num(section: dict, key: str, default: float, lo: float, hi: float, hint: str = "") -> float:
    value = section.get(key, default)
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not lo <= v <= hi:
        raise UserError(f"brand.{key} = {value!r} không hợp lệ: phải là số trong khoảng {lo:g}–{hi:g}.{hint}")
    return v


def _brand(base: Path, raw, problems: list[str] | None = None) -> Brand:
    if not raw:
        return Brand()
    if not isinstance(raw, dict):
        raise UserError("brand trong config.yaml phải là một bảng khoá: giá trị (xem README, mục Brand).")
    text = {k: str(raw.get(k) or "").strip() for k in ("name", "tagline", "context")}
    b = Brand(**text, colors=_colors(raw.get("colors")))
    if not raw.get("logo"):
        return b
    if problems is None:
        return _logo(base, raw, b)
    try:
        return _logo(base, raw, b)
    except UserError as e:
        problems.append(e.message)
        b.logo, b.logo_size = None, None
        return b


def _logo(base: Path, raw: dict, b: Brand) -> Brand:
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


def _colors(raw) -> dict[str, str]:
    """brand.colors: {accent, ink, on_accent} as #RGB or #RRGGBB. Returned as upper-case #RRGGBB."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise UserError("brand.colors trong config.yaml phải là một bảng khoá: giá trị, ví dụ "
                        "accent: \"#ED1C24\" (xem README, mục Text overlays).")
    unknown = set(raw) - set(COLOR_ROLES)
    if unknown:
        raise UserError(f"brand.colors có khoá không hỗ trợ: {', '.join(sorted(map(str, unknown)))}. "
                        f"Chỉ dùng: {', '.join(COLOR_ROLES)}.")
    out = {}
    for role in COLOR_ROLES:
        if role not in raw:
            continue
        v = "" if raw[role] is None else str(raw[role]).strip()   # None: `accent: #ED1C24` unquoted
        if not _HEX.fullmatch(v):
            raise UserError(f"brand.colors.{role} = {raw[role]!r} không phải mã màu hex hợp lệ. "
                            "Dùng dạng \"#RRGGBB\" (ví dụ \"#ED1C24\") và đặt trong dấu ngoặc kép, "
                            "vì YAML coi phần sau dấu # là chú thích.")
        if len(v) == 4:
            v = "#" + "".join(c * 2 for c in v[1:])
        out[role] = v.upper()
    return out
