"""Batches keyframes from all queued shots into 3x3 contact sheets; one request per sheet.

Requests run strictly sequentially. A malformed reply is retried (default twice) for the shots
it failed to cover; shots still uncovered get caption=None. LimitReached / ProviderUnavailable
propagate to the caller untouched, with every result obtained so far kept in `results`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import contact_sheet
from .base import ProviderTransient, VisionProvider

log = logging.getLogger("videoauto")

SHOT_SIZES = {"extreme_close_up", "close_up", "medium", "wide", "extreme_wide"}
CAMERA_VI = {
    "static": "máy quay đứng yên", "pan": "máy lia ngang", "tilt": "máy lia dọc",
    "zoom": "máy zoom", "handheld": "máy cầm tay, hơi rung", "tracking": "máy bám theo chủ thể",
}


@dataclass
class ShotJob:
    key: tuple[str, str]          # (sha256, shot id)
    frames: list[Path]
    camera: str
    orientation: str


@dataclass
class Caption:
    caption: str
    subjects: list[str]
    shot_size: str


@dataclass
class Stats:
    calls: int = 0
    shots_per_call: list[int] = field(default_factory=list)


def build_prompt(image_ref: str, layout: list[tuple[list[int], ShotJob]]) -> str:
    lines = []
    for cells, job in layout:
        where = f"cell {cells[0]}" if len(cells) == 1 else \
            f"cells {cells[0]}-{cells[-1]} (frames of the SAME shot, answer once with cell {cells[0]})"
        lines.append(f"- {where}: {job.orientation} video, camera movement measured as "
                     f"\"{job.camera}\" ({CAMERA_VI.get(job.camera, job.camera)})")
    return f"""{image_ref} is a 3x3 contact sheet of video keyframes. Each cell shows its number
(1-9) in yellow at the top-left; numbering runs left to right, top to bottom. Dark empty cells
are padding: ignore them.

Shots on this sheet:
{chr(10).join(lines)}

Return ONE JSON object per shot, keyed by its first cell number:
{{"cell": <int>, "caption": "<Vietnamese>", "subjects": ["<short English noun>", ...],
  "shot_size": "extreme_close_up" | "close_up" | "medium" | "wide" | "extreme_wide"}}

caption: one natural Vietnamese sentence (at most ~25 words) that says what is happening,
names the shot size (đặc tả, cận cảnh, trung cảnh, toàn cảnh, đại cảnh) and the camera movement
given above. Do not invent camera movement that is not given.
subjects: 1-5 main visible things, lowercase English nouns.

Reply with ONLY the JSON array of {len(layout)} objects. No prose, no code fences."""


def parse_reply(text: str, expected_cells: set[int]) -> dict[int, Caption]:
    """Return the valid entries; anything malformed is simply absent."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    out: dict[int, Caption] = {}
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        cell, cap = item.get("cell"), item.get("caption")
        subjects, size = item.get("subjects"), item.get("shot_size")
        if isinstance(cell, str) and cell.isdigit():
            cell = int(cell)
        if (cell in expected_cells and isinstance(cap, str) and cap.strip()
                and isinstance(subjects, list) and all(isinstance(s, str) for s in subjects)
                and size in SHOT_SIZES):
            out[cell] = Caption(cap.strip(), [s.strip().lower() for s in subjects if s.strip()], size)
    return out


class Captioner:
    def __init__(self, provider: VisionProvider, workdir: Path, max_retries: int = 2,
                 cells_per_sheet: int = contact_sheet.CELLS):
        self.provider = provider
        self.workdir = workdir
        self.max_retries = max_retries
        self.cells_per_sheet = cells_per_sheet
        self.queue: list[ShotJob] = []
        self.results: dict[tuple[str, str], Caption | None] = {}
        self.stats = Stats()
        self._sheet_no = 0
        self._ready = False

    def ensure_ready(self) -> None:
        if not self._ready:
            self.provider.check()
            self._ready = True

    def add(self, job: ShotJob) -> None:
        self.queue.append(job)

    def queued_cells(self) -> int:
        return sum(len(j.frames) for j in self.queue)

    def flush(self, force: bool, on_sheet_done: Callable[[], None]) -> None:
        """Send full sheets; with force=True also the final partial sheet."""
        while self.queue and (force or self.queued_cells() >= self.cells_per_sheet):
            batch, cells = [], 0
            while self.queue and (not batch or cells + len(self.queue[0].frames) <= self.cells_per_sheet):
                job = self.queue.pop(0)
                batch.append(job)
                cells += len(job.frames)
            self._caption_batch(batch)
            on_sheet_done()

    def _caption_batch(self, batch: list[ShotJob]) -> None:
        pending = list(batch)
        for attempt in range(self.max_retries + 1):
            layout, images = [], []
            for job in pending:
                cells = list(range(len(images) + 1, len(images) + len(job.frames) + 1))
                layout.append((cells, job))
                images += job.frames
            self._sheet_no += 1
            sheet = contact_sheet.build(images, self.workdir / f"sheet_{self._sheet_no:03d}.jpg")
            prompt = build_prompt(self.provider.image_reference(sheet), layout)

            self.stats.calls += 1
            self.stats.shots_per_call.append(len(pending))
            log.info("model: lượt gọi #%d (%s) — %d shot, %d khung hình%s", self.stats.calls,
                     self.provider.name, len(pending), len(images),
                     f", thử lại lần {attempt}" if attempt else "")
            try:
                reply = self.provider.describe(sheet, prompt)
                got = parse_reply(reply, {cells[0] for cells, _ in layout})
            except ProviderTransient as e:
                log.warning("model: lỗi tạm thời (%s)", e)
                got = {}
            still = []
            for cells, job in layout:
                if cells[0] in got:
                    self.results[job.key] = got[cells[0]]
                else:
                    still.append(job)
            if still:
                log.warning("model: trả lời thiếu/sai định dạng cho %d shot", len(still))
            pending = still
            if not pending:
                return
        for job in pending:
            self.results[job.key] = None
