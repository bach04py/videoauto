"""CLI: python -m videoauto.run <folder> --out output/

One command: index -> plan -> build. The three stage commands (videoauto.index / .plan /
.build) stay available for debugging; this module calls the same code.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .build import encoder_summary
from .cli import guarded, setup_io

log = logging.getLogger("videoauto")


def _stage(n: int, title: str) -> None:
    log.info("━━ [%d/3] %s", n, title)     # no blank lines: PowerShell shows them as RemoteException


def main(argv: list[str] | None = None, provider=None, occ=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.run",
                                 description="Từ thư mục video thô ra video hoàn chỉnh: lập chỉ mục → "
                                             "lập kế hoạch → dựng.")
    ap.add_argument("folder", type=Path, nargs="?", default=None,
                    help="thư mục video thô (mặc định: footage_dir trong config.yaml)")
    ap.add_argument("--footage", type=Path, default=None, help="như folder: ghi đè footage_dir")
    ap.add_argument("--script", type=Path, default=None,
                    help="tệp kịch bản cần theo (ghi đè script_file trong config.yaml)")
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
    from .errors import UserError
    from .features import check_script_file
    from .indexer import collect
    from .preflight import Problems, check_tools

    t_all = time.monotonic()
    out = args.out
    index_dir, plan_path, video = out / "index", out / "plan.json", out / "final.mp4"

    # ---- preflight: everything that can fail in a second fails before the slow work, and every
    # problem is reported in one message (not one per run)
    log.info("kiểm tra môi trường…")
    problems = Problems()
    pcfg = load_project_config(args.config, problems=problems.items)   # a bad logo is collected
    if args.folder and args.footage and args.folder.resolve() != args.footage.resolve():
        raise UserError("Chỉ truyền thư mục video một lần: hoặc làm tham số, hoặc --footage.")
    folder = args.folder or args.footage or pcfg.footage_dir         # command line wins over config
    if not (args.folder or args.footage) and (folder is None or not folder.is_dir()):
        saved = _saved_footage()        # config.yaml still has the sample path: use the one setup saved
        if saved is not None:
            log.info("footage_dir trong config.yaml chưa dùng được; dùng thư mục video đã lưu: %s", saved)
            folder = saved
    if folder is None:
        problems.add("Chưa biết lấy video ở đâu: đặt footage_dir trong config.yaml, hoặc chạy "
                     "python -m videoauto.run <thư mục video>.")
    elif not folder.is_dir():
        problems.add(f"Thư mục video '{folder}' không tồn tại. Sửa footage_dir trong config.yaml "
                     "hoặc truyền đúng thư mục.")
    else:
        pcfg.footage_dir = folder = folder.resolve()
    script, script_ok = "", True
    if args.script is not None:
        if args.script.is_file():
            pcfg.script_file = args.script.resolve()
        else:
            problems.add(f"Không tìm thấy tệp kịch bản '{args.script}' (--script).")
            script_ok = False
    # a missing script_file is never replaced by one the model writes
    if script_ok and problems.attempt(check_script_file, pcfg)[0]:
        script = problems.attempt(pcfg.script_text)[1] or ""
    env = check_tools(pcfg, problems, occ=occ, provider=provider, vision_config=args.vision_config,
                      script=script)
    problems.raise_if_any()
    vcfg, occ, provider, enc = env.vcfg, env.occ, env.provider, env.enc
    sources = collect(folder, index_dir)
    log.info("ok: %d tệp trong %s, brief %gs %s, OpenChatCut tại %s", len(sources), folder,
             pcfg.brief.duration, pcfg.brief.aspect, pcfg.occ.repo)
    b = pcfg.brand
    log.info("mã hoá video: %s (gpu_encode=%s)", enc.label(), enc.mode)
    log.info("thương hiệu: %s; logo: %s; kịch bản: %s", b.name or "(không)",
             f"{b.logo.name} ({b.logo_position}, {b.logo_scale:g}, {b.logo_mode})" if b.logo else "(không)",
             f"theo {pcfg.script_file.name} ({len(script)} ký tự)" if script else "model tự viết")
    res = run_pipeline(pcfg, vcfg, provider, occ, sources, index_dir, plan_path, video, log_stage=True)
    log.info("━━ XONG trong %.0fs (lập chỉ mục %.0fs, kế hoạch %.0fs, dựng %.0fs); %d lượt gọi model",
             time.monotonic() - t_all, res.times["index"], res.times["plan"], res.times["build"],
             res.model_calls)
    log.info("   video:     %s", video.resolve())
    log.info("   kế hoạch:  %s", plan_path.resolve())
    log.info("   mã hoá:    %s", encoder_summary(res.encoder))
    log.info("   báo cáo:   %s", res.report.resolve())
    log.info("   chỉ mục:   %s", index_dir.resolve())
    return 0


def _saved_footage() -> Path | None:
    from .watch import load_saved
    raw = load_saved().get("footage")
    p = Path(raw) if raw else None
    return p if p is not None and p.is_dir() else None


@dataclass
class PipelineResult:
    """Filled in as the pipeline goes, so a caller still sees the stage and calls after a failure."""
    stage: str = ""                                   # "index" | "plan" | "build" | "done"
    model_calls: int = 0
    times: dict[str, float] = field(default_factory=dict)
    report: Path | None = None
    encoder: dict | None = None                       # gpu.verify() record of the render


def run_pipeline(pcfg, vcfg, provider, occ, sources, index_dir: Path, plan_path: Path, video: Path,
                 result: PipelineResult | None = None, on_stage: Callable[[str], None] | None = None,
                 log_stage: bool = False) -> PipelineResult:
    """index -> plan -> build for already-checked config, provider and occ. Raises on failure;
    `result` (if passed) keeps the stage reached and the model calls spent so far."""
    from .build import finish_build
    from .builder.builder import Builder
    from .errors import PlanRejected, UserError
    from .features import check_plan_text
    from .indexer import Indexer
    from .plan import log_plan, save_plan
    from .planner.planner import make_plan

    from .speech import merge_whisper
    res = result or PipelineResult()
    folder = pcfg.footage_dir
    vcfg = merge_whisper(vcfg, getattr(pcfg, "whisper", None))     # config.yaml wins over videoauto.toml

    def stage(key: str, n: int, title: str) -> None:
        res.stage = key
        if log_stage:
            _stage(n, title)
        if on_stage:
            on_stage(key)

    # ---- 1. index
    stage("index", 1, f"Lập chỉ mục {folder} → {index_dir}")
    t = time.monotonic()
    rep = Indexer(vcfg, index_dir, provider=provider).run(sources)
    res.times["index"] = time.monotonic() - t
    res.model_calls += rep.model_calls
    log.info("lập chỉ mục: %d tệp mới, %d trúng cache, %d lỗi, %d lượt gọi model, %.1fs",
             len(rep.written), rep.cache_hits, len(rep.failed), rep.model_calls, res.times["index"])
    if rep.normalized:
        log.info("lập chỉ mục: %d clip có thẻ xoay đã được chuẩn hoá đứng thẳng (dựng từ bản chuẩn hoá trong "
                 "%s): %s", len(rep.normalized), index_dir / "normalized", ", ".join(rep.normalized))
    if rep.stopped:
        raise rep.stopped
    for path, msg in rep.failed:
        log.warning("bỏ qua %s: %s", path.name, msg)
    if not rep.written and not rep.cache_hits:
        raise UserError("Không lập chỉ mục được tệp nào, nên không thể lập kế hoạch. Xem các lỗi ở trên.")

    # ---- 2. plan
    stage("plan", 2, f"Lập kế hoạch dựng → {plan_path}")
    t = time.monotonic()
    plan = make_plan(pcfg, index_dir, provider)
    res.model_calls += plan["model_calls"]
    save_plan(plan, plan_path)
    res.times["plan"] = time.monotonic() - t
    log_plan(plan, plan_path, res.times["plan"])
    if plan["tier"] == "reject":
        raise PlanRejected(f"Kế hoạch bị từ chối, không dựng video: {plan['reason']}")
    check_plan_text(plan)                  # every TEXT line has its overlay, or no render at all

    # ---- 3. build
    stage("build", 3, f"Dựng video bằng OpenChatCut → {video}")
    t = time.monotonic()
    brep = Builder(pcfg, occ).build(plan, video)
    res.times["build"] = time.monotonic() - t
    res.encoder = brep.encoder
    res.report = finish_build(brep, video)
    res.stage = "done"
    return res


if __name__ == "__main__":
    sys.exit(main())
