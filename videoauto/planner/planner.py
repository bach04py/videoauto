"""M2 planner: M1 indexes + brief + catalogue -> ONE model call -> validated EditPlan."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import schema as index_schema
from ..brief import ProjectConfig
from ..cache import sha256_of
from ..errors import PlanValidationError, UserError
from ..vision.base import ProviderTransient, VisionProvider
from . import overlays as ov
from .catalogue import Catalogue
from .validate import REJECTING, cut_times, schema_errors, validate_plan

log = logging.getLogger("videoauto")

MIN_CONFIDENCE_FULL = 0.6
DURATION_TOLERANCE = 0.2      # total length within ±20 % of the brief, else a warning


@dataclass
class Material:
    indexes: dict[str, dict[str, Any]] = field(default_factory=dict)   # file name -> M1 index
    warnings: list[str] = field(default_factory=list)

    def usable(self) -> list[tuple[str, dict]]:
        return [(f, s) for f, ix in self.indexes.items() for s in ix["shots"]
                if not set(s["flags"]) & REJECTING]

    @property
    def usable_seconds(self) -> float:
        return sum(s["end"] - s["start"] for _, s in self.usable())


def load_material(index_dir: Path, media_dir: Path | None) -> Material:
    if not index_dir.is_dir():
        raise UserError(f"Không tìm thấy thư mục chỉ mục '{index_dir}'. Chạy M1 trước: "
                        f"python -m videoauto.index <thư mục video> --out {index_dir}")
    m = Material()
    media: dict[str, list[Path]] = {}
    if media_dir is not None:
        for p in media_dir.rglob("*"):
            if p.is_file() and "music" not in (x.lower() for x in p.relative_to(media_dir).parts[:-1]):
                media.setdefault(p.name, []).append(p)
    # Warnings are how the user learns the footage could not cover part of the script, so routine
    # cache leftovers must not bury them: indexes whose content is no longer in the footage folder
    # (a clip replaced or removed) are counted in ONE line. Only a real ambiguity gets its own line.
    orphans = 0
    candidates: dict[str, list[tuple[dict[str, Any], Path | None]]] = {}
    for p in sorted(index_dir.glob("*.json")):
        try:
            ix = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m.warnings.append(f"bỏ qua {p.name}: không đọc được JSON")
            continue
        if index_schema.errors(ix):
            m.warnings.append(f"bỏ qua {p.name}: không khớp schema chỉ mục hiện tại (chạy lại M1)")
            continue
        if ix["kind"] != "video":
            continue                                    # music is not used in this slice
        found: Path | None = None
        if media_dir is not None:
            found = next((q for q in media.get(ix["file"], []) if sha256_of(q) == ix["sha256"]), None)
            if found is None:
                orphans += 1                            # its content is no longer in the folder
                continue
        candidates.setdefault(ix["file"], []).append((ix, found))

    for name, cands in candidates.items():
        m.indexes[name] = cands[0][0]
        if len(cands) < 2:
            continue
        # same file name, different content, and (with a footage folder) BOTH versions present
        if media_dir is not None:
            where = ", ".join(str(f.relative_to(media_dir)) for _, f in cands)
            m.warnings.append(f"'{name}': {len(cands)} tệp cùng tên nhưng khác nội dung trong thư mục video "
                              f"({where}); kế hoạch chỉ dùng {cands[0][1].relative_to(media_dir)}. Đổi tên "
                              "một tệp nếu muốn dùng cả hai.")
        else:
            m.warnings.append(f"'{name}': {len(cands)} chỉ mục cùng tên nhưng khác nội dung; kế hoạch dùng "
                              f"bản {cands[0][0]['sha256'][:12]}…, không kiểm tra được đó có phải bản đang có "
                              "trong thư mục video (chưa đặt footage_dir).")
    if orphans:
        m.warnings.append(f"bỏ qua {orphans} chỉ mục cũ không còn tệp tương ứng trong thư mục video "
                          f"(dọn khi chắc chắn: python -m videoauto.index --prune --out {index_dir})")
    return m


def _reject(brief, reason: str, warnings: list[str], material: Material, calls: int) -> dict:
    return _base_plan(brief, material, calls) | {"tier": "reject", "reason": reason,
                                                 "warnings": warnings}


def _base_plan(brief, material: Material, calls: int) -> dict:
    return {
        "version": 1,
        "target": {"duration": brief.duration, "aspect": brief.aspect, "fps": brief.fps},
        "confidence": 0.0, "tier": "reject", "clips": [], "transitions": [],
        "fades": {"in": 0, "out": 0}, "overlays": [], "script": "", "warnings": [],
        "sources": {f: {"sha256": ix["sha256"], "duration": ix["duration"],
                        "orientation": ix["orientation"]} for f, ix in material.indexes.items()},
        "model_calls": calls,
    }


# ------------------------------------------------------------------------ prompt

def build_prompt(cfg: ProjectConfig, material: Material, cat: Catalogue) -> str:
    b = cfg.brief
    usable = material.usable()
    ranked = sorted(usable, key=lambda fs: -fs[1]["sharpness"])
    rank = {(f, s["id"]): i for i, (f, s) in enumerate(ranked, 1)}
    lines = []
    for f, ix in material.indexes.items():
        lines.append(f"FILE {f} | {ix['duration']:.2f}s | {ix['orientation']} {ix['width']}x{ix['height']} "
                     f"| audio: {'yes' if ix['audio']['has_audio'] else 'none'}"
                     f"{' | speech' if ix['has_speech'] else ''}")
        for s in ix["shots"]:
            bad = set(s["flags"]) & REJECTING
            head = f"  {s['id']} {s['start']:.2f}-{s['end']:.2f}s"
            if bad:
                lines.append(f"{head} | UNUSABLE ({', '.join(sorted(bad))}) - never use")
                continue
            lines.append(
                f"{head} | sharpness {s['sharpness']:.0f} (rank {rank[(f, s['id'])]}/{len(usable)})"
                f" | motion {s['motion']:.2f} | camera {s['camera'] or '?'} | {s['shot_size'] or '?'}"
                f" | flags: {', '.join(s['flags']) or '-'} | {s['caption'] or '(no caption)'}")
            for seg in s["transcript"] or []:
                lines.append(f"      speech {seg['start']:.1f}-{seg['end']:.1f}: {seg['text']}")

    luts = []
    for it in cat.of_kind("lut"):
        ps = ", ".join(f"{p['key']} {p['min']}..{p['max']} (default {p['default']})"
                       for p in cat.numeric_params(it["assetId"]))
        luts.append(f"  {it['assetId']} | {it.get('description', '')[:70]} | params: {ps}")
    trans = ", ".join(it["assetId"] for it in cat.of_kind("transition"))
    brand, script = cfg.brand, cfg.script_text()

    brand_block = ""
    if brand.has_text:
        brand_block = "BRAND (the video is for this business: let shot choice, pacing and the script reflect it)\n"
        brand_block += f"- name: {brand.name}\n" if brand.name else ""
        brand_block += f"- tagline: {brand.tagline}\n" if brand.tagline else ""
        brand_block += ("- context:\n" + "\n".join("    " + ln for ln in brand.context.splitlines()) + "\n"
                        if brand.context else "")
        brand_block += ("- Only describe what the footage actually shows. If the brand mentions things the\n"
                        "  footage does not contain, do not imply the video shows them.\n")

    style_block = _style_prompt(cfg, cat)

    if script:
        script_block = f"""USER SCRIPT (this is the user's intent: FOLLOW it, do not invent your own narrative)
<<<
{script}
>>>
- Split it into beats in its own order (timecodes, if present, are the intended timing).
- For each beat pick the closest matching usable footage. If the footage has nothing for a beat,
  say so in "beats" (empty "clips" and a Vietnamese "note" naming what is missing) and use the
  closest match only if one is reasonable. Never pretend footage shows something it does not,
  and never skip a beat silently.
"""
        script_rule = ('"script": "<Vietnamese: how the edit follows the user script, beat by beat>",\n'
                       ' "beats": [{"beat": "<beat text from the user script>", "clips": [1, 2], '
                       '"note": "<Vietnamese, empty if fully covered>"}],')
    else:
        script_block = ""
        script_rule = '"script": "<2-4 sentences in Vietnamese: what the edit shows, in order>",'

    if brand.logo is None:
        trans_rule = "5. Transitions are optional, at most one per cut; plain cuts are fine. Use only ids from the list."
    elif brand.logo_mode == "always":
        trans_rule = ("5. NO transitions: a logo overlay is on screen for the whole video, and the renderer draws\n"
                      "   transitions on top of it, hiding it. Use plain cuts; reply \"transitions\": [].")
    else:
        trans_rule = ("5. Transitions are optional, at most one per cut, but NONE in the last "
                      "3 s: a logo overlay is\n   shown there and the renderer would hide it under a transition.")

    overlay_block, overlay_rule = _overlay_prompt(cfg, cat, ov.script_text_lines(script))

    return f"""You are planning a short video edit from raw footage. Only the text index below is
available; you cannot see the footage. Reply with ONE JSON object and nothing else.

BRIEF
- target duration: {b.duration:g} s (total of all clips should be within ±10 %)
- aspect {b.aspect} ({b.orientation}), {b.fps} fps, platform: {b.platform}
{('- notes: ' + b.notes) if b.notes else ''}
{brand_block}{style_block}{script_block}
MATERIAL (times are source seconds; shots marked UNUSABLE must never be used)
{chr(10).join(lines)}

RULES (keep it simple: this is a first, crude cut)
1. Pick shots and order them. Each clip lies inside ONE usable shot: shot.start <= in < out <= shot.end.
   Clips 1.5-6 s long. Never reuse the same source seconds twice.
2. When two options are similar, prefer the SHARPER shot (higher sharpness / better rank).
   Prefer shots whose orientation matches the target ({b.orientation}).
   {_fit_rule(b)}
   A "blurry" flag means clearly soft footage: use it only if nothing else fits.
3. One LUT for the whole piece, chosen from the list below; params only from that LUT, inside its ranges.
4. Fade in at the start and fade out at the end: 0.3-1.0 s each.
{trans_rule}
6. If the material cannot make a watchable video for this brief, do NOT invent one: reply
   {{"reject": "<reason in Vietnamese>"}}.

LUTS
{chr(10).join(luts)}
TRANSITIONS (durations in frames, at {b.fps} fps; keep them under half of the shorter neighbouring clip)
  {trans}
{overlay_block}
REPLY FORMAT
{{"clips": [{{"src": "<file>", "shot": "s01", "in": 0.0, "out": 3.5}}],
 "look": {{"id": "<lut id>", "params": {{"intensity": 0.8}}}},
 "transitions": [{{"after_clip": 1, "id": "<transition id>", "frames": 8}}],
 "fades": {{"in": 0.5, "out": 0.8}},{overlay_rule}
 "confidence": 0.0-1.0,
 {script_rule}
 "warnings": ["<Vietnamese, optional>"]}}
"after_clip": N puts the transition on the cut between clip N and clip N+1 (1-based).
"""


PACE_RULES = {
    "fast": "fast: mostly short clips (1.5-3 s), quick cuts, energetic order",
    "medium": "medium: clips of about 2.5-4.5 s, a steady rhythm",
    "calm": "calm: longer clips (4-6 s), few cuts, let shots breathe",
}


def _fit_rule(b) -> str:
    """What the builder does with footage of another shape, so the model plans for it (and does
    not warn that it still has to be cropped: the builder does that)."""
    if b.fit == "fit":
        return (f"Footage of another shape is shown whole, with black bars, in the {b.aspect} frame "
                "(brief.fit = fit).")
    return (f"Footage of another shape is scaled to FILL the {b.aspect} frame and cropped at the CENTRE "
            "automatically (no black bars): horizontal footage in a vertical video loses its left and right "
            "sides, so prefer shots whose subject is near the centre. Do not warn that cropping is needed.")


def _style_prompt(cfg: ProjectConfig, cat: Catalogue) -> str:
    """STYLE block from config.yaml `style:` (tone, pace, prefer_luts); "" when nothing is set.
    LUT ids not in the catalogue are left out here and reported by make_plan."""
    st = cfg.style
    luts = [i for i in st.prefer_luts if cat.kind(i) == "lut"]
    if not (st.tone or st.pace or luts):
        return ""
    out = "STYLE (how the edit should feel; the footage and the rules below still come first)\n"
    out += f"- tone: {st.tone}\n" if st.tone else ""
    out += f"- pace: {PACE_RULES[st.pace]}\n" if st.pace else ""
    if luts:
        out += (f"- preferred looks: {', '.join(luts)}. Choose the LUT from these unless none suits the "
                "footage; say why in \"warnings\" if you pick another.\n")
    return out


def overlay_templates(cfg: ProjectConfig, cat: Catalogue) -> list[dict]:
    """Templates the validator would accept for this brief and logo: vi_ok, right orientation,
    and (with a logo) a known position outside the logo's corner. Preferred group first,
    the default (keyword pop) at the very top."""
    brand = cfg.brand
    out = []
    for it in cat.of_kind("template"):
        if it.get("vi_ok") is not True or ov.orientation(it) != cfg.brief.orientation:
            continue
        if not ov.text_params(it):
            continue
        if brand.logo is not None:
            c = ov.corners(it["assetId"])
            if c is None or (brand.logo_mode == "always" and brand.logo_position in c):
                continue
        out.append(it)
    return sorted(out, key=lambda it: (it["assetId"] != ov.DEFAULT_TEMPLATE,
                                       it.get("category") != ov.PREFERRED_GROUP, it["assetId"]))


def _overlay_prompt(cfg: ProjectConfig, cat: Catalogue, lines: list[str]) -> tuple[str, str]:
    if not lines:
        return "", ""
    tpls = overlay_templates(cfg, cat)
    if not tpls:
        return "", ""
    rows = []
    for it in tpls[:20]:
        where = ov.zone(it["assetId"]) or "position unknown"
        rows.append(f"  {it['assetId']} | {it['name']} | {where} | text fields: {', '.join(ov.text_params(it))}"
                    f" | {it.get('description', '')[:60]}")
    logo = cfg.brand
    logo_note = (f"- The logo is in the {logo.logo_position} corner; the templates listed already avoid it "
                 "(prefer centre or upper-third ones).\n" if logo.logo is not None else "")
    block = f"""
TEXT OVERLAYS (the user script asks for on-screen text with TEXT: lines)
- One overlay per TEXT line, on that line's beat. A beat without a TEXT line gets NO overlay.
- Copy the TEXT line EXACTLY into the template's first text field: same words, accents and capitals.
  Do not translate, shorten, rephrase or add text, even if it is long; leave every other text field "".
- "at"/"duration" are timeline seconds of YOUR edit (where that beat's clips land), 1.5-3 s each,
  inside the video, and overlays must never overlap in time.
- Use only the templates below. For a short punchy line use the first one (keyword pop, centred).
  Colours are filled from the brand automatically; do not set them.
{logo_note}TEXT lines in the script: {" / ".join(lines)}
TEMPLATES (id | name | position | text fields | description)
{chr(10).join(rows)}
"""
    rule = ('\n "overlays": [{"template": "<template id>", "at": 0.0, "duration": 2.5, '
            '"fields": {"keyword": "<TEXT line, verbatim>", "subText": ""}}],')
    return block, rule


def parse_reply(text: str) -> dict | None:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------------ assemble

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def assemble(reply: dict, cfg: ProjectConfig, material: Material, cat: Catalogue) -> dict:
    """Turn the model's reply into an EditPlan and validate it hard."""
    b = cfg.brief
    plan = _base_plan(b, material, 1)
    plan["warnings"] = list(material.warnings)
    if isinstance(reply.get("reject"), str) and reply["reject"].strip():
        plan["reason"] = reply["reject"].strip()
        return plan

    warn = plan["warnings"]
    look: list[dict] = []
    lk = reply.get("look")
    if isinstance(lk, dict) and isinstance(lk.get("id"), str):
        if cat.kind(lk["id"]) != "lut":
            warn.append(f"bỏ look '{lk['id']}': không phải LUT trong catalogue")
        else:
            try:
                look = [{"id": lk["id"], "params": cat.params_from_dict(lk["id"], lk.get("params") or {})}]
            except (ValueError, TypeError) as e:
                warn.append(f"bỏ look '{lk['id']}': {e}")
    else:
        warn.append("model không chọn LUT")

    clips = []
    for n, c in enumerate(reply.get("clips") or [], 1):
        if not isinstance(c, dict) or not isinstance(c.get("src"), str) \
                or _num(c.get("in")) is None or _num(c.get("out")) is None:
            warn.append(f"bỏ clip {n}: thiếu src/in/out")
            clips.append(None)
            continue
        clip = {"src": c["src"], "in": round(_num(c["in"]), 3), "out": round(_num(c["out"]), 3),
                "track": "V1", "effects": [dict(e, params=list(e["params"])) for e in look]}
        if isinstance(c.get("shot"), str) and re.fullmatch(r"s[0-9]{2,}", c["shot"]):
            clip["shot"] = c["shot"]
        clips.append(clip)
    plan["clips"] = [c for c in clips if c is not None]

    fades = reply.get("fades") if isinstance(reply.get("fades"), dict) else {}
    plan["fades"] = {k: max(0.0, _num(fades.get(k)) if _num(fades.get(k)) is not None else 0.5)
                     for k in ("in", "out")}
    conf = _num(reply.get("confidence"))
    plan["confidence"] = min(1.0, max(0.0, conf if conf is not None else 0.0))
    plan["script"] = reply.get("script") if isinstance(reply.get("script"), str) else ""
    warn.extend(f"model: {w}" for w in reply.get("warnings") or [] if isinstance(w, str))
    plan["text_lines"] = ov.script_text_lines(cfg.script_text())
    plan["overlays"] = _overlays_from_reply(reply.get("overlays"), warn)
    plan["tier"] = "full"                           # provisional; decided below
    if cfg.script_text():
        plan["script_source"] = "user"
        warn.extend(_beat_warnings(reply.get("beats")))     # count as issues: they lower the tier
    else:
        plan["script_source"] = "planner"

    shots = {f: ix["shots"] for f, ix in material.indexes.items()}
    # pass 1: clips only, so transitions can be tied to the clips that survived
    validate_plan(plan, cat, shots, cfg.brand)
    if plan["tier"] == "reject":
        return plan
    kept = plan["clips"]
    pos = {id(c): i for i, c in enumerate(kept)}
    cuts = cut_times(kept, b.fps)
    for t in reply.get("transitions") or []:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str):
            continue
        k, frames = t.get("after_clip"), t.get("frames")
        orig = clips[k - 1] if isinstance(k, int) and 1 <= k < len(clips) else None
        nxt = clips[k] if orig is not None else None
        if orig is None or nxt is None or id(orig) not in pos or pos.get(id(nxt)) != pos[id(orig)] + 1:
            warn.append(f"bỏ chuyển cảnh '{t['id']}' sau clip {k}: clip kề bên không còn hoặc không liền nhau")
            continue
        if not isinstance(frames, int) or isinstance(frames, bool) or frames < 1:
            warn.append(f"bỏ chuyển cảnh '{t['id']}': số khung không hợp lệ ({frames!r})")
            continue
        plan["transitions"].append({"at": cuts[pos[id(orig)]], "id": t["id"], "frames": frames})
    # pass 2: transitions (clips are already valid and stay unchanged)
    validate_plan(plan, cat, shots, cfg.brand)

    total = sum(c["out"] - c["in"] for c in plan["clips"])
    if abs(total - b.duration) > DURATION_TOLERANCE * b.duration:
        warn.append(f"tổng thời lượng {total:.1f}s lệch so với mục tiêu {b.duration:g}s")
    # anything our checks had to drop or fix (the model's own advisory warnings don't count)
    dropped = any(not w.startswith("model: ") for w in warn[len(material.warnings):])
    if plan["confidence"] < MIN_CONFIDENCE_FULL or dropped or total < 0.8 * b.duration:
        plan["tier"] = "basic"
    return plan


def _overlays_from_reply(raw, warn: list[str]) -> list[dict]:
    """Shape check only; the rules themselves live in validate.validate_overlays."""
    out = []
    for n, o in enumerate(raw if isinstance(raw, list) else [], 1):
        at = _num(o.get("at")) if isinstance(o, dict) else None
        dur = _num(o.get("duration")) if isinstance(o, dict) else None
        fields = o.get("fields") if isinstance(o, dict) else None
        if (not isinstance(o, dict) or not isinstance(o.get("template"), str) or at is None
                or dur is None or at < 0 or dur <= 0 or not isinstance(fields, dict)):
            warn.append(f"bỏ overlay {n}: thiếu template/at/duration/fields")
            continue
        out.append({"template": o["template"], "at": round(at, 4), "duration": round(dur, 4),
                    "fields": {str(k): v if isinstance(v, str) else str(v) for k, v in fields.items()
                               if v is not None}})
    return out


def _beat_warnings(beats) -> list[str]:
    """The user script must never be ignored silently: every beat the footage cannot cover
    becomes a warning that names it. A reply without a beat report is itself a warning."""
    if not isinstance(beats, list) or not beats:
        return ["kịch bản: model không báo cáo đã theo kịch bản của bạn thế nào (thiếu 'beats'); "
                "kiểm tra kỹ kế hoạch so với kịch bản"]
    out = []
    for b in beats:
        if not isinstance(b, dict):
            continue
        text = str(b.get("beat") or "").strip()[:120] or "?"
        note = str(b.get("note") or "").strip()
        if not b.get("clips"):
            out.append(f"kịch bản: không có cảnh phù hợp cho \"{text}\"" + (f" ({note})" if note else ""))
        elif note:
            out.append(f"kịch bản: \"{text}\" chỉ khớp gần đúng: {note}")
    return out


# ------------------------------------------------------------------------ entry

def make_plan(cfg: ProjectConfig, index_dir: Path, provider: VisionProvider) -> dict:
    cat = Catalogue.load(cfg.catalogue)
    material = load_material(index_dir, cfg.footage_dir)
    b = cfg.brief
    if not material.indexes:
        return _finish(_reject(b, "Không có chỉ mục video hợp lệ nào. Chạy M1 trên thư mục video trước "
                               "(và kiểm tra footage_dir trong config.yaml).", material.warnings, material, 0))
    have, need = material.usable_seconds, cfg.min_material_ratio * b.duration
    if have < need:
        return _finish(_reject(
            b, f"Không đủ hình dùng được: chỉ có {have:.1f}s (sau khi loại shot đen/đứng hình), "
               f"cần ít nhất {need:.1f}s cho video {b.duration:g}s. Thêm cảnh quay hoặc giảm brief.duration.",
            material.warnings, material, 0))

    unknown = [i for i in cfg.style.prefer_luts if cat.kind(i) != "lut"]
    if unknown:
        material.warnings.append(f"style.prefer_luts: bỏ qua id không phải LUT trong catalogue.json: "
                                 f"{', '.join(unknown)}")
    provider.check()
    prompt = build_prompt(cfg, material, cat)
    log.info("model: 1 lượt gọi (%s), %d tệp, %d shot dùng được, prompt %d ký tự",
             provider.name, len(material.indexes), len(material.usable()), len(prompt))
    try:
        text = provider.complete(prompt)
    except ProviderTransient as e:
        raise UserError(f"Gọi model thất bại ({e}). Chạy lại lệnh sau ít phút.") from None
    reply = parse_reply(text)
    if reply is None:
        return _finish(_reject(b, "Model trả lời sai định dạng (không phải JSON), nên không có kế hoạch. "
                                  "Chạy lại lệnh.", material.warnings + [f"trả lời thô: {text[:200]}"],
                               material, 1))
    return _finish(assemble(reply, cfg, material, cat))


def _finish(plan: dict) -> dict:
    errs = schema_errors(plan)
    if errs:
        raise PlanValidationError("EditPlan failed validation:\n  " + "\n  ".join(errs))
    return plan
