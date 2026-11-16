"""Index validation: JSON Schema plus invariants the schema cannot express."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import IndexValidationError

SCHEMA_PATH = Path(__file__).parent / "schemas" / "index.schema.json"
SCHEMA_VERSION = 1


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(index: Any) -> list[str]:
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in _validator().iter_errors(index)]
    if errs or not isinstance(index, dict) or index.get("kind") != "video":
        return errs
    shots = index["shots"]
    for n, s in enumerate(shots, 1):
        if s["id"] != f"s{n:02d}":
            errs.append(f"shots/{n-1}/id: expected s{n:02d}, got {s['id']}")
        if not s["start"] < s["end"]:
            errs.append(f"shots/{n-1}: start >= end")
        if not all(s["start"] <= k <= s["end"] for k in s["keyframes"]):
            errs.append(f"shots/{n-1}: keyframe outside shot")
        if n > 1 and abs(s["start"] - shots[n - 2]["end"]) > 1e-6:
            errs.append(f"shots/{n-1}: not contiguous with previous shot")
    if shots and shots[-1]["end"] > index["duration"] + 0.5:
        errs.append("shots: last shot ends after duration")
    rejected = sum(1 for s in shots if set(s["flags"]) & {"black", "frozen", "blurry"})
    q = index["quality"]
    if q["rejected"] != rejected or q["usable_shots"] != len(shots) - rejected:
        errs.append("quality: usable_shots/rejected do not match shot flags")
    if index["has_speech"] != any(s["transcript"] for s in shots):
        errs.append("has_speech does not match shot transcripts")
    return errs


def validate(index: Any) -> None:
    errs = errors(index)
    if errs:
        name = index.get("file", "?") if isinstance(index, dict) else "?"
        raise IndexValidationError(f"index for {name} failed validation:\n  " + "\n  ".join(errs))
