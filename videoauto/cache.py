"""Cache keyed by sha256 of the source file: <out>/<sha256>.json, written atomically."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import schema
from .errors import UnreadableFile

log = logging.getLogger("videoauto")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        raise UnreadableFile(f"Không mở được tệp '{path.name}': {e.strerror}. Kiểm tra quyền truy cập.") from None
    return h.hexdigest()


class Cache:
    def __init__(self, out_dir: Path):
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def frames_dir(self, sha: str) -> Path:
        return self.dir / "frames" / sha

    def get(self, sha: str) -> dict[str, Any] | None:
        p = self.path(sha)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("cache: %s hỏng, sẽ lập chỉ mục lại", p.name)
            return None
        if schema.errors(data):
            log.warning("cache: %s không khớp schema hiện tại, sẽ lập chỉ mục lại", p.name)
            return None
        return data

    def put(self, index: dict[str, Any]) -> Path:
        """Validate, then write atomically. A failing index raises and nothing is written."""
        schema.validate(index)
        p = self.path(index["sha256"])
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return p
