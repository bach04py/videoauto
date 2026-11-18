"""catalogue.json (made by videoauto.occ_catalogue from OpenChatCut): ids and param ranges.

Read as plain JSON. Nothing from OpenChatCut is imported.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..errors import UserError

EFFECT_KINDS = {"lut", "effect"}


def _numeric(p: dict) -> bool:
    return p.get("editable", True) and p.get("type") in ("number", "integer")


class Catalogue:
    def __init__(self, items: list[dict[str, Any]]):
        self.items = {it["assetId"]: it for it in items}

    @classmethod
    def load(cls, path: Path) -> "Catalogue":
        if not path.exists():
            raise UserError(f"Không tìm thấy catalogue '{path}'. videoauto tự tạo tệp này từ OpenChatCut khi "
                            "chạy; tạo ngay bằng: python -m videoauto.setup (cài OpenChatCut nếu chưa có, rồi tạo "
                            "catalogue). Không cần chép tay tệp nào.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(data["items"])
        except (OSError, ValueError, KeyError, TypeError):
            raise UserError(f"'{path.name}' không phải catalogue hợp lệ của OpenChatCut (thiếu 'items').") from None

    def kind(self, asset_id: str) -> str | None:
        it = self.items.get(asset_id)
        return it["kind"] if it else None

    def of_kind(self, kind: str) -> list[dict]:
        return [it for it in self.items.values() if it["kind"] == kind]

    def numeric_params(self, asset_id: str) -> list[dict]:
        return [p for p in self.items[asset_id].get("params", []) if _numeric(p)]

    def param_error(self, asset_id: str, params: list) -> str | None:
        """None if params fit the published ranges (positional over the numeric params)."""
        spec = self.numeric_params(asset_id)
        if len(params) > len(spec):
            return f"{len(params)} tham số nhưng {asset_id} chỉ có {len(spec)}"
        for v, p in zip(params, spec):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return f"{p['key']} = {v!r} không phải số"
            lo, hi = p.get("min"), p.get("max")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                return f"{p['key']} = {v} ngoài khoảng [{lo}, {hi}]"
            if p.get("type") == "integer" and v != int(v):
                return f"{p['key']} = {v} phải là số nguyên"
        return None

    def overrides(self, asset_id: str, params: list) -> dict[str, float]:
        return {p["key"]: v for v, p in zip(params, self.numeric_params(asset_id))}

    def params_from_dict(self, asset_id: str, values: dict) -> list:
        """Model replies use {key: value}; plans use positional lists (defaults fill gaps)."""
        spec = self.numeric_params(asset_id)
        unknown = set(values) - {p["key"] for p in spec}
        if unknown:
            raise ValueError(f"tham số không có trong catalogue: {', '.join(sorted(unknown))}")
        out = [values.get(p["key"], p.get("default")) for p in spec]
        while out and spec[len(out) - 1]["key"] not in values:   # trailing defaults stay implicit
            out.pop()
        return out
