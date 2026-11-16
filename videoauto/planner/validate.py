"""Hard validation of an EditPlan. Used by the planner before writing and by the builder
before running anything. Invalid parts are DROPPED with a Vietnamese warning, never passed on."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

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


def validate_plan(plan: dict, cat: Catalogue,
                  shots: dict[str, list[dict]] | None = None) -> list[str]:
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
