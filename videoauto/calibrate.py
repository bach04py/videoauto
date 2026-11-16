"""--calibrate-blur: local analysis only (no vision provider, no Whisper, no index writes).

Uses the exact same shot spans, keyframes, sharpness metric and blur rule as indexing, then
prints every shot sorted by sharpness and writes contact sheets of the flagged shots and of the
unflagged shots near the threshold, so a person can judge the boundary by eye.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from . import frames, keyframes, quality
from .cache import sha256_of
from .crop import detect_crop
from .errors import UserError
from .indexer import Source
from .probe import probe
from .vision import contact_sheet

log = logging.getLogger("videoauto")

NEAR_LOW, NEAR_HIGH = 0.67, 1.5   # "near the boundary" = score within these multiples of threshold


@dataclass
class Row:
    file: str
    shot: str
    start: float
    end: float
    score: float          # max over the shot's keyframes, as indexing uses
    keyframe: Path        # the keyframe that produced the score
    black: bool
    flagged: bool
    near: bool
    no: int = 0           # row number = cell label on the contact sheets


def calibrate_blur(cfg: dict, sources: list[Source], out_dir: Path) -> tuple[list[Row], list[Path], int]:
    thr = cfg["quality"]["blur_threshold"]
    rows: list[Row] = []
    failed = 0
    for src in sources:
        if src.kind != "video":
            log.info("bỏ qua %s (nhạc)", src.path.name)
            continue
        try:
            rows += _shots(cfg, src.path, out_dir, thr)
        except UserError as e:
            log.error("lỗi: %s", e.message)
            failed += 1
    rows.sort(key=lambda r: r.score)
    for i, r in enumerate(rows, 1):
        r.no = i
    return rows, _write_outputs(rows, out_dir), failed


def _shots(cfg: dict, path: Path, out_dir: Path, thr: float) -> list[Row]:
    log.info("phân tích %s", path.name)
    sha = sha256_of(path)
    pr = probe(path)
    if not pr.has_video:
        raise UserError(f"'{path.name}' không có luồng hình ảnh.")
    crop = detect_crop(path, pr.width, pr.height)
    fa = frames.analyse(path, pr, crop, cfg["shots"]["threshold"], cfg["shots"]["min_scene_len"])
    det = quality.detect(path, pr.duration, False, cfg["quality"]["silence_db"], crop)
    fdir = out_dir / "frames" / sha
    rows = []
    for span in frames.shot_spans(fa, pr.duration):
        _times, kpaths = keyframes.for_shot(path, span, cfg["keyframes"], fdir, crop)
        scored = [(quality.sharpness(p), p) for p in kpaths]
        score, best = max(scored)
        black = quality.coverage(det.black, span.start, span.end) >= cfg["quality"]["black_cover"]
        rows.append(Row(path.name, span.id, span.start, span.end, round(score, 1), best, black,
                        quality.is_blurry(score, thr, black),
                        not black and NEAR_LOW * thr <= score < NEAR_HIGH * thr))
    return rows


def _write_outputs(rows: list[Row], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("calibrate_blur_*.jpg"):
        old.unlink()
    with open(out_dir / "calibrate_blur.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["no", "file", "shot", "start", "end", "sharpness", "blurry", "near", "black", "keyframe"])
        for r in rows:
            w.writerow([r.no, r.file, r.shot, r.start, r.end, r.score, int(r.flagged), int(r.near),
                        int(r.black), str(r.keyframe.resolve())])
    sheets = []
    for name, group in (("flagged", [r for r in rows if r.flagged]),
                        ("near", [r for r in rows if r.near and not r.flagged])):
        for k in range(0, len(group), contact_sheet.CELLS):
            chunk = group[k:k + contact_sheet.CELLS]
            out = out_dir / f"calibrate_blur_{name}_{k // contact_sheet.CELLS + 1:02d}.jpg"
            contact_sheet.build([r.keyframe for r in chunk], out, labels=[str(r.no) for r in chunk])
            sheets.append(out)
    return sheets


def print_report(rows: list[Row], sheets: list[Path], thr: float, out_dir: Path) -> None:
    print(f"\nNgưỡng mờ hiện tại: {thr:g} (độ nét < ngưỡng → blurry). '~' = gần ngưỡng "
          f"({NEAR_LOW:g}×–{NEAR_HIGH:g}×). Sắp xếp theo độ nét tăng dần.\n")
    print(f"{'#':>3}  {'độ nét':>7}  {'mờ?':<4} {'':1}  {'tệp':<24} {'shot':<4}  {'thời gian':<15}  keyframe")
    for r in rows:
        mark = "BLACK" if r.black else ("CÓ" if r.flagged else "-")
        print(f"{r.no:>3}  {r.score:>7.1f}  {mark:<4} {'~' if r.near else ' ':1}  {r.file[:24]:<24} "
              f"{r.shot:<4}  {r.start:6.2f}–{r.end:<7.2f}  {r.keyframe.resolve()}")
    flagged = sum(r.flagged for r in rows)
    print(f"\n{len(rows)} shot, {flagged} bị gắn cờ mờ, {sum(r.near for r in rows)} gần ngưỡng.")
    print(f"Bảng đầy đủ: {(out_dir / 'calibrate_blur.tsv').resolve()}")
    for s in sheets:
        print(f"Contact sheet: {s.resolve()}  (số trên ô = cột #)")
    if not sheets:
        print("Không có shot nào bị gắn cờ hoặc gần ngưỡng, nên không tạo contact sheet.")
    print("Không gọi model nào (chỉ phân tích cục bộ); không ghi chỉ mục.")
