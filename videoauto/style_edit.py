"""Edit the planner's style fields in config.yaml from the watcher (key S).

Four fields steer the planner:
    context      brand.context     multi-line free text: what this place is, who it is for
    tone         style.tone        one line
    pace         style.pace        fast | medium | calm
    prefer_luts  style.prefer_luts LUT ids from catalogue.json

config.yaml is hand-written and full of comments (the CHANGE markers among them), so it is
written back with ruamel.yaml in round-trip mode: comments, key order, quoting and layout
survive; only these four values change. The write is atomic (temp file in the same folder,
then os.replace) and the result is parsed and validated before it replaces the real file.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .brief import PACES, split_luts
from .errors import UserError

FIELDS = ("context", "tone", "pace", "prefer_luts")
PATHS = {"context": ("brand", "context"), "tone": ("style", "tone"), "pace": ("style", "pace"),
         "prefer_luts": ("style", "prefer_luts")}
BOM = b"\xef\xbb\xbf"


@dataclass
class Snapshot:
    """The style values as read from disk, plus a fingerprint of the exact bytes."""
    path: Path
    values: dict
    digest: str
    luts: list[str] = field(default_factory=list)       # LUT ids in the catalogue, for validation


class ChangedOnDisk(Exception):
    """config.yaml is not the file the editor showed: someone saved it in between."""


def _yaml():
    try:
        from ruamel.yaml import YAML
    except ImportError:
        raise UserError("Thiếu thư viện ruamel.yaml (cần để ghi config.yaml mà giữ nguyên chú thích). "
                        "Chạy: pip install -e .") from None
    y = YAML()                     # round-trip
    y.preserve_quotes = True
    y.width = 4096                 # never re-wrap long lines
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise UserError(f"Không đọc được '{path}': {e.strerror or e}.") from None


def _load(data: bytes):
    from ruamel.yaml.error import YAMLError
    try:
        doc = _yaml().load(data.removeprefix(BOM).decode("utf-8"))
    except (YAMLError, UnicodeDecodeError) as e:
        raise UserError(f"config.yaml sai cú pháp YAML, không sửa được từ đây: {e}") from None
    if doc is None:
        from ruamel.yaml.comments import CommentedMap
        doc = CommentedMap()
    if not hasattr(doc, "insert"):
        raise UserError("config.yaml phải là một bảng khoá: giá trị.")
    return doc


def _get(doc, section: str, key: str):
    sec = doc.get(section)
    return sec.get(key) if hasattr(sec, "get") else None


def values_of(doc) -> dict:
    ctx = _get(doc, "brand", "context")
    return {"context": str(ctx or "").strip("\n").rstrip(),
            "tone": " ".join(str(_get(doc, "style", "tone") or "").split()),
            "pace": str(_get(doc, "style", "pace") or "").strip().lower(),
            "prefer_luts": split_luts(_get(doc, "style", "prefer_luts"))}


def snapshot(path: Path, catalogue: Path | None = None) -> Snapshot:
    data = _read(path)
    luts: list[str] = []
    if catalogue is not None:
        try:
            from .planner.catalogue import Catalogue
            luts = sorted(it["assetId"] for it in Catalogue.load(catalogue).of_kind("lut"))
        except UserError:
            luts = []
    return Snapshot(path, values_of(_load(data)), _digest(data), luts)


# ---------------------------------------------------------------- writing

def _section(doc, name: str, after: str | None):
    from ruamel.yaml.comments import CommentedMap
    sec = doc.get(name)
    if hasattr(sec, "insert"):
        return sec
    sec = CommentedMap()
    keys = list(doc.keys())
    if name in doc:                                  # present but empty (`style:`): replace in place
        doc[name] = sec
    elif after in keys:
        doc.insert(keys.index(after) + 1, name, sec)
    else:
        doc[name] = sec
    return sec


def apply(doc, values: dict) -> None:
    """Put the four values into a round-trip document, touching nothing else. Assigning to an
    existing key keeps its position and its end-of-line comment."""
    from ruamel.yaml.comments import CommentedSeq
    from ruamel.yaml.scalarstring import LiteralScalarString

    old = values_of(doc)
    changed = {k for k in FIELDS if values[k] != old[k]}   # an unchanged field keeps its exact text
    if "context" in changed:
        brand = _section(doc, "brand", None)
        ctx = values["context"]
        # always a `|` block, like the committed file; "" stays an (empty) value of the key
        brand["context"] = LiteralScalarString(ctx.rstrip("\n") + "\n") if ctx else ""
    if not changed & {"tone", "pace", "prefer_luts"}:
        return
    style = _section(doc, "style", "brand")
    for k in ("tone", "pace"):
        if k in changed or k not in style:
            style[k] = values[k]
    if "prefer_luts" in changed or "prefer_luts" not in style:
        seq = CommentedSeq(values["prefer_luts"])
        seq.fa.set_flow_style()                      # prefer_luts: [a, b] on one line
        style["prefer_luts"] = seq


def render(data: bytes, values: dict) -> bytes:
    doc = _load(data)
    apply(doc, values)
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    out = buf.getvalue().encode("utf-8")
    return BOM + out if data.startswith(BOM) else out


def save(snap: Snapshot, values: dict, overwrite_changed: bool = False,
         validate: Callable[[Path], object] | None = None) -> Path:
    """Write `values` into config.yaml atomically.

    Raises ChangedOnDisk when the file is no longer the one `snap` was read from, unless
    overwrite_changed: then the four values go into the file as it is NOW (other edits made in
    the meantime are kept). `validate(tmp_path)` runs on the finished temp file before it
    replaces config.yaml; if it raises, config.yaml is left untouched.
    """
    path = snap.path
    current = _read(path)
    if _digest(current) != snap.digest and not overwrite_changed:
        raise ChangedOnDisk(str(path))
    new = render(current, values)
    if values_of(_load(new)) != values:              # what we wrote must read back as what we meant
        raise UserError("Không ghi được config.yaml: giá trị đọc lại không khớp (đã giữ nguyên tệp).")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        if validate is not None:
            validate(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


# ---------------------------------------------------------------- the interactive editor

def _show(value) -> str:
    if isinstance(value, list):
        return ", ".join(value) if value else "(trống)"
    return value if value else "(trống)"


def edit(snap: Snapshot, ask: Callable[[str], str], say: Callable[[str], None]) -> dict | None:
    """Ask for the four fields. Enter keeps the current value, '-' clears it. Returns the new
    values, or None when nothing changed."""
    cur = snap.values
    new = dict(cur)
    say("Sửa phong cách (Enter = giữ nguyên, '-' = xoá trống).")

    say("1. context (bối cảnh: nơi này là gì, dành cho ai). Hiện tại:")
    for ln in (cur["context"].splitlines() or ["(trống)"]):
        say(f"     {ln}")
    say("   Gõ nội dung mới, nhiều dòng; kết thúc bằng một dòng chỉ có dấu chấm (.)")
    say("   Enter ngay ở dòng đầu = giữ nguyên.")
    first = ask("   > ")
    if first.strip() == "-":
        new["context"] = ""
    elif first.strip() and first.strip() != ".":
        lines = [first.rstrip()]
        while True:
            ln = ask("   > ")
            if ln.strip() == ".":
                break
            lines.append(ln.rstrip())
        new["context"] = "\n".join(lines).strip("\n")

    tone = ask(f"2. tone (giọng điệu, một dòng) [{_show(cur['tone'])}]: ").strip()
    if tone == "-":
        new["tone"] = ""
    elif tone:
        new["tone"] = " ".join(tone.split())

    while True:
        pace = ask(f"3. pace ({' | '.join(PACES)}) [{_show(cur['pace'])}]: ").strip().lower()
        if pace == "-":
            new["pace"] = ""
        elif pace and pace not in PACES:
            say(f"   Chỉ nhận: {', '.join(PACES)}.")
            continue
        elif pace:
            new["pace"] = pace
        break

    while True:
        raw = ask(f"4. prefer_luts (id LUT, cách nhau bằng dấu phẩy) [{_show(cur['prefer_luts'])}]: ").strip()
        if raw == "-":
            new["prefer_luts"] = []
        elif raw:
            ids = split_luts(raw)
            bad = [i for i in ids if snap.luts and i not in snap.luts]
            if bad:
                say(f"   Không có trong catalogue.json: {', '.join(bad)}")
                say(f"   LUT có sẵn: {', '.join(snap.luts)}")
                continue
            new["prefer_luts"] = ids
        break

    if new == cur:
        say("Không có gì thay đổi.")
        return None
    say("Giá trị mới:")
    for i, k in enumerate(FIELDS, 1):
        mark = "*" if new[k] != cur[k] else " "
        if k == "context":
            say(f" {mark}{i}. context:")
            for ln in (new[k].splitlines() or ["(trống)"]):
                say(f"       {ln}")
        else:
            say(f" {mark}{i}. {k}: {_show(new[k])}")
    return new
