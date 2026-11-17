"""Hard validation of an EditPlan. Used by the planner before writing and by the builder
before running anything. Invalid parts are DROPPED with a Vietnamese warning, never passed on."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..brief import ASPECTS
from . import overlays as ov
from .catalogue import EFFECT_KINDS, Catalogue

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "editplan.schema.json"
MIN_CLIP = 0.5            # seconds
EDGE_SLACK = 0.05         # seconds: tolerance at the end of a source
MAX_FADE = 2.0            # seconds
REJECTING = {"black", "frozen"}


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(plan: Any) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in _validator().iter_errors(plan)]


def clip_frames(clip: dict, fps: int) -> int:
    """Timeline length of a clip. Rounded per edge so consecutive clips never drift."""
    return round(clip["out"] * fps) - round(clip["in"] * fps)


def cut_times(clips: list[dict], fps: int) -> list[float]:
    """Timeline seconds of each cut between consecutive clips."""
    cuts, f = [], 0
    for c in clips[:-1]:
        f += clip_frames(c, fps)
        cuts.append(f / fps)
    return cuts


def transition_window(cut_frame: int, frames: int) -> tuple[int, int]:
    """Frames [start, end) that OpenChatCut paints a GL transition over: centred on the cut,
    start = cut - floor(L/2) (src/editor/TimelineComposition.tsx, glWindows). The window is
    drawn above every track, so any overlay underneath (the logo) disappears for its length."""
    start = cut_frame - frames // 2
    return start, start + frames


def logo_window(brand, total_frames: int, fps: int) -> tuple[int, int] | None:
    """Frames [start, end) where the logo is on screen, or None without a logo."""
    if brand is None or brand.logo is None or total_frames <= 0:
        return None
    if brand.logo_mode == "end":
        from ..brief import LOGO_END_SECONDS
        return max(0, total_frames - round(LOGO_END_SECONDS * fps)), total_frames
    return 0, total_frames


def validate_plan(plan: dict, cat: Catalogue,
                  shots: dict[str, list[dict]] | None = None, brand=None) -> list[str]:
    """Drop every invalid clip/effect/transition/fade in place. Returns the new warnings
    (also appended to plan["warnings"]). `shots` (file -> M1 shots) enables the
    black/frozen check; the builder runs without it."""
    fps = plan["target"]["fps"]
    warn: list[str] = []

    kept: list[dict] = []
    for n, c in enumerate(plan["clips"], 1):
        tag = f"clip {n} ({c['src']} {c['in']:.2f}–{c['out']:.2f}s)"
        src = plan["sources"].get(c["src"])
        if src is None:
            warn.append(f"bỏ {tag}: nguồn không có trong chỉ mục")
            continue
        if not 0 <= c["in"] < c["out"] <= src["duration"] + EDGE_SLACK:
            warn.append(f"bỏ {tag}: in/out ngoài độ dài nguồn ({src['duration']:.2f}s)")
            continue
        c["out"] = min(c["out"], src["duration"])
        if c["out"] - c["in"] < MIN_CLIP or clip_frames(c, fps) < 1:
            warn.append(f"bỏ {tag}: ngắn hơn {MIN_CLIP}s")
            continue
        if shots is not None:
            bad = [s["id"] for s in shots.get(c["src"], [])
                   if set(s["flags"]) & REJECTING and s["start"] < c["out"] and s["end"] > c["in"]]
            if bad:
                warn.append(f"bỏ {tag}: chạm shot đen/đứng hình {', '.join(bad)}")
                continue
        clash = next((k for k in kept if k["src"] == c["src"]
                      and min(k["out"], c["out"]) - max(k["in"], c["in"]) > 1 / fps), None)
        if clash:
            warn.append(f"bỏ {tag}: trùng đoạn nguồn với clip khác ({clash['in']:.2f}–{clash['out']:.2f}s)")
            continue
        effects, luts = [], 0
        for e in c["effects"]:
            kind = cat.kind(e["id"])
            if kind not in EFFECT_KINDS:
                warn.append(f"{tag}: bỏ hiệu ứng '{e['id']}': không có trong catalogue")
                continue
            err = cat.param_error(e["id"], e["params"])
            if err:
                warn.append(f"{tag}: bỏ hiệu ứng '{e['id']}': {err}")
                continue
            if kind == "lut":
                luts += 1
                if luts > 1:
                    warn.append(f"{tag}: bỏ LUT thứ hai '{e['id']}' (một LUT cho mỗi clip)")
                    continue
            effects.append(e)
        c["effects"] = effects
        kept.append(c)
    plan["clips"] = kept

    cuts = cut_times(kept, fps)
    total = sum(clip_frames(c, fps) for c in kept)
    logo = logo_window(brand, total, fps)
    over = validate_overlays(plan, cat, brand, total, warn)
    used: set[int] = set()
    trans = []
    for t in plan["transitions"]:
        tag = f"chuyển cảnh '{t['id']}' tại {t['at']:.2f}s"
        if cat.kind(t["id"]) != "transition":
            warn.append(f"bỏ {tag}: không có trong catalogue")
            continue
        k = next((i for i, x in enumerate(cuts) if abs(x - t["at"]) <= 0.5 / fps), None)
        if k is None:
            warn.append(f"bỏ {tag}: không nằm đúng điểm cắt nào")
            continue
        if k in used:
            warn.append(f"bỏ {tag}: điểm cắt này đã có chuyển cảnh")
            continue
        limit = min(clip_frames(kept[k], fps), clip_frames(kept[k + 1], fps)) // 2
        if t["frames"] > limit:
            warn.append(f"bỏ {tag}: {t['frames']} khung dài hơn nửa clip kề bên ({limit})")
            continue
        if logo is not None:
            ws, we = transition_window(round(cuts[k] * fps), t["frames"])
            if ws < logo[1] and we > logo[0]:
                warn.append(f"bỏ {tag}: chuyển cảnh của OpenChatCut che mất logo trong lúc chạy "
                            "(logo đang hiện ở đoạn này)")
                continue
        ws, we = transition_window(round(cuts[k] * fps), t["frames"])
        hit = next((o for o in over if ws < o[1] and we > o[0]), None)
        if hit is not None:
            warn.append(f"bỏ {tag}: chuyển cảnh của OpenChatCut che mất chữ overlay "
                        f"({hit[0] / fps:.2f}–{hit[1] / fps:.2f}s)")
            continue
        used.add(k)
        trans.append({**t, "at": round(cuts[k], 4)})
    plan["transitions"] = trans

    if kept:
        for edge, clip in (("in", kept[0]), ("out", kept[-1])):
            v = plan["fades"][edge]
            if v > min(MAX_FADE, (clip["out"] - clip["in"]) / 2):
                warn.append(f"bỏ fade {edge} {v}s: quá dài (tối đa {MAX_FADE}s và nửa clip)")
                plan["fades"][edge] = 0
    elif plan["tier"] != "reject":
        plan["tier"] = "reject"
        plan["reason"] = "Không còn clip hợp lệ nào sau khi kiểm tra kế hoạch dựng."

    plan["warnings"].extend(warn)
    return warn


def overlay_frames(o: dict, fps: int) -> tuple[int, int]:
    start = round(o["at"] * fps)
    return start, start + round(o["duration"] * fps)


def validate_overlays(plan: dict, cat: Catalogue, brand, total_frames: int,
                      warn: list[str]) -> list[tuple[int, int]]:
    """Keep only overlays that are safe to render; returns their [start, end) frame windows.

    Rules (each failure drops the overlay with a Vietnamese warning, nothing is guessed):
    vi_ok template only; template orientation matches the target (unknown size refused); every
    non-empty text field is one of the script's TEXT lines, verbatim, each line used once (no invented text, and no
    TEXT lines -> no overlays); fields over MAX_TEXT characters are shortened at a word
    boundary with a warning; inside the timeline, at least MIN_OVERLAY long; no overlap with
    another overlay; not in the logo's corner while the logo is on screen. Brand colours that
    would make the text unreadable are not applied (readable_brand_props); each one is a warning."""
    fps = plan["target"]["fps"]
    lines = [ov.norm(t) for t in plan.get("text_lines") or []]
    # a line already cut to MAX_TEXT (plan.json validated again by the builder) still counts as verbatim
    allowed = {ov.shorten(t): t for t in lines} | {t: t for t in lines}
    logo = logo_window(brand, total_frames, fps)
    w, h = ASPECTS[plan["target"]["aspect"]]
    want = "square" if w == h else ("vertical" if h > w else "horizontal")
    kept, windows, used = [], [], set()
    for n, o in enumerate(sorted(plan.get("overlays") or [], key=lambda o: o["at"]), 1):
        tpl = cat.items.get(o["template"])
        tag = f"overlay {n} ('{o['template'].rsplit(':', 1)[-1]}' tại {o['at']:.2f}s)"
        if tpl is None or tpl.get("kind") != "template":
            warn.append(f"bỏ {tag}: template không có trong catalogue")
            continue
        if tpl.get("vi_ok") is not True:
            warn.append(f"bỏ {tag}: template không đạt kiểm tra font tiếng Việt (vi_ok khác true: "
                        f"{tpl.get('vi_status', 'chưa kiểm tra')})")
            continue
        if ov.orientation(tpl) != want:      # unknown size is refused too: it could be stretched
            size = f"{tpl.get('width')}x{tpl.get('height')}" if ov.orientation(tpl) else "không rõ kích thước"
            warn.append(f"bỏ {tag}: template {size} không hợp khung {plan['target']['aspect']}")
            continue
        names = ov.text_params(tpl)
        extra = set(o["fields"]) - set(names)
        if extra:
            warn.append(f"{tag}: bỏ trường không phải chữ của template: {', '.join(sorted(extra))}")
        fields, bad = {}, None
        for k in names:
            v = ov.norm(o["fields"].get(k, ""))
            if v and v not in allowed:
                bad = f"chữ \"{v}\" không có trong các dòng TEXT của kịch bản (không tự đặt chữ)"
                break
            if v and allowed[v] in used:
                bad = f"dòng TEXT \"{allowed[v]}\" đã dùng cho overlay khác"
                break
            fields[k] = v
        if bad is None and not any(fields.values()):
            bad = ("kịch bản không có dòng TEXT nào" if not lines else "không có chữ nào")
        if bad:
            warn.append(f"bỏ {tag}: {bad}")
            continue
        start, end = overlay_frames(o, fps)
        end = min(end, total_frames)
        if start >= total_frames or end - start < round(ov.MIN_OVERLAY * fps):
            warn.append(f"bỏ {tag}: nằm ngoài video hoặc ngắn hơn {ov.MIN_OVERLAY:g}s")
            continue
        clash = next((x for x in windows if start < x[1] and end > x[0]), None)
        if clash:
            warn.append(f"bỏ {tag}: trùng thời gian với overlay khác ({clash[0] / fps:.2f}–{clash[1] / fps:.2f}s)")
            continue
        if brand is not None and brand.logo is not None and logo and start < logo[1] and end > logo[0]:
            c = ov.corners(o["template"])
            if c is None:
                warn.append(f"bỏ {tag}: không rõ template đặt chữ ở đâu, không bảo đảm được là không đè logo")
                continue
            if brand.logo_position in c:
                warn.append(f"bỏ {tag}: template đặt chữ ở góc {brand.logo_position}, trùng góc logo")
                continue
        for k, v in fields.items():
            if len(v) > ov.MAX_TEXT:
                fields[k] = ov.shorten(v)
                warn.append(f"{tag}: chữ \"{v}\" dài {len(v)} ký tự (tối đa {ov.MAX_TEXT}), "
                            f"rút gọn thành \"{fields[k]}\" để không bị cắt")
        if brand is not None:
            warn.extend(f"{tag}: {note}" for note in ov.readable_brand_props(tpl, brand.colors)[1])
        used.update(allowed[v] for v in fields.values() if v)
        kept.append({"template": o["template"], "at": round(start / fps, 4),
                     "duration": round((end - start) / fps, 4), "fields": fields})
        windows.append((start, end))
    plan["overlays"] = kept
    return windows
