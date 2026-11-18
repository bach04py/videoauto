"""Orchestration: local analysis per file, captions batched across files, cache writes."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import audio, frames, keyframes, normalize, quality, schema
from .cache import Cache, load_hash_memo, save_hash_memo, sha256_of
from .crop import detect_crop
from .errors import LimitReached, ProviderUnavailable, UserError
from .probe import probe
from .proxy import make_proxy, to_source
from .schema import SCHEMA_VERSION
from .speech import SR as SPEECH_SR, Transcriber
from .vision import make_provider
from .vision.base import VisionProvider
from .vision.captioner import Captioner, ShotJob

log = logging.getLogger("videoauto")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
REJECTING = schema.REJECTING   # blurry is NOT here: blur ranks shots, it never discards them


@dataclass
class Source:
    path: Path
    kind: str  # "video" | "music"


@dataclass
class RunReport:
    written: list[Path] = field(default_factory=list)
    cache_hits: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)
    model_calls: int = 0
    shots_per_call: list[int] = field(default_factory=list)
    stopped: LimitReached | None = None
    normalized: list[str] = field(default_factory=list)   # clips with a rotation tag, used upright


def collect(target: Path, out_dir: Path) -> list[Source]:
    if not target.exists():
        raise UserError(f"Không tìm thấy '{target}'. Kiểm tra lại đường dẫn.")
    out_dir = out_dir.resolve()

    def kind_of(p: Path, rel_parts: tuple[str, ...]) -> str | None:
        in_music = any(part.lower() == "music" for part in rel_parts[:-1])
        ext = p.suffix.lower()
        if in_music and ext in AUDIO_EXT | VIDEO_EXT:
            return "music"
        if ext in VIDEO_EXT:
            return "video"
        return None

    if target.is_file():
        k = kind_of(target, (target.parent.name, target.name))
        if k is None:
            raise UserError(f"'{target.name}' không phải tệp video hỗ trợ ({', '.join(sorted(VIDEO_EXT))}).")
        return [Source(target, k)]

    sources = []
    for p in sorted(target.rglob("*")):
        if not p.is_file() or out_dir in p.resolve().parents:
            continue
        k = kind_of(p, (target.name, *p.relative_to(target).parts))
        if k:
            sources.append(Source(p, k))
    if not sources:
        raise UserError(f"Không có tệp video/nhạc nào trong '{target}'.")
    return sources


class Indexer:
    def __init__(self, cfg: dict[str, Any], out_dir: Path, provider: VisionProvider | None = None,
                 cells_per_sheet: int | None = None):
        self.cfg = cfg
        self.cache = Cache(out_dir)
        load_hash_memo(self.cache.dir)
        self.provider = provider or make_provider(cfg["vision"])
        self.transcriber = Transcriber(cfg["whisper"])
        self._tmp = tempfile.TemporaryDirectory(prefix="videoauto_")
        kw = {"cells_per_sheet": cells_per_sheet} if cells_per_sheet else {}
        self.captioner = Captioner(self.provider, Path(self._tmp.name),
                                   max_retries=cfg["vision"]["max_retries"],
                                   concurrency=cfg["vision"].get("concurrency", 1), **kw)
        self.pending: dict[str, dict[str, Any]] = {}   # sha -> index awaiting captions
        self.report = RunReport()

    # ---------------------------------------------------------------- run
    def run(self, sources: list[Source]) -> RunReport:
        try:
            for src in sources:
                try:
                    self._index_one(src)
                except UserError as e:
                    if isinstance(e, (LimitReached, ProviderUnavailable)):
                        raise   # affects every file: stop the run
                    log.error("lỗi: %s", e.message)
                    self.report.failed.append((src.path, e.message))
            self.captioner.flush(force=True, on_sheet_done=self._finalize_ready)
        except LimitReached as e:
            self.report.stopped = e
            if self.pending:
                log.warning("dừng: %d tệp đang dở không được ghi (%s)", len(self.pending),
                            ", ".join(ix["file"] for ix in self.pending.values()))
        finally:
            self.captioner.close()
            self.report.model_calls = self.captioner.stats.calls
            self.report.shots_per_call = list(self.captioner.stats.shots_per_call)
            self._tmp.cleanup()
            save_hash_memo(self.cache.dir)
        return self.report

    def _index_one(self, src: Source) -> None:
        sha = sha256_of(src.path)
        cached = self.cache.get(sha)
        existing = cached or self.pending.get(sha)
        if existing is not None and existing["kind"] != src.kind:
            other = "video" if existing["kind"] == "video" else "nhạc"
            raise UserError(
                f"'{src.path.name}' có nội dung giống hệt '{existing['file']}' đã được lập chỉ mục dạng "
                f"{other} trong cache này. Một tệp không thể vừa là video vừa là nhạc trong cùng thư mục "
                "--out: dùng thư mục --out khác, hoặc bỏ bản sao ra khỏi music/."
            )
        if cached is not None:
            self.report.cache_hits += 1
            log.info("cache: TRÚNG %s (%s…) — bỏ qua, 0 lượt gọi model", src.path.name, sha[:12])
            if src.kind == "video":
                self._keep_normalized(src.path, cached)
            return
        if sha in self.pending:
            log.info("bỏ qua %s: trùng nội dung với tệp đang xử lý", src.path.name)
            return
        log.info("lập chỉ mục %s (%s)", src.path.name, src.kind)
        if src.kind == "music":
            from .music import index_music
            self._write(index_music(src.path, sha))
            return
        self.captioner.ensure_ready()
        index, jobs = self._analyse_video(src.path, sha)
        if not jobs:
            self._write(self._finish_video(index))
            return
        self.pending[sha] = index
        for job in jobs:
            self.captioner.add(job)
        self.captioner.flush(force=False, on_sheet_done=self._finalize_ready)

    # ---------------------------------------------------------------- video
    def _keep_normalized(self, path: Path, index: dict[str, Any]) -> None:
        """Cache hit: the upright copy exists for a tagged clip (an index written before
        normalisation existed gets it now; its analysis was already upright, ffmpeg decoded it)."""
        sha = index["sha256"]
        if index.get("normalized"):
            if not normalize.path_for(self.cache.dir, sha).is_file():
                normalize.ensure(path, sha, self.cache.dir)
            self.report.normalized.append(index["file"])
            return
        pr = probe(path)
        if normalize.rotation(pr):
            normalize.ensure(path, sha, self.cache.dir, pr)
            index["normalized"] = {"rotation": pr.rotation, "file": f"{normalize.DIR}/{sha}.mp4"}
            self.cache.put(index)
            self.report.normalized.append(index["file"])

    def _analyse_video(self, path: Path, sha: str) -> tuple[dict[str, Any], list[ShotJob]]:
        c = self.cfg
        pr = probe(path)
        if not pr.has_video:
            raise UserError(f"'{path.name}' không có luồng hình ảnh. Đặt tệp nhạc vào thư mục music/.")
        name, norm = path.name, None
        if normalize.rotation(pr):
            # rotation tag: index (and later build from) an upright copy that needs no tag reading
            norm = {"rotation": pr.rotation, "file": f"{normalize.DIR}/{sha}.mp4"}
            path = normalize.ensure(path, sha, self.cache.dir, pr)
            pr = probe(path)
            self.report.normalized.append(name)
        # one decode of a large source into a 640 px proxy; cuts, motion, black/freeze/silence and
        # the bars are measured on it (videoauto/proxy.py). Keyframes still come from the source.
        made = make_proxy(path, pr, Path(self._tmp.name), c.get("indexing", {}).get("hwaccel", "auto"))
        apath, apr = made if made else (path, pr)
        acrop = detect_crop(apath, apr.width, apr.height)
        crop = to_source(acrop, apr, pr) if made else acrop
        if crop:
            log.info("  phát hiện viền đen: phân tích vùng hình %dx%d", crop.w, crop.h)
        fa = frames.analyse(apath, apr, acrop, c["shots"]["threshold"], c["shots"]["min_scene_len"])
        det = quality.detect(apath, pr.duration, pr.has_audio, c["quality"]["silence_db"], acrop)

        pcm = audio.load_audio(apath, SPEECH_SR) if pr.has_audio else None
        if made:
            apath.unlink(missing_ok=True)
        # None: speech recognition off or failed (never fatal) -> transcript null on every shot
        segments = self.transcriber.transcribe(pcm) if pcm is not None else []
        peaks = audio.peak_times(pcm, SPEECH_SR) if pcm is not None else []

        # orientation of the actual picture (inside any bars), for the caption prompt
        cw, ch = (crop.w, crop.h) if crop else (pr.width, pr.height)
        picture = "square" if cw == ch else ("vertical" if ch > cw else "horizontal")
        q = c["quality"]
        shots, jobs = [], []
        fdir = self.cache.frames_dir(sha)
        for span in frames.shot_spans(fa, pr.duration):
            sid, f0, f1, start, end = span.id, span.f0, span.f1, span.start, span.end
            samples = [s for s in fa.samples if f0 <= s.frame_a and s.frame_b < f1]
            times, kpaths = keyframes.for_shot(path, span, c["keyframes"], fdir, crop)
            sharp = max(quality.sharpness(p) for p in kpaths)

            flags = []
            if quality.coverage(det.black, start, end) >= q["black_cover"]:
                flags.append("black")
            if quality.coverage(det.frozen, start, end) >= q["frozen_cover"]:
                flags.append("frozen")
            if quality.coverage(det.silent, start, end) >= q["silent_cover"]:
                flags.append("silent")

            seg = [{"start": s.start, "end": s.end, "text": s.text}
                   for s in segments or [] if s.start < end and s.end > start]
            camera = frames.camera_movement(samples, end - start)
            shot = {
                "id": sid, "start": start, "end": end, "keyframes": times,
                "caption": None, "subjects": [], "shot_size": None, "camera": camera,
                "motion": frames.motion_level(samples), "sharpness": round(sharp, 1),
                "transcript": seg or None, "flags": flags,
            }
            if "black" in flags and all(quality.is_blank(p) for p in kpaths):
                shot["caption"] = "Khung hình đen, không có nội dung"   # nothing to describe: no model cell
            else:
                jobs.append(ShotJob((sha, sid), kpaths, camera, picture))
            shots.append(shot)

        # blur needs every shot of the file (relative rule), so it is judged after the loop
        verdicts = quality.judge_blur([s["sharpness"] for s in shots],
                                      ["black" in s["flags"] for s in shots], q)
        for s, v in zip(shots, verdicts):
            if v.blurry:
                s["flags"].append("blurry")
                s["_blur_note"] = quality.blur_note(s["id"], s["sharpness"], v, q)

        index = {
            "schema_version": SCHEMA_VERSION, "file": name, "sha256": sha, "kind": "video",
            "duration": round(pr.duration, 3), "fps": pr.fps, "width": pr.width, "height": pr.height,
            "orientation": pr.orientation, "has_speech": bool(segments), "shots": shots,
            "audio": {"has_audio": pr.has_audio, "has_music": None, "bpm": None, "peak_times": peaks},
            "quality": {},
        }
        if norm:
            index["normalized"] = norm
        log.info("  %d shot, %d khung hình chờ mô tả, giọng nói: %s", len(shots),
                 sum(len(j.frames) for j in jobs), "có" if segments else "không")
        return index, jobs

    def _finalize_ready(self) -> None:
        for sha in list(self.pending):
            index = self.pending[sha]
            keys = [(sha, s["id"]) for s in index["shots"] if s["caption"] is None]
            if all(k in self.captioner.results for k in keys):
                del self.pending[sha]
                self._write(self._finish_video(index))

    def _finish_video(self, index: dict[str, Any]) -> dict[str, Any]:
        sha = index["sha256"]
        notes, rejected = [], 0
        for s in index["shots"]:
            blur_note = s.pop("_blur_note", None)
            if s["caption"] is None:
                cap = self.captioner.results.get((sha, s["id"]))
                if cap is None:
                    s["flags"].append("caption_failed")
                else:
                    s["caption"], s["subjects"], s["shot_size"] = cap.caption, cap.subjects, cap.shot_size
            f = s["flags"]
            if "black" in f:
                notes.append(f"{s['id']}: tối dưới ngưỡng (khung hình đen)")
            if "frozen" in f:
                notes.append(f"{s['id']}: hình bị đứng (đóng băng)")
            if blur_note:
                notes.append(blur_note + " (vẫn tính là dùng được)")
            if "silent" in f and index["audio"]["has_audio"]:
                notes.append(f"{s['id']}: không có tiếng")
            if "caption_failed" in f:
                notes.append(f"{s['id']}: không tạo được mô tả (model trả lời sai định dạng)")
            rejected += bool(set(f) & REJECTING)
        if not index["audio"]["has_audio"]:
            notes.insert(0, "tệp không có âm thanh")
        index["quality"] = {"usable_shots": len(index["shots"]) - rejected, "rejected": rejected,
                            "notes": notes}
        return index

    def _write(self, index: dict[str, Any]) -> None:
        p = self.cache.put(index)
        self.report.written.append(p)
        log.info("đã ghi %s → %s", index["file"], p.name)
