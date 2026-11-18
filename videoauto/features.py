"""What the user asked for must be delivered, or the run stops and says why.

A feature the user asked for (TEXT: lines in the script, a logo, a script file, preferred LUTs) is
never dropped silently: a video without the text it was asked to show is worse than no video,
because nobody notices until they watch it. Each check here turns such a silent skip into a
UserError with what is missing and how to fix it.
"""

from __future__ import annotations

from .errors import UserError
from .planner import overlays as ov

ORIENT_VI = {"vertical": "dọc", "horizontal": "ngang", "square": "vuông"}


def text_overlay_problem(cfg, cat, script: str) -> str | None:
    """None when the script has no TEXT lines, or when at least one template can carry them for
    this brief (vi_ok, right orientation, clear of the logo). Else the Vietnamese explanation."""
    from .planner.planner import overlay_templates
    lines = ov.script_text_lines(script)
    if not lines or overlay_templates(cfg, cat):
        return None
    return (f"Kịch bản có {len(lines)} dòng TEXT: (chữ trên video) nhưng không có template chữ nào dùng "
            f"được cho khung {cfg.brief.aspect}, nên video sẽ không có chữ. Dừng để không giao video thiếu "
            f"chữ. {no_template_cause(cfg, cat)}")


def no_template_cause(cfg, cat) -> str:
    """Why overlay_templates() is empty for this brief, and how to fix it (Vietnamese)."""
    tpls = cat.of_kind("template")
    vi_ok = [t for t in tpls if t.get("vi_ok") is True]
    want = cfg.brief.orientation
    fit = [t for t in vi_ok if ov.orientation(t) == want and ov.text_params(t)]
    if not vi_ok:
        reviewed = sum(isinstance(t.get("vi_ok"), bool) for t in tpls)
        what = ("catalogue.json không có kết quả kiểm font tiếng Việt (không template nào có trường vi_ok)"
                if reviewed == 0 else f"0/{len(tpls)} template đạt kiểm tra font tiếng Việt (vi_ok)")
        return (f"Nguyên nhân: {what}. Cách sửa: chạy `python -m videoauto.occ_catalogue --refresh` "
                f"để tạo lại '{cfg.catalogue.name}' kèm kết quả kiểm font đóng gói sẵn trong videoauto "
                "(thư mục openchatcut/).")
    if not fit:
        return (f"Nguyên nhân: {len(vi_ok)} template đạt kiểm tra font tiếng Việt nhưng không cái nào "
                f"có khung {ORIENT_VI.get(want, want)} và trường chữ. Cách sửa: đổi brief.aspect trong config.yaml, "
                "hoặc bỏ các dòng TEXT: khỏi kịch bản.")
    b = cfg.brand
    return (f"Nguyên nhân: {len(fit)} template hợp khung {ORIENT_VI.get(want, want)} nhưng đều đặt chữ ở "
            f"góc {b.logo_position} của logo (hoặc không rõ vị trí). Cách sửa: đổi brand.logo_position, đặt "
            "brand.logo_mode: end, hoặc bỏ các dòng TEXT: khỏi kịch bản.")


def check_text_overlays(cfg, cat, script: str) -> None:
    msg = text_overlay_problem(cfg, cat, script)
    if msg:
        raise UserError(msg)


def check_style(cfg, cat) -> None:
    """style.prefer_luts names LUTs the user asked for: an id that is not a LUT is a typo, not
    something to drop with a warning nobody reads."""
    unknown = [i for i in cfg.style.prefer_luts if cat.kind(i) != "lut"]
    if unknown:
        luts = ", ".join(sorted(it["assetId"] for it in cat.of_kind("lut"))[:8])
        raise UserError(f"style.prefer_luts trong config.yaml có id không phải LUT trong catalogue: "
                        f"{', '.join(unknown)}. Sửa lại (ví dụ: {luts}, ...) hoặc để prefer_luts: [].")


def check_script_file(cfg) -> None:
    """script_file set but missing: following a script the user wrote was asked for; writing one
    instead would quietly ignore it."""
    if cfg.script_file is not None and not cfg.script_file.is_file():
        raise UserError(f"Không tìm thấy kịch bản '{cfg.script_file}' (script_file trong config.yaml). "
                        "Sửa đường dẫn, hoặc xoá dòng script_file nếu muốn model tự viết kịch bản.")


def missing_text_lines(plan: dict) -> list[str]:
    """TEXT lines of the script that no overlay of the (validated) plan shows."""
    lines = plan.get("text_lines") or []
    shown = {ov.norm(v) for o in plan.get("overlays") or [] for v in o.get("fields", {}).values() if v}
    return [t for t in lines if t not in shown and ov.shorten(t) not in shown]


def check_plan_text(plan: dict) -> None:
    missing = missing_text_lines(plan)
    if not missing:
        return
    why = [w for w in plan.get("warnings") or [] if "overlay" in w][:6]
    raise UserError(
        f"Kế hoạch dựng không đặt được chữ cho {len(missing)}/{len(plan.get('text_lines') or [])} dòng TEXT: "
        + "; ".join(f"\"{t}\"" for t in missing)
        + ". Dừng trước khi dựng để không giao video thiếu chữ."
        + (" Lý do: " + " | ".join(why) if why else " Model không đặt overlay cho các dòng này.")
        + " Cách sửa: chép kịch bản vào lại để lập kế hoạch lần nữa; nếu vẫn lỗi, bớt hoặc rút ngắn dòng TEXT "
          "(mỗi dòng cần 1,5–3 giây riêng, không chồng nhau).")
