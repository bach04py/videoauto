"""Environment check shared by python -m videoauto.run and python -m videoauto.watch.

Every problem is collected and reported in ONE message: a fresh machine used to need four runs
(missing logo -> fix -> missing footage folder -> fix -> missing script -> fix -> missing
catalogue). A check that depends on an earlier one (the catalogue needs a working OpenChatCut)
is skipped when that one failed, and the earlier problem is what gets reported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import UserError

log = logging.getLogger("videoauto")


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        if message not in self.items:
            self.items.append(message)

    def attempt(self, fn: Callable, *args, **kw) -> tuple[bool, Any]:
        """(True, result), or (False, None) with the UserError's message recorded."""
        try:
            return True, fn(*args, **kw)
        except UserError as e:
            self.add(e.message)
            return False, None

    def raise_if_any(self) -> None:
        if not self.items:
            return
        if len(self.items) == 1:
            raise UserError(self.items[0])
        lines = "\n".join(f"  {n}. {m}" for n, m in enumerate(self.items, 1))
        raise UserError(f"Còn {len(self.items)} việc cần sửa trước khi chạy (sửa hết rồi chạy lại một lần):\n{lines}")


@dataclass
class Checked:
    vcfg: dict | None = None
    occ: Any = None
    cat: Any = None
    enc: Any = None
    provider: Any = None
    notes: list[str] = field(default_factory=list)


def check_tools(pcfg, problems: Problems, occ=None, provider=None, vision_config=None,
                script: str | None = None, say: Callable[[str], None] | None = None) -> Checked:
    """ffmpeg, the vision config and provider, OpenChatCut (repo, tsx, Node), the GPU choice,
    catalogue.json (regenerated if missing or stale), style and TEXT checks. Never raises:
    every failure goes into `problems`. script=None: no TEXT check (the watcher checks each
    script when it arrives)."""
    from . import gpu, occ_catalogue
    from .builder.occ import Occ
    from .config import load_config
    from .features import check_style, check_text_overlays
    from .planner.catalogue import Catalogue
    from .tools import check_ffmpeg

    out = Checked()
    problems.attempt(check_ffmpeg)
    _, out.vcfg = problems.attempt(load_config, vision_config)
    out.occ = occ or Occ(pcfg.occ)
    occ_problems = out.occ.problems() if hasattr(out.occ, "problems") else (
        [] if problems.attempt(out.occ.check)[0] else None)
    for m in occ_problems or []:
        problems.add(m)
    occ_ok = occ_problems == []
    if occ_ok:
        _, out.enc = problems.attempt(gpu.choose, pcfg.occ.gpu_encode, pcfg.occ.repo)
        problems.attempt(occ_catalogue.ensure, pcfg.catalogue, pcfg.occ.repo, pcfg.occ.node,
                         **({"say": say} if say else {}))
    if occ_ok or pcfg.catalogue.exists():
        ok, out.cat = problems.attempt(Catalogue.load, pcfg.catalogue)
        if ok:
            problems.attempt(check_style, pcfg, out.cat)
            if script is not None:
                problems.attempt(check_text_overlays, pcfg, out.cat, script)
    else:
        problems.add(f"catalogue.json ('{pcfg.catalogue}') chưa có; videoauto tự tạo nó khi OpenChatCut "
                     "dùng được (sửa lỗi OpenChatCut ở trên, không cần làm gì thêm).")
    if out.vcfg is not None:
        if provider is None:
            from .vision import make_provider
            ok, provider = problems.attempt(make_provider, out.vcfg["vision"])
        if provider is not None:
            problems.attempt(provider.check)
    out.provider = provider
    return out
