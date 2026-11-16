"""Stale indexes: one summary warning in the planner, explicit and guarded --prune."""

from __future__ import annotations

import io
import json
import shutil
import sys

import pytest

from conftest import FakeProvider
from videoauto import index as index_cli
from videoauto.cache import sha256_of
from videoauto.config import load_config
from videoauto.errors import UserError
from videoauto.indexer import Indexer, collect
from videoauto.planner.planner import load_material
from videoauto.prune import prune


@pytest.fixture(scope="module")
def built(tmp_path_factory, gym, sunset):
    root = tmp_path_factory.mktemp("prune")
    media = root / "media"
    media.mkdir()
    shutil.copy(gym, media / "gym.mp4")
    shutil.copy(sunset, media / "sunset.mp4")
    idx = root / "idx"
    Indexer(load_config(), idx, provider=FakeProvider()).run(collect(media, idx))
    return media, idx


@pytest.fixture
def lib(tmp_path, built):
    """A private copy of footage + index, plus three orphaned indexes (content no longer present)."""
    media, idx = tmp_path / "media", tmp_path / "idx"
    shutil.copytree(built[0], media)
    shutil.copytree(built[1], idx)
    gym_ix = json.loads((idx / f"{sha256_of(media / 'gym.mp4')}.json").read_text(encoding="utf-8"))
    orphans = []
    for i, name in enumerate(("gym.mp4", "C0140.MP4", "C0141.MP4")):     # old version of gym.mp4 too
        sha = f"{i:x}" * 64
        (idx / f"{sha}.json").write_text(json.dumps({**gym_ix, "sha256": sha, "file": name}),
                                         encoding="utf-8")
        (idx / "frames" / sha).mkdir(parents=True)
        (idx / "frames" / sha / "k.jpg").write_bytes(b"jpg")
        orphans.append(sha)
    return media, idx, orphans, gym_ix


def test_orphaned_indexes_give_one_summary_line(lib):
    media, idx, orphans, _ = lib
    m = load_material(idx, media)
    assert set(m.indexes) == {"gym.mp4", "sunset.mp4"}
    assert m.indexes["gym.mp4"]["sha256"] == sha256_of(media / "gym.mp4")     # the version on disk
    assert len(m.warnings) == 1
    assert m.warnings[0].startswith("bỏ qua 3 chỉ mục cũ không còn tệp tương ứng trong thư mục video")
    assert "--prune" in m.warnings[0]


def test_same_name_different_content_both_present_keeps_its_own_line(lib):
    media, idx, _, gym_ix = lib
    other = media / "ngay2" / "gym.mp4"
    other.parent.mkdir()
    other.write_bytes((media / "gym.mp4").read_bytes() + b"\0" * 16)          # a replaced take
    sha = sha256_of(other)
    (idx / f"{sha}.json").write_text(json.dumps({**gym_ix, "sha256": sha}), encoding="utf-8")
    m = load_material(idx, media)
    per_file = [w for w in m.warnings if w.startswith("'gym.mp4'")]
    assert len(per_file) == 1 and "2 tệp cùng tên nhưng khác nội dung" in per_file[0]
    assert "gym.mp4" in per_file[0] and "ngay2" in per_file[0] and "kế hoạch chỉ dùng" in per_file[0]
    assert sum("chỉ mục cũ" in w for w in m.warnings) == 1                    # orphans still one line


def test_prune_refuses_when_footage_folder_missing_or_empty(lib, tmp_path):
    _, idx, _, _ = lib
    before = sorted(p.name for p in idx.glob("*.json"))
    with pytest.raises(UserError, match="không tồn tại.*chưa được kết nối"):
        prune(tmp_path / "o-mang-chua-noi", idx, ask=lambda _: "y")
    empty = tmp_path / "trong"
    empty.mkdir()
    (empty / "ghi-chu.txt").write_text("x", encoding="utf-8")
    with pytest.raises(UserError, match="không có tệp video nào"):
        prune(empty, idx, ask=lambda _: "y")
    assert sorted(p.name for p in idx.glob("*.json")) == before               # nothing touched


def test_prune_lists_asks_and_deletes_only_on_yes(lib, capsys):
    media, idx, orphans, _ = lib
    asked = []
    assert prune(media, idx, ask=lambda q: asked.append(q) or "") == 0        # Enter = no
    out = capsys.readouterr().out
    assert "Sẽ xoá 3 chỉ mục" in out and "C0140.MP4" in out and "C0141.MP4" in out
    assert asked and "[y/N]" in asked[0] and "Không xoá gì" in out
    assert all((idx / f"{s}.json").exists() for s in orphans)

    assert prune(media, idx, ask=lambda _: "y") == 0
    assert not any((idx / f"{s}.json").exists() or (idx / "frames" / s).exists() for s in orphans)
    for f in ("gym.mp4", "sunset.mp4"):                                        # live indexes kept
        assert (idx / f"{sha256_of(media / f)}.json").exists()
    assert prune(media, idx, ask=lambda _: "y") == 0
    assert "Không có chỉ mục cũ nào" in capsys.readouterr().out


def test_prune_cli(lib, monkeypatch, capsys):
    media, idx, orphans, _ = lib
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert index_cli.main([str(media), "--prune", "--out", str(idx)]) == 0
    assert not any((idx / f"{s}.json").exists() for s in orphans)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert index_cli.main([str(media / "khong-co"), "--prune", "--out", str(idx)]) == 2
    assert "Không dọn gì" in capsys.readouterr().err
