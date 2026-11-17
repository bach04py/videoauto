"""Watcher key S: the style fields are written back into config.yaml without losing its comments,
atomically, and never over a file that changed underneath."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_planner import CATALOGUE, LUT
from videoauto import style_edit as se
from videoauto.brief import load_project_config, parse_style
from videoauto.errors import UserError
from videoauto.planner.catalogue import Catalogue
from videoauto.planner.planner import _style_prompt

COMMENTED = """\
# videoauto config. Relative paths are relative to this file.
# Lines marked CHANGE are the ones you must edit for your machine.

brief:
  duration: 15          # target length in seconds
  aspect: "9:16"        # 9:16 | 16:9 | 1:1 | 4:5 | 4:3 | 3:4

footage_dir: D:/quay/buoi-tap        # CHANGE: folder with the raw clips

brand:
  name: "Hoàng Gia Gym & Fitness 24h"
  context: |                         # optional free text for the planner (never sent to captioning)
    Chuỗi phòng tập, hồ bơi và sân pickleball ở Bình Dương.
    Giọng điệu khỏe khoắn, hướng tới hội viên mới.
  colors:
    accent: "#ED1C24"                # Keep the quotes: YAML reads # as a comment

style:                               # optional; steers the planner
  tone: ""                           # one line
  pace: ""                           # fast | medium | calm
  prefer_luts: []                    # LUT ids from catalogue.json

catalogue: catalogue.json     # generated from OpenChatCut (README step 2)

openchatcut:
  repo: ../OpenChatCut        # CHANGE if OpenChatCut is not next to the videoauto folder
  gpu_encode: auto            # auto | on | off
"""

NEW = {"context": "Phòng tập 24h ở Thủ Dầu Một.\nDành cho sinh viên và dân văn phòng.",
       "tone": "mạnh mẽ, năng động", "pace": "fast", "prefer_luts": [LUT["assetId"]]}


def comments(text: str) -> list[str]:
    """Every comment, as written (the part from # on), in order."""
    out = []
    for ln in text.splitlines():
        i = ln.find("# ")
        if ln.lstrip().startswith("#") or (i > 0 and '"#' not in ln[:i + 1]):
            out.append(ln[ln.find("#"):].strip())
    return out


@pytest.fixture
def conf(tmp_path) -> Path:
    (tmp_path / "catalogue.json").write_text(json.dumps(CATALOGUE), encoding="utf-8")
    p = tmp_path / "config.yaml"
    p.write_text(COMMENTED, encoding="utf-8")
    return p


def test_save_keeps_every_comment_and_changes_only_the_four_values(conf):
    snap = se.snapshot(conf, conf.parent / "catalogue.json")
    assert snap.values["context"].startswith("Chuỗi phòng tập")
    se.save(snap, NEW)
    after = conf.read_text(encoding="utf-8")
    assert comments(after) == comments(COMMENTED)                # all of them, same order
    assert "CHANGE: folder with the raw clips" in after and "CHANGE if OpenChatCut" in after
    assert se.values_of(se._load(conf.read_bytes())) == NEW
    # every line that is not one of the four values is byte-identical
    edited = ("    Chuỗi", "    Giọng", "  tone:", "  pace:", "  prefer_luts:")
    keep = [ln for ln in COMMENTED.splitlines() if not ln.startswith(edited)]
    assert [ln for ln in after.splitlines() if ln in keep] == keep
    # quoting style and the comment column of the original line are kept
    assert '  tone: "mạnh mẽ, năng động"         # one line\n' in after
    assert '  pace: "fast"                       # fast | medium | calm\n' in after
    assert f"  prefer_luts: [{LUT['assetId']}]" in after
    cfg = load_project_config(conf)                              # what the next build reads
    assert cfg.style.tone == "mạnh mẽ, năng động" and cfg.style.pace == "fast"
    assert cfg.brand.context == NEW["context"]


def test_saving_unchanged_values_is_byte_identical(conf):
    snap = se.snapshot(conf)
    se.save(snap, dict(snap.values))
    assert conf.read_text(encoding="utf-8") == COMMENTED


def test_missing_style_section_is_added_after_brand(tmp_path):
    p = tmp_path / "config.yaml"
    text = COMMENTED.split("style:")[0] + "catalogue: catalogue.json  # generated\n"
    p.write_text(text, encoding="utf-8")
    se.save(se.snapshot(p), NEW)
    after = p.read_text(encoding="utf-8")
    assert after.index("brand:") < after.index("style:") < after.index("catalogue:")
    assert comments(after) == comments(text)
    assert load_project_config(p).style.prefer_luts == NEW["prefer_luts"]


def test_bom_survives(conf):
    conf.write_bytes(se.BOM + COMMENTED.encode("utf-8"))
    se.save(se.snapshot(conf), NEW)
    assert conf.read_bytes().startswith(se.BOM)
    assert load_project_config(conf).style.pace == "fast"


def test_changed_on_disk_is_refused_unless_confirmed_and_keeps_the_other_edit(conf):
    snap = se.snapshot(conf)
    conf.write_text(COMMENTED.replace("duration: 15", "duration: 30"), encoding="utf-8")   # someone else
    with pytest.raises(se.ChangedOnDisk):
        se.save(snap, NEW)
    assert "duration: 30" in conf.read_text(encoding="utf-8")                 # untouched
    se.save(snap, NEW, overwrite_changed=True)
    after = conf.read_text(encoding="utf-8")
    assert "duration: 30" in after and "mạnh mẽ" in after


def test_crash_while_replacing_leaves_config_whole_and_no_temp(conf, monkeypatch):
    snap = se.snapshot(conf)

    def boom(src, dst):
        raise OSError("power cut")
    monkeypatch.setattr(se.os, "replace", boom)
    with pytest.raises(OSError):
        se.save(snap, NEW)
    assert conf.read_text(encoding="utf-8") == COMMENTED
    assert sorted(p.name for p in conf.parent.iterdir()) == ["catalogue.json", "config.yaml"]


def test_invalid_result_never_replaces_config(conf):
    snap = se.snapshot(conf)

    def reject(tmp: Path):
        assert tmp.parent == conf.parent and tmp != conf                      # validated before replace
        raise UserError("nope")
    with pytest.raises(UserError):
        se.save(snap, NEW, validate=reject)
    assert conf.read_text(encoding="utf-8") == COMMENTED
    assert sorted(p.name for p in conf.parent.iterdir()) == ["catalogue.json", "config.yaml"]


def test_temp_file_is_written_in_full_before_replace(conf, monkeypatch):
    """The real file is only ever swapped for a complete, fsynced temp file."""
    seen = {}
    real = os.replace

    def spy(src, dst):
        seen["text"] = Path(src).read_text(encoding="utf-8")
        real(src, dst)
    monkeypatch.setattr(se.os, "replace", spy)
    se.save(se.snapshot(conf), NEW)
    assert seen["text"] == conf.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the interactive editor

def scripted(*answers):
    it = iter(answers)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return next(it)
    return ask, asked


def test_enter_everywhere_keeps_everything(conf):
    ask, asked = scripted("", "", "", "")
    out = []
    assert se.edit(se.snapshot(conf), ask, out.append) is None
    assert "Không có gì thay đổi." in out
    assert any("Chuỗi phòng tập" in ln for ln in out)              # the current value is shown
    assert asked[1].startswith("2. tone") and asked[2].startswith("3. pace (fast | medium | calm) [(trống)]")


def test_editor_multiline_context_validation_and_summary(conf):
    ask, asked = scripted(
        "Phòng tập 24h ở Thủ Dầu Một.", "", "Dành cho sinh viên.", ".",       # context, ends with "."
        "  mạnh mẽ,   năng động ",                                            # tone (spaces folded)
        "nhanh", "FAST",                                                      # pace: rejected, then ok
        "builtin:nope", f"{LUT['assetId']} , {LUT['assetId']}")               # luts: rejected, then ok
    out = []
    new = se.edit(se.snapshot(conf, conf.parent / "catalogue.json"), ask, out.append)
    assert new == {"context": "Phòng tập 24h ở Thủ Dầu Một.\n\nDành cho sinh viên.",
                   "tone": "mạnh mẽ, năng động", "pace": "fast", "prefer_luts": [LUT["assetId"]]}
    assert "   Chỉ nhận: fast, medium, calm." in out
    assert "   Không có trong catalogue.json: builtin:nope" in out
    assert any(ln.startswith(" *2. tone: mạnh mẽ") for ln in out)        # changed fields are marked


def test_dash_clears(conf):
    conf.write_text(COMMENTED.replace('tone: ""', 'tone: "êm"'), encoding="utf-8")
    ask, _ = scripted("-", "-", "", "")
    new = se.edit(se.snapshot(conf), ask, lambda s: None)
    assert new["context"] == "" and new["tone"] == ""
    se.save(se.snapshot(conf), new)
    cfg = load_project_config(conf)
    assert cfg.brand.context == "" and cfg.style.tone == ""


# ---------------------------------------------------------------- config parsing and the planner

def test_style_parsing():
    assert parse_style(None).is_set is False
    st = parse_style({"tone": "a  b", "pace": "Calm", "prefer_luts": "x, y,x"})
    assert (st.tone, st.pace, st.prefer_luts) == ("a b", "calm", ["x", "y"])
    with pytest.raises(UserError, match="style.pace"):
        parse_style({"pace": "slow"})


def test_style_goes_into_the_prompt_and_unknown_luts_are_dropped(conf):
    se.save(se.snapshot(conf), dict(NEW, prefer_luts=[LUT["assetId"], "builtin:nope"]))
    cfg = load_project_config(conf)
    cat = Catalogue.load(cfg.catalogue)
    block = _style_prompt(cfg, cat)
    assert block.startswith("STYLE")
    assert "- tone: mạnh mẽ, năng động" in block and "- pace: fast:" in block
    assert LUT["assetId"] in block and "builtin:nope" not in block


def test_no_style_no_block(conf):
    cfg = load_project_config(conf)
    assert _style_prompt(cfg, Catalogue.load(cfg.catalogue)) == ""
