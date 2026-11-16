"""Shared CLI plumbing: UTF-8 console, logging, Vietnamese error mapping without tracebacks."""

from __future__ import annotations

import logging
import sys
from typing import Callable

from .errors import IndexValidationError, PlanValidationError, UserError


def setup_io(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "faster_whisper", "pyscenedetect", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def guarded(fn: Callable[[], int], debug: bool) -> int:
    try:
        return fn()
    except UserError as e:
        if debug:
            raise
        print(f"Lỗi: {e.message}", file=sys.stderr)
        return e.exit_code
    except PlanValidationError as e:
        if debug:
            raise
        print("Lỗi nội bộ (bug của videoauto): kế hoạch dựng tạo ra không khớp schema nên không được ghi.\n"
              f"Hãy báo lỗi kèm chi tiết sau:\n{e}", file=sys.stderr)
        return e.exit_code
    except IndexValidationError as e:
        if debug:
            raise
        print("Lỗi nội bộ (bug của videoauto): chỉ mục tạo ra không khớp schema nên không được ghi.\n"
              f"Hãy báo lỗi kèm chi tiết sau:\n{e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("Đã dừng theo yêu cầu.", file=sys.stderr)
        return 130
    except Exception as e:  # last resort: no traceback for users
        if debug:
            raise
        print(f"Lỗi không mong đợi: {type(e).__name__}: {e}. Chạy lại với --debug để xem chi tiết.",
              file=sys.stderr)
        return 1
