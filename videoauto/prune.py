"""python -m videoauto.index --prune --out <index dir> [<footage folder>]

Removes indexes whose content is no longer anywhere in the footage folder. Never automatic: the
footage folder may sit on a network drive that is briefly unreachable, and a mass prune would throw
away a cache that costs real model calls to rebuild. So this lists what it would remove, asks, and
refuses outright when the footage folder is missing or holds no video (the drive is probably not
mounted, which is not the same as the library being gone).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import UserError


def footage_for(target: Path | None, out: Path) -> tuple[Path, str]:
    """The footage folder to compare against, and where that answer came from."""
    if target is not None:
        return target, "tham số dòng lệnh"
    from .watch import load_saved, clean_path, work_root
    saved = load_saved()
    if saved.get("footage") and saved.get("output"):
        idx = work_root(Path(clean_path(saved["output"])).resolve()) / ".index"
        if idx.resolve() == out.resolve():
            return Path(clean_path(saved["footage"])), "videoauto.local.yaml (python -m videoauto.watch)"
    from .brief import load_project_config
    try:
        cfg = load_project_config(None)
    except UserError:
        cfg = None
    if cfg is not None and cfg.footage_dir is not None:
        return cfg.footage_dir, "footage_dir trong config.yaml"
    raise UserError("Không biết thư mục video để so sánh. Chạy: python -m videoauto.index <thư mục video> "
                    f"--prune --out {out}")


def prune(target: Path | None, out: Path, ask=input) -> int:
    from .cache import load_hash_memo, save_hash_memo, sha256_of
    from .indexer import VIDEO_EXT, collect

    if not out.is_dir():
        raise UserError(f"Không tìm thấy thư mục chỉ mục '{out}'.")
    footage, source = footage_for(target, out)
    print(f"Thư mục video: {footage} (lấy từ {source})")
    print(f"Thư mục chỉ mục: {out}")
    refuse = ("Không dọn gì: {why}. Có thể ổ đĩa chứa video chưa được kết nối (ổ mạng, ổ ngoài); dọn lúc "
              "này sẽ xoá nhầm cả chỉ mục còn dùng được. Kết nối lại ổ đĩa rồi chạy lại.")
    if not footage.is_dir():
        raise UserError(refuse.format(why=f"thư mục video '{footage}' không tồn tại"))
    if not any(p.is_file() and p.suffix.lower() in VIDEO_EXT for p in footage.rglob("*")):
        raise UserError(refuse.format(why=f"thư mục video '{footage}' không có tệp video nào"))

    load_hash_memo(out)
    present = {sha256_of(s.path) for s in collect(footage, out)}
    save_hash_memo(out)

    orphans: list[tuple[Path, str, str]] = []          # (index json, file name, sha)
    unreadable = 0
    for p in sorted(out.glob("*.json")):
        try:
            ix = json.loads(p.read_text(encoding="utf-8"))
            sha, name = str(ix["sha256"]), str(ix.get("file", "?"))
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1                             # not ours to judge: left alone
            continue
        if sha not in present:
            orphans.append((p, name, sha))
    if unreadable:
        print(f"Bỏ qua {unreadable} tệp .json không đọc được (không xoá).")
    if not orphans:
        print("Không có chỉ mục cũ nào: mọi chỉ mục đều còn tệp tương ứng trong thư mục video.")
        return 0

    print(f"Sẽ xoá {len(orphans)} chỉ mục không còn tệp nào cùng nội dung trong thư mục video:")
    for p, name, sha in orphans:
        print(f"  {name:28} {p.name[:16]}…" + ("  (+ ảnh khung hình)" if (out / "frames" / sha).is_dir() else ""))
    print("Lập chỉ mục lại một tệp đã xoá sẽ tốn lượt gọi model.")
    try:
        answer = ask(f"Xoá {len(orphans)} chỉ mục này? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes", "c", "có", "co"):
        print("Không xoá gì.")
        return 0
    for p, _, sha in orphans:
        p.unlink(missing_ok=True)
        shutil.rmtree(out / "frames" / sha, ignore_errors=True)
    print(f"Đã xoá {len(orphans)} chỉ mục cũ.")
    return 0
