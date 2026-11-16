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


# Hashing a 4K library takes seconds per GB, and indexer, planner and builder each check content.
# The sha256 stays the cache key and the identity check; this memo only skips re-reading a file
# whose (path, size, mtime) has not changed since it was hashed. A file rewritten with the same
# size AND the same mtime would be missed; cameras and editors never do that.
HASH_MEMO = "hashes.memo"        # in the index dir; not *.json, so it is never read as an index
_memo: dict[str, tuple[int, int, str]] = {}      # normcased absolute path -> (size, mtime_ns, sha)
_memo_dirty = False


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def load_hash_memo(index_dir: Path) -> None:
    """Merge the memo saved in index_dir (if any) into this process's memo."""
    f = index_dir / HASH_MEMO
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        for k, (size, mtime, sha) in data.items():
            _memo.setdefault(k, (int(size), int(mtime), str(sha)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass                                         # missing or damaged: files are simply hashed


def save_hash_memo(index_dir: Path) -> None:
    global _memo_dirty
    if not _memo_dirty and (index_dir / HASH_MEMO).exists():
        return
    f = index_dir / HASH_MEMO
    tmp = f.with_name(f.name + ".tmp")
    try:
        tmp.write_text(json.dumps({k: list(v) for k, v in _memo.items()}), encoding="utf-8")
        os.replace(tmp, f)
        _memo_dirty = False
    except OSError as e:
        log.debug("không ghi được %s: %s", f, e)


def sha256_of(path: Path) -> str:
    """sha256 of the file, re-read only when its size or mtime changed since the last hash."""
    global _memo_dirty
    try:
        st = os.stat(path)
    except OSError as e:
        raise UnreadableFile(f"Không mở được tệp '{path.name}': {e.strerror}. Kiểm tra quyền truy cập.") from None
    key = _key(path)
    hit = _memo.get(key)
    if hit and hit[:2] == (st.st_size, st.st_mtime_ns):
        return hit[2]
    sha = _hash_file(path)
    try:
        after = os.stat(path)
    except OSError:
        return sha
    if (after.st_size, after.st_mtime_ns) == (st.st_size, st.st_mtime_ns):   # not changed while reading
        _memo[key] = (st.st_size, st.st_mtime_ns, sha)
        _memo_dirty = True
    return sha


def _hash_file(path: Path) -> str:
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
