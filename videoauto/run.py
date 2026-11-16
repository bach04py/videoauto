"""CLI: python -m videoauto.run <folder> --out output/

One command: index -> plan -> build. The three stage commands (videoauto.index / .plan /
.build) stay available for debugging; this module calls the same code.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .cli import guarded, setup_io

log = logging.getLogger("videoauto")


def _stage(n: int, title: str) -> None:
    log.info("━━ [%d/3] %s", n, title)     # no blank lines: PowerShell shows them as RemoteException


def main(argv: list[str] | None = None, provider=None, occ=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.run",
                                 description="Từ thư mục video thô ra video hoàn chỉnh: lập chỉ mục → "
                                             "lập kế hoạch → dựng.")
    ap.add_argument("folder", type=Path, help="thư mục video thô (nhạc đặt trong thư mục con music/)")
    ap.add_argument("--out", type=Path, default=Path("output"), help="thư mục kết quả (mặc định output/)")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (mặc định: trong thư mục hiện tại)")
    ap.add_argument("--vision-config", type=Path, default=None, help="videoauto.toml (tuỳ chọn)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true", help="hiện traceback đầy đủ khi lỗi")
    args = ap.parse_args(argv)
    setup_io(args.verbose or args.debug)
    return guarded(lambda: _run(args, provider, occ), args.debug)


def _run(args, provider, occ) -> int:
    from .brief import load_project_config
    from .build import finish_build
    from .builder.builder import Builder
    from .builder.occ import Occ
    from .config import load_config
    from .errors import PlanRejected, UserError
    from .indexer import Indexer, collect
    from .plan import log_plan, save_plan
    from .planner.catalogue import Catalogue
    from .planner.planner import make_plan
    from .tools import check_ffmpeg
    from .vision import make_provider

    t_all = time.monotonic()
    out = args.out
    index_dir, plan_path, video = out / "index", out / "plan.json", out / "final.mp4"

    # ---- preflight: everything that can fail in a second fails before the slow work
    log.info("kiểm tra môi trường…")
    pcfg = load_project_config(args.config)
    if not args.folder.is_dir():
        raise UserError(f"'{args.folder}' không phải thư mục. Truyền thư mục chứa video thô.")
    pcfg.media_dir = args.folder.resolve()                      # the folder given here wins
    vcfg = load_config(args.vision_config)
    check_ffmpeg()
    Catalogue.load(pcfg.catalogue)
    occ = occ or Occ(pcfg.occ)
    occ.check()
    provider = provider or make_provider(vcfg["vision"])
    provider.check()
    sources = collect(args.folder, index_dir)
    log.info("ok: %d tệp, brief %gs %s, OpenChatCut tại %s", len(sources), pcfg.brief.duration,
             pcfg.brief.aspect, pcfg.occ.repo)
    times: dict[str, float] = {}

    # ---- 1. index
    _stage(1, f"Lập chỉ mục {args.folder} → {index_dir}")
    t = time.monotonic()
    rep = Indexer(vcfg, index_dir, provider=provider).run(sources)
    times["index"] = time.monotonic() - t
    index_calls = rep.model_calls
    log.info("lập chỉ mục: %d tệp mới, %d trúng cache, %d lỗi, %d lượt gọi model, %.1fs",
             len(rep.written), rep.cache_hits, len(rep.failed), index_calls, times["index"])
    if rep.stopped:
        raise rep.stopped
    for path, msg in rep.failed:
        log.warning("bỏ qua %s: %s", path.name, msg)
    if not rep.written and not rep.cache_hits:
        raise UserError("Không lập chỉ mục được tệp nào, nên không thể lập kế hoạch. Xem các lỗi ở trên.")

    # ---- 2. plan
    _stage(2, f"Lập kế hoạch dựng → {plan_path}")
    t = time.monotonic()
    plan = make_plan(pcfg, index_dir, provider)
    save_plan(plan, plan_path)
    times["plan"] = time.monotonic() - t
    log_plan(plan, plan_path, times["plan"])
    if plan["tier"] == "reject":
        raise PlanRejected(f"Kế hoạch bị từ chối, không dựng video: {plan['reason']}")

    # ---- 3. build
    _stage(3, f"Dựng video bằng OpenChatCut → {video}")
    t = time.monotonic()
    brep = Builder(pcfg, occ).build(plan, video)
    times["build"] = time.monotonic() - t
    report = finish_build(brep, video)

    log.info("━━ XONG trong %.0fs (lập chỉ mục %.0fs, kế hoạch %.0fs, dựng %.0fs); %d lượt gọi model",
             time.monotonic() - t_all, times["index"], times["plan"], times["build"],
             index_calls + plan["model_calls"])
    log.info("   video:     %s", video.resolve())
    log.info("   kế hoạch:  %s", plan_path.resolve())
    log.info("   báo cáo:   %s", report.resolve())
    log.info("   chỉ mục:   %s", index_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
