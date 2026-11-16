"""CLI: python -m videoauto.build plan.json --out output/final.mp4"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .cli import guarded, setup_io

log = logging.getLogger("videoauto")


def main(argv: list[str] | None = None, occ=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.build",
                                 description="Dựng video từ EditPlan bằng OpenChatCut (occ CLI).")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--out", type=Path, default=Path("output/final.mp4"))
    ap.add_argument("--config", type=Path, default=None, help="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    setup_io(args.verbose or args.debug)

    def run() -> int:
        from .brief import load_project_config
        from .builder.builder import Builder, load_plan
        pcfg = load_project_config(args.config)
        plan = load_plan(args.plan)
        log.info("dựng %s → %s (data dir OpenChatCut: %s)", args.plan, args.out, pcfg.occ.data_dir)
        rep = Builder(pcfg, occ).build(plan, args.out)
        finish_build(rep, args.out)
        return 0

    return guarded(run, args.debug)


def finish_build(rep, out: Path) -> Path:
    """Write <out>.build.json, log a summary, raise the build's error if it had one."""
    report = out.with_name(out.name + ".build.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    for w in rep.warnings:
        log.warning("cảnh báo: %s", w)
    log.info("occ: %d lệnh (%d lệnh item, %d lỗi); thời gian %.1fs; báo cáo %s",
             len(rep.commands), rep.attempted, rep.failed, rep.seconds, report)
    if rep.error:
        raise rep.error
    log.info("đã xuất %s (%.1f MB)", rep.output, rep.output.stat().st_size / 1e6)
    return report


if __name__ == "__main__":
    sys.exit(main())
