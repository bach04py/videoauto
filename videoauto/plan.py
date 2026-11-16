"""CLI: python -m videoauto.plan <index-folder> --out plan.json"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .cli import guarded, setup_io

log = logging.getLogger("videoauto")
EXIT_REJECT = 5


def main(argv: list[str] | None = None, provider=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.plan",
                                 description="Lập kế hoạch dựng (EditPlan) từ chỉ mục M1.")
    ap.add_argument("index_dir", type=Path, help="thư mục chỉ mục của M1 (vd .index)")
    ap.add_argument("--out", type=Path, default=Path("plan.json"))
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (brief + đường dẫn)")
    ap.add_argument("--vision-config", type=Path, default=None, help="videoauto.toml (provider)")
    ap.add_argument("--provider", choices=["claude", "openai-compatible"], default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    setup_io(args.verbose or args.debug)

    def run() -> int:
        from .brief import load_project_config
        from .config import load_config
        from .planner.planner import make_plan
        from .vision import make_provider
        t0 = time.monotonic()
        pcfg = load_project_config(args.config)
        vcfg = load_config(args.vision_config)
        if args.provider:
            vcfg["vision"]["provider"] = args.provider
        plan = make_plan(pcfg, args.index_dir, provider or make_provider(vcfg["vision"]))
        tmp = args.out.with_name(args.out.name + ".tmp")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, args.out)

        total = sum(c["out"] - c["in"] for c in plan["clips"])
        for w in plan["warnings"]:
            log.warning("cảnh báo: %s", w)
        log.info("─" * 60)
        log.info("kế hoạch: tier %s, %d clip, %.1fs / mục tiêu %gs, %d chuyển cảnh, độ tin cậy %.2f",
                 plan["tier"], len(plan["clips"]), total, plan["target"]["duration"],
                 len(plan["transitions"]), plan["confidence"])
        log.info("model: %d lượt gọi; thời gian %.1fs; đã ghi %s", plan["model_calls"],
                 time.monotonic() - t0, args.out)
        if plan["tier"] == "reject":
            print(f"Từ chối: {plan['reason']}", file=sys.stderr)
            return EXIT_REJECT
        return 0

    return guarded(run, args.debug)


if __name__ == "__main__":
    sys.exit(main())
