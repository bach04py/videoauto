"""M3 builder: EditPlan -> occ project new -> import_folder -> clips -> LUT -> transitions
-> fades -> occ render.

A failed item command is skipped and recorded; the run aborts only when more than
max_fail_ratio (30 %) of the item commands fail. project/import/render failing is fatal,
because every later step depends on them.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..brief import ASPECTS, ProjectConfig
from ..cache import sha256_of
from ..errors import BuildAborted, UserError
from ..planner.catalogue import Catalogue
from ..planner.validate import clip_frames, logo_window, schema_errors, validate_plan
from .occ import Occ, OccResult

log = logging.getLogger("videoauto")


@dataclass
class BuildReport:
    output: Path
    project_id: str | None = None
    attempted: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    commands: list[dict] = field(default_factory=list)
    error: UserError | None = None

    def to_json(self) -> dict:
        return {"ok": self.error is None, "error": self.error.message if self.error else None,
                "output": str(self.output), "project_id": self.project_id,
                "item_commands": self.attempted, "item_failures": self.failed,
                "seconds": round(self.seconds, 1), "warnings": self.warnings, "commands": self.commands}


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise UserError(f"Không đọc được '{path}'. Chạy M2 trước: python -m videoauto.plan .index --out {path}") from None
    except ValueError as e:
        raise UserError(f"'{path.name}' không phải JSON hợp lệ: {e}") from None
    errs = schema_errors(plan)
    if errs:
        raise UserError(f"'{path.name}' không đúng định dạng EditPlan:\n  " + "\n  ".join(errs[:8]))
    if plan["tier"] == "reject":
        raise UserError(f"Kế hoạch bị từ chối nên không dựng: {plan['reason']}")
    return plan


class Builder:
    def __init__(self, cfg: ProjectConfig, occ: Occ | None = None):
        self.cfg = cfg
        self.occ = occ or Occ(cfg.occ)

    def build(self, plan: dict, out: Path) -> BuildReport:
        """Never raises a UserError: it lands in rep.error, so the caller can still write
        the per-command report before telling the user."""
        t0 = time.monotonic()
        rep = BuildReport(output=out.resolve())
        try:
            self._build(plan, rep)
        except UserError as e:
            rep.error = e
        finally:
            rep.seconds = time.monotonic() - t0
            rep.commands = [r.record() for r in self.occ.history]
        return rep

    # ------------------------------------------------------------------
    def _build(self, plan: dict, rep: BuildReport) -> None:
        cfg, fps = self.cfg, plan["target"]["fps"]
        if cfg.footage_dir is None or not cfg.footage_dir.is_dir():
            raise UserError("Cần footage_dir (thư mục video gốc) trong config.yaml để nhập vào OpenChatCut.")
        cat = Catalogue.load(cfg.catalogue)
        rep.warnings += validate_plan(plan, cat, brand=cfg.brand)   # plan.json may be hand-edited
        self._check_media(plan, rep, cat)
        if not plan["clips"]:
            raise BuildAborted("Không còn clip hợp lệ nào trong kế hoạch, không dựng.")
        self.occ.check()

        # ---- fatal steps: everything later depends on them
        w, h = ASPECTS[plan["target"]["aspect"]]
        r = self.occ.run("project new", ["project", "new", f"videoauto {datetime.now():%Y-%m-%d %H:%M:%S}",
                                         "--size", f"{w}x{h}", "--fps", str(fps)])
        self._fatal(r, "tạo project")
        pid = rep.project_id = r.data["id"]
        r = self.occ.run("import_folder", ["tools", "call", "import_folder", "--project", pid, "--apply"],
                         {"path": str(cfg.footage_dir)})
        self._fatal(r, "nhập thư mục video")
        assets = {}
        for a in r.tool_result().get("imported") or []:
            assets.setdefault(a["name"], a["id"])

        # ---- item steps: count, skip failures, abort above the ratio
        clips = plan["clips"]
        total = (len(clips) + sum(len(c["effects"]) for c in clips) + len(plan["transitions"])
                 + (plan["fades"]["in"] > 0) + (plan["fades"]["out"] > 0)
                 + (LOGO_COMMANDS if cfg.brand.logo is not None else 0))
        placed: list[str | None] = []
        cursor = 0
        for n, c in enumerate(clips, 1):
            frames = clip_frames(c, fps)
            asset = assets.get(c["src"])
            if asset is None:
                rep.attempted += 1
                self._fail(rep, total, f"clip {n} ({c['src']}): không có trong kết quả import_folder")
                placed.append(None)
                continue
            r = self._item(rep, total, f"clip {n} {c['src']}", ["tools", "call", "edit_item", "--project", pid,
                           "--apply"], {"adds": [{"type": "video", "assetId": asset, "track": "V1",
                                                  "fromFrame": cursor,
                                                  "sourceStartFrame": round(c["in"] * fps),
                                                  "sourceDurationInFrames": frames}]})
            item = None
            if r.ok:
                try:
                    item = r.tool_result()["results"][0]["placed"]["itemId"]
                except (KeyError, IndexError, TypeError):
                    self._fail(rep, total, f"clip {n}: occ không trả về itemId")
            placed.append(item)
            if item:
                cursor += frames                     # failed clips leave no gap: later ones pack left

        for n, (c, item) in enumerate(zip(clips, placed), 1):
            for e in c["effects"]:
                if item is None:
                    rep.warnings.append(f"bỏ hiệu ứng {e['id']} của clip {n}: clip không được đặt")
                    continue
                self._item(rep, total, f"effect clip {n}", ["tools", "call", "manage_effects", "--project", pid,
                           "--apply"], {"action": "add", "targetItemId": item, "assetId": e["id"],
                                        "propertyOverrides": cat.overrides(e["id"], e["params"])})

        cut_index = _cut_indexes(clips, fps)
        for t in plan["transitions"]:
            k = cut_index.get(round(t["at"] * fps))
            a, b = (placed[k], placed[k + 1]) if k is not None else (None, None)
            if a is None or b is None:
                rep.warnings.append(f"bỏ chuyển cảnh {t['id']} tại {t['at']:.2f}s: clip kề bên không được đặt")
                continue
            self._item(rep, total, f"transition {t['id'].split(':')[-1]}", ["tools", "call", "edit_item",
                       "--project", pid, "--apply"],
                       {"adds": [{"type": "transition", "assetId": t["id"], "outgoingItemId": a,
                                  "incomingItemId": b, "durationInFrames": t["frames"]}]})

        real = [i for i in placed if i]
        for edge, key, item in (("in", "fadeInSeconds", real[0] if real else None),
                                ("out", "fadeOutSeconds", real[-1] if real else None)):
            secs = plan["fades"][edge]
            if secs <= 0:
                continue
            if item is None:
                rep.warnings.append(f"bỏ fade {edge}: không có clip nào được đặt")
                continue
            self._item(rep, total, f"fade {edge}", ["tools", "call", "edit_item", "--project", pid, "--apply"],
                       {"updates": [{"type": "video", "itemId": item, key: secs}]})

        if not real:
            raise BuildAborted("Không đặt được clip nào lên timeline, không xuất video.")

        if cfg.brand.logo is not None:
            self._place_logo(rep, total, pid, (w, h), fps, cursor)

        # ---- render (fatal)
        rep.output.parent.mkdir(parents=True, exist_ok=True)
        r = self.occ.run("render", ["render", pid, "--out", str(rep.output),
                                    "--resolution", cfg.occ.resolution, "--fps", str(fps),
                                    "--bitrate", str(round(cfg.occ.video_bitrate_mbps * 1_000_000))],
                         timeout=cfg.occ.timeout)
        self._fatal(r, "xuất video (render)")
        if not rep.output.exists() or rep.output.stat().st_size == 0:
            raise BuildAborted(f"occ render báo xong nhưng không có tệp '{rep.output}'.")

    # ------------------------------------------------------------------
    def _place_logo(self, rep: BuildReport, total: int, pid: str, canvas: tuple[int, int], fps: int,
                    timeline_frames: int) -> None:
        """import_asset (image) -> edit_track create (new video track above V1) -> edit_item add
        image on it -> edit_item update transform. Arguments as probed against occ; see README."""
        brand = self.cfg.brand
        call = ["tools", "call", None, "--project", pid, "--apply"]
        r = self._item(rep, total, "logo import", _with(call, "import_asset"), {"path": str(brand.logo)})
        asset = ((r.tool_result().get("imported") or [{}])[0]).get("id") if r.ok else None
        if asset is None:
            rep.warnings.append("bỏ logo: không nhập được ảnh logo vào OpenChatCut")
            return
        r = self._item(rep, total, "logo track", _with(call, "edit_track"),
                       {"action": "create", "json": json.dumps({"trackType": "video"})})
        track = ((r.tool_result().get("created") or [{}])[0]).get("id") if r.ok else None
        if track is None:
            rep.warnings.append("bỏ logo: không tạo được track phía trên V1")
            return
        start, end = logo_window(brand, timeline_frames, fps)
        r = self._item(rep, total, "logo place", _with(call, "edit_item"),
                       {"adds": [{"type": "image", "assetId": asset, "track": track,
                                  "fromFrame": start, "durationInFrames": end - start}]})
        try:
            item = r.tool_result()["results"][0]["placed"]["itemId"] if r.ok else None
        except (KeyError, IndexError, TypeError):
            item = None
        if item is None:
            rep.warnings.append("bỏ logo: không đặt được ảnh logo lên timeline")
            return
        self._item(rep, total, "logo transform", _with(call, "edit_item"),
                   {"updates": [{"type": "image", "itemId": item, "transform": logo_transform(brand, canvas)}]})

    def _check_media(self, plan: dict, rep: BuildReport, cat: Catalogue) -> None:
        ok: dict[str, bool] = {}
        for name in {c["src"] for c in plan["clips"]}:
            want = plan["sources"][name]["sha256"]
            hits = [p for p in self.cfg.footage_dir.rglob(name) if p.is_file()]
            ok[name] = any(sha256_of(p) == want for p in hits)
            if not ok[name]:
                rep.warnings.append(f"bỏ các clip từ '{name}': không có tệp khớp nội dung trong footage_dir")
        if not all(ok.values()):
            plan["clips"] = [c for c in plan["clips"] if ok[c["src"]]]
            rep.warnings += validate_plan(plan, cat, brand=self.cfg.brand)  # re-ties transitions

    def _item(self, rep: BuildReport, total: int, label: str, args: list[str], tool_args: dict) -> OccResult:
        rep.attempted += 1
        r = self.occ.run(label, args, tool_args)
        if not r.ok:
            self._fail(rep, total, f"{label}: {r.error}")
        return r

    def _fail(self, rep: BuildReport, total: int, msg: str) -> None:
        """One item failed: record it, carry on, abort only above the configured ratio."""
        rep.warnings.append(f"lỗi, bỏ qua {msg}")
        rep.failed += 1
        if rep.failed > self.cfg.occ.max_fail_ratio * total:
            raise BuildAborted(
                f"Dừng dựng: {rep.failed}/{total} lệnh occ thất bại (vượt {self.cfg.occ.max_fail_ratio:.0%}). "
                "Xem chi tiết từng lệnh trong tệp báo cáo .build.json cạnh tệp xuất.")

    def _fatal(self, r: OccResult, what: str) -> None:
        if not r.ok:
            raise BuildAborted(f"Không {what} được trong OpenChatCut: {r.error}. "
                               "Xem tệp báo cáo .build.json cạnh tệp xuất.")


LOGO_COMMANDS = 4      # import_asset, edit_track, edit_item add, edit_item transform


def _with(call: list, tool: str) -> list[str]:
    return [tool if a is None else a for a in call]


def logo_transform(brand, canvas: tuple[int, int]) -> dict:
    """occ image transform for the logo, from measured occ semantics:
    - an image without transform is fitted 'contain' into the canvas, centred;
    - transform.scale multiplies that fitted size;
    - transform.x / .y move its CENTRE by % of canvas width / height from the canvas centre.
    logo_scale is the logo's width as a fraction of the frame width; aspect is kept."""
    W, H = canvas
    lw, lh = brand.logo_size
    fitted_w = lw * min(W / lw, H / lh)
    w = brand.logo_scale * W
    h = w * lh / lw
    m = brand.logo_margin * W
    cx = m + w / 2 if brand.logo_position.endswith("left") else W - m - w / 2
    cy = m + h / 2 if brand.logo_position.startswith("top") else H - m - h / 2
    return {"scale": round(w / fitted_w, 5),
            "x": round((cx - W / 2) / W * 100, 3), "y": round((cy - H / 2) / H * 100, 3),
            "opacity": brand.logo_opacity}


def _cut_indexes(clips: list[dict], fps: int) -> dict[int, int]:
    """Frame of each planned cut -> index of the clip before it."""
    out, f = {}, 0
    for k, c in enumerate(clips[:-1]):
        f += clip_frames(c, fps)
        out[f] = k
    return out
