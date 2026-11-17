"""CLI: python -m videoauto.templates --preview --out previews/

Choose overlay templates by eye: renders ONE frame of every vi_ok template (the only ones the
planner may use) with the brand colours from config.yaml and a Vietnamese sample line, then
writes labelled contact sheets and templates.tsv (id, name, group, and which animation and
shadow/outline fields each template exposes). No model calls: catalogue.json + OpenChatCut's
Remotion bundle only.

Without --preview it only prints the vi_ok templates (n, id, name, group).

--measure-colours (maintainers, after the templates change): renders every vi_ok template in
its own colours and records, for the pixels of its text, which colour prop paints the text and
which paints what is behind it. Written to videoauto/planner/overlay_colours.json, which the
planner and builder use to keep brand colours from making text unreadable.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .cli import guarded, setup_io
from .planner import overlays as ov

log = logging.getLogger("videoauto")

SAMPLE_TEXT = "ĐỐT MỠ ĐÓN TẾT"
RENDERER = Path(__file__).with_name("preview_render.mjs")
FRAME_CANDIDATES = (0.6, 0.45, 0.8, 0.3, 0.95)   # same order as OpenChatCut's Vietnamese font audit
MIN_TEXT_PX = 400

# Field classification, by param key. Animation: timing/motion knobs (numbers, switches, choices),
# never text or colour. Shadow/outline: shadow, outline, stroke, border and glow fields of any
# type, but not corner radii and not background glows/blooms (those light the backdrop, not text).
_ANIM = re.compile(r"anim|speed|delay|duration|stagger|reveal|intro|outro|bob|jitter|amplitude|"
                   r"loop|eas(e|ing)|spring|bounce|typewriter", re.I)
_SHADOW = re.compile(r"shadow|outline|stroke|border|glow", re.I)
_NOT_SHADOW = re.compile(r"radius|^bg", re.I)
_NUMBER_LIKE = re.compile(r"[\d\s.,:;%+\-x×/]*")


def animation_fields(tpl: dict) -> list[str]:
    return [p["key"] for p in tpl.get("params", [])
            if p.get("type") in ("number", "integer", "boolean", "select") and _ANIM.search(p["key"])]


def shadow_outline_fields(tpl: dict) -> list[str]:
    return [p["key"] for p in tpl.get("params", [])
            if _SHADOW.search(p["key"]) and not _NOT_SHADOW.search(p["key"])]


def sample_fields(tpl: dict) -> list[str]:
    """Text fields that get the sample line: the ones holding words. Fields whose default is a
    URL, a number/year/percentage, a 1-2 character unit/symbol, or empty keep their default (a
    chart's "1.2, 1.8" data or an avatar URL would only break the render)."""
    out = []
    for p in tpl.get("params", []):
        if p.get("type") != "text" or not p.get("editable", True):
            continue
        d = str(p.get("default") or "").strip()
        if (len(d) <= 2 or p["key"].lower().endswith("url") or d.startswith(("http", "data:", "/"))
                or _NUMBER_LIKE.fullmatch(d)):
            continue
        out.append(p["key"])
    return out


def preview_items(items: list[dict], colors: dict[str, str], text: str = SAMPLE_TEXT,
                  table: dict | None = None) -> list[dict]:
    """vi_ok templates sorted by group then name, numbered from 1, with the props to render:
    the same contrast-checked brand colours a build would use."""
    tpls = sorted((it for it in items if it.get("kind") == "template" and it.get("vi_ok")),
                  key=lambda it: (it.get("category") or "", it.get("name") or "", it["id"]))
    out = []
    for n, it in enumerate(tpls, 1):
        fill = sample_fields(it)
        colour_props, colour_notes = ov.readable_brand_props(it, colors, table)
        out.append({
            "n": n, "id": it["id"], "assetId": it["assetId"], "name": it.get("name", ""),
            "group": it.get("category") or "", "source": it["source"],
            "orientation": ov.orientation(it) or "",
            "fill": fill,
            "overrides": {**colour_props, **{k: text for k in fill}},
            "colour_notes": colour_notes,
            "animation": animation_fields(it), "shadow_outline": shadow_outline_fields(it),
        })
    return out


TSV_COLUMNS = ("n", "id", "name", "group", "orientation", "animation_fields", "shadow_outline_fields",
               "text_fields_filled", "brand_colour_fields", "kept_template_colour", "frame", "sheet",
               "status")


def write_tsv(path: Path, items: list[dict], results: dict[int, dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(TSV_COLUMNS)
        for it in items:
            r = results.get(it["n"], {})
            colour_keys = [k for k in it["overrides"] if k not in it["fill"]]
            w.writerow([it["n"], it["id"], it["name"], it["group"], it["orientation"],
                        ",".join(it["animation"]), ",".join(it["shadow_outline"]),
                        ",".join(it["fill"]), ",".join(colour_keys), " | ".join(it["colour_notes"]),
                        "" if r.get("frame") is None else f'{r["frame"]}/{r.get("durationInFrames", "")}',
                        r.get("sheet", ""), r.get("status", "not_rendered")
                        + (f': {r["error"]}' if r.get("error") else "")])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m videoauto.templates",
                                 description="Xem trước các template chữ dùng được với tiếng Việt (vi_ok).")
    ap.add_argument("--preview", action="store_true",
                    help="dựng 1 khung hình mỗi template, ghi contact sheet + templates.tsv")
    ap.add_argument("--measure-colours", action="store_true",
                    help="(bảo trì) đo màu chữ/nền của mọi template vi_ok, ghi planner/overlay_colours.json")
    ap.add_argument("--out", type=Path, default=Path("previews"), help="thư mục kết quả (mặc định previews/)")
    ap.add_argument("--text", default=SAMPLE_TEXT, help=f'câu mẫu (mặc định "{SAMPLE_TEXT}")')
    ap.add_argument("--only", default=None, help="chỉ các template có id bắt đầu bằng / tên chứa chuỗi này "
                                                 "(phân cách bằng dấu phẩy)")
    ap.add_argument("--concurrency", type=int, default=3, help="số khung dựng song song (mặc định 3)")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (mặc định: trong thư mục hiện tại)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true", help="hiện traceback đầy đủ khi lỗi")
    args = ap.parse_args(argv)
    setup_io(args.verbose or args.debug)
    return guarded(lambda: _run(args), args.debug)


def _run(args) -> int:
    from .brief import load_project_config
    from .builder.occ import Occ
    from .errors import UserError
    from .planner.catalogue import Catalogue

    cfg = load_project_config(args.config)
    cat = Catalogue.load(cfg.catalogue)
    colors = cfg.brand.colors
    items = preview_items(list(cat.items.values()), colors, ov.norm(args.text))
    if args.only:
        keys = [k.strip().lower() for k in args.only.split(",") if k.strip()]
        items = [it for it in items if any(it["id"].startswith(k) or k in it["name"].lower() for k in keys)]
    if not items:
        raise UserError("Không có template vi_ok nào khớp. Kiểm tra catalogue.json (cần trường vi_ok) "
                        "hoặc giá trị --only.")

    if args.measure_colours:
        return _measure(cfg, items, args)
    if not args.preview:
        for it in items:
            print(f'{it["n"]:>3}  {it["id"]}  {it["group"]:<22} {it["name"]}')
        print(f"{len(items)} template vi_ok. Thêm --preview --out previews/ để xem hình.", file=sys.stderr)
        return 0

    if not colors:
        log.warning("config.yaml không có brand.colors: template giữ màu mặc định.")
    occ = Occ(cfg.occ)
    occ.check()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    log.info("dựng %d template vi_ok, mỗi cái 1 khung → %s", len(items), out)

    t0 = time.monotonic()
    results = _render(cfg, out, "preview", items, args, colors=colors)
    write_tsv(out / "templates.tsv", items, results)

    ok = sum(r.get("status") == "ok" for r in results.values())
    faint = [r["n"] for r in results.values() if r.get("status") == "text_not_visible"]
    failed = [r for r in results.values() if r.get("status") == "error"]
    sheets = sorted({r["sheet"] for r in results.values() if r.get("sheet")})
    log.info("xong %.0fs: %d/%d template đã dựng (%d hiện chữ rõ), %d contact sheet, templates.tsv",
             time.monotonic() - t0, ok + len(faint), len(items), ok, len(sheets))
    if faint:
        log.warning("chữ không thấy rõ ở khung đã chọn: #%s", ", #".join(map(str, faint)))
    for r in failed:
        log.warning("lỗi #%d %s: %s", r["n"], r["id"], r.get("error"))
    print(out / "templates.tsv")
    return 0 if not failed else 1


def _render(cfg, out: Path, mode: str, items: list[dict], args, colors: dict | None = None) -> dict[int, dict]:
    """Run preview_render.mjs in the OpenChatCut repo; returns its results by n."""
    from .errors import UserError
    with tempfile.TemporaryDirectory(prefix="videoauto_preview_") as tmp:
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({
            "mode": mode, "repo": str(cfg.occ.repo.resolve()), "out": str(out),
            "concurrency": max(1, args.concurrency), "frameCandidates": FRAME_CANDIDATES,
            "minTextPx": MIN_TEXT_PX, "text": ov.norm(args.text),
            "title": "videoauto overlay preview", "colors": colors or {},
            "templates": [{k: it[k] for k in ("n", "id", "source", "name", "group", "overrides", "fill")}
                          for it in items],
        }, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([cfg.occ.node, str(RENDERER), str(job)], cwd=cfg.occ.repo,
                              stderr=sys.stderr, stdout=sys.stderr)
    results_path = out / ("colour-measure.json" if mode == "measure" else "render-results.json")
    if proc.returncode != 0 or not results_path.exists():
        raise UserError(f"Dựng thất bại (node thoát mã {proc.returncode}). Xem log phía trên; "
                        "kiểm tra OpenChatCut đã `npm install` và `occ render` chạy được.")
    return {r["n"]: r for r in json.loads(results_path.read_text(encoding="utf-8"))["results"] if r}


def _measure(cfg, items: list[dict], args) -> int:
    """Brand-independent: template colours + sample text only. Writes planner/overlay_colours.json."""
    from .builder.occ import Occ
    Occ(cfg.occ).check()
    plain = [{**it, "overrides": {k: ov.norm(args.text) for k in it["fill"]}} for it in items]
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="videoauto_measure_") as tmp:
        log.info("đo màu chữ/nền của %d template vi_ok…", len(plain))
        results = _render(cfg, Path(tmp), "measure", plain, args)
    table = dict(ov.colour_table()) if args.only else {}     # --only updates entries, keeps the rest
    for it in plain:
        r = results.get(it["n"]) or {}
        if r.get("status") == "ok":
            table[it["id"]] = {"name": it["name"], "frame": r["frame"], "defaults": r["defaults"],
                               "groups": r["groups"]}
        else:
            table.pop(it["id"], None)
            log.warning("không đo được #%d %s: %s %s", it["n"], it["name"], r.get("status"), r.get("error", ""))
    ov.COLOUR_TABLE.write_text(json.dumps({
        "generated_by": "python -m videoauto.templates --measure-colours",
        "sample_text": ov.norm(args.text), "min_contrast": ov.MIN_CONTRAST,
        "templates": dict(sorted(table.items())),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ov.colour_table.cache_clear()
    log.info("xong %.0fs: %d/%d template đã đo → %s", time.monotonic() - t0,
             sum(1 for it in plain if it["id"] in table), len(plain), ov.COLOUR_TABLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
