"""CLI: python -m videoauto.build plan.json --out output/final.mp4"""

from __future__ import annotations

import argparse
import json
import logging
import os
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
    ap.add_argument("--footage", type=Path, default=None, help="ghi đè footage_dir")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    setup_io(args.verbose or args.debug)

    def run() -> int:
        from .brief import load_project_config
        from .builder.builder import Builder, load_plan
        pcfg = load_project_config(args.config)
        if args.footage:
            pcfg.footage_dir = args.footage.resolve()
        plan = load_plan(args.plan)
        log.info("dựng %s → %s (data dir OpenChatCut: %s)", args.plan, args.out, pcfg.occ.data_dir)
        rep = Builder(pcfg, occ).build(plan, args.out)
        finish_build(rep, args.out)
        return 0

    return guarded(run, args.debug)


def keep_failed_video(video: Path) -> Path | None:
    """A build that failed never deletes its video: it may be a complete render that a later
    check refused (that is how a finished 21.6 MB video was lost). The file is renamed
    <name>.loi.mp4 (or <name>.loi-2.mp4, ...) so the name is free again and nobody mistakes it
    for a good result. Returns the new path, or None if there was no file."""
    if not video.is_file():
        return None
    stem, n = f"{video.stem}.loi", 1
    dest = video.with_name(f"{stem}{video.suffix}")
    while dest.exists():
        n += 1
        dest = video.with_name(f"{stem}-{n}{video.suffix}")
    try:
        os.replace(video, dest)
    except OSError as e:
        log.warning("không đổi tên được video lỗi %s (%s); để nguyên", video, e.strerror or e)
        return video
    return dest


def finish_build(rep, out: Path) -> Path:
    """Write <out>.build.json, log a summary, raise the build's error if it had one. On an error
    the video (if any) is kept as <name>.loi.mp4, never deleted."""
    report = out.with_name(out.name + ".build.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    if rep.error:
        kept = keep_failed_video(out)
        if kept is not None:
            rep.kept_video = kept
            rep.error.kept = kept
            rep.error.message += f" Video đã dựng được giữ lại để xem: {kept}"
            rep.error.args = (rep.error.message,)
    report.write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    for w in rep.warnings:
        log.warning("cảnh báo: %s", w)
    log.info("occ: %d lệnh (%d lệnh item, %d lỗi); thời gian %.1fs; báo cáo %s",
             len(rep.commands), rep.attempted, rep.failed, rep.seconds, report)
    if rep.encoder:
        log.info("mã hoá: %s", encoder_summary(rep.encoder))
    if rep.error:
        raise rep.error
    log.info("đã xuất %s (%.1f MB)", rep.output, rep.output.stat().st_size / 1e6)
    return report


def encoder_summary(enc: dict | None) -> str:
    """One line for the run summary: what encoded the video, and why that one."""
    if not enc:
        return "(không có)"
    from .gpu import LABELS
    used = enc["used"]
    head = f"{used} ({LABELS.get(used, 'không xác định')})" + (", GPU" if enc["hardware"] else "")
    if enc.get("fallback"):
        return f"{head}; {enc['fallback']}"
    return f"{head}; gpu_encode={enc['mode']}"


if __name__ == "__main__":
    sys.exit(main())
