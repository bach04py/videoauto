"""The one YAML library of videoauto: ruamel.yaml (declared in pyproject.toml).

Reading config.yaml / videoauto.local.yaml goes through `load`; style_edit.py writes config.yaml
back with ruamel's round-trip mode. PyYAML is not used: a clean install died at `import yaml`.
ruamel reads YAML 1.2, so a bare `off`/`on`/`yes` is the string, not a boolean; the settings
that take on/off accept both.
"""

from __future__ import annotations

import io

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

__all__ = ["YAMLError", "dump", "load"]


def _safe() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    y.width = 4096
    return y


def load(text: str):
    """Plain dicts/lists/scalars. Raises YAMLError on bad syntax."""
    return _safe().load(text)


def dump(data: dict) -> str:
    """Top-level keys in the given order (the safe dumper would sort them)."""
    out = io.StringIO()
    y = _safe()
    for k, v in data.items():
        y.dump({k: v}, out)
    return out.getvalue()
