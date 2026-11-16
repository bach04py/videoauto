"""CLI: python -m videoauto.index <file|folder> --out .index/"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .errors import IndexValidationError, LimitReached, UserError
from .tools import check_ffmpeg

log = logging.getLogger("videoauto")


def _setup_io(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "faster_whisper", "pyscenedetect", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.index",
                                 description="Lập chỉ mục video/nhạc thành JSON.")
    ap.add_argument("target", type=Path, help="tệp hoặc thư mục")
    ap.add_argument("--out", type=Path, default=Path(".index"), help="thư mục cache (mặc định .index/)")
    ap.add_argument("--config", type=Path, default=None, help="tệp videoauto.toml")
    ap.add_argument("--provider", choices=["claude", "openai-compatible"], default=None)
    ap.add_argument("--calibrate-blur", action="store_true",
                    help="chỉ phân tích cục bộ (không gọi model): in bảng độ nét từng shot + contact sheet")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true", help="hiện traceback đầy đủ khi lỗi")
    args = ap.parse_args(argv)
    _setup_io(args.verbose or args.debug)

    try:
        from .indexer import Indexer, collect
        cfg = load_config(args.config)
        if args.provider:
            cfg["vision"]["provider"] = args.provider
        check_ffmpeg()
        sources = collect(args.target, args.out)
        if args.calibrate_blur:
            from .calibrate import calibrate_blur, print_report
            rows, sheets, failed = calibrate_blur(cfg, sources, args.out)
            print_report(rows, sheets, cfg["quality"], args.out)
            return 1 if failed else 0
        report = Indexer(cfg, args.out).run(sources)
    except UserError as e:
        if args.debug:
            raise
        print(f"Lỗi: {e.message}", file=sys.stderr)
        return e.exit_code
    except IndexValidationError as e:
        if args.debug:
            raise
        print("Lỗi nội bộ (bug của videoauto): chỉ mục tạo ra không khớp schema nên không được ghi.\n"
              f"Hãy báo lỗi kèm chi tiết sau:\n{e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("Đã dừng theo yêu cầu. Các tệp đã xong vẫn nằm trong cache.", file=sys.stderr)
        return 130
    except Exception as e:  # last resort: no traceback for users
        if args.debug:
            raise
        print(f"Lỗi không mong đợi: {type(e).__name__}: {e}. Chạy lại với --debug để xem chi tiết.",
              file=sys.stderr)
        return 1

    spc = report.shots_per_call
    log.info("─" * 60)
    log.info("tổng kết: %d tệp đã ghi, %d trúng cache, %d lỗi", len(report.written),
             report.cache_hits, len(report.failed))
    log.info("model: %d lượt gọi; số shot mỗi lượt: %s%s", report.model_calls, spc or "[]",
             f" (trung bình {sum(spc) / len(spc):.1f})" if spc else "")
    if report.stopped:
        print(f"Dừng: {report.stopped.message}", file=sys.stderr)
        return LimitReached.exit_code
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
