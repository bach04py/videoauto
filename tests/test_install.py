"""A clean `pip install -e .` must be enough: every library videoauto imports is declared in
pyproject.toml, and YAML goes through one library only (ruamel.yaml). A laptop reinstalled from
scratch died at `import yaml` (PyYAML) in watch.py while the writer used ruamel.yaml."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

from conftest import REPO

PKG = REPO / "videoauto"
OPTIONAL_MODULES = {"librosa"}          # extra "music": imported lazily, with its own error message


def _norm(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _declared() -> set[str]:
    meta = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(meta["dependencies"])
    for extra in meta.get("optional-dependencies", {}).values():
        reqs += extra
    out = set()
    for r in reqs:
        name = r.split(";")[0]
        for sep in "<>=!~[ ":
            name = name.split(sep)[0]
        out.add(_norm(name))
    return out


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def _third_party(mods: set[str]) -> set[str]:
    return {m for m in mods if m not in sys.stdlib_module_names and m != "videoauto"}


def test_every_imported_library_is_declared_in_pyproject():
    dists = packages_distributions()
    declared = _declared()
    missing = []
    for f in sorted(PKG.rglob("*.py")):
        for mod in sorted(_third_party(_imports(f))):
            names = {_norm(d) for d in dists.get(mod, [])}
            if not names:
                missing.append(f"{f.relative_to(REPO)}: {mod} (not installed in this venv either)")
            elif not names & declared:
                missing.append(f"{f.relative_to(REPO)}: {mod} -> {sorted(names)} not in pyproject.toml")
    assert not missing, "\n".join(missing)


def test_one_yaml_library_ruamel_only():
    users = [str(f.relative_to(REPO)) for f in sorted([*PKG.rglob("*.py"), *(REPO / "tests").glob("*.py")])
             if "yaml" in _imports(f)]
    assert users == [], f"PyYAML imported by {users}: use videoauto.yamlio (ruamel.yaml)"
    assert "pyyaml" not in _declared()


BLOCKER = r"""
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "yaml" or name.startswith("yaml."):
            raise ModuleNotFoundError("No module named 'yaml' (simulated clean install)")
        return None
sys.meta_path.insert(0, Block())
import os, pathlib, pkgutil, importlib
os.chdir(sys.argv[1])
import videoauto
for m in pkgutil.walk_packages(videoauto.__path__, "videoauto."):
    if m.name.endswith("__main__"):
        continue
    importlib.import_module(m.name)
from videoauto.brief import load_project_config
from videoauto import watch
cfg = load_project_config(pathlib.Path(sys.argv[2]))
assert cfg.brief.aspect == "9:16" and cfg.whisper.get("model") == "off", (cfg.brief, cfg.whisper)
f = watch.save({"footage": pathlib.Path("C:/a"), "scripts": pathlib.Path("C:/b b"), "output": pathlib.Path("C:/Hoàng")})
got = watch.load_saved()
assert got == {"footage": str(pathlib.Path("C:/a")), "scripts": str(pathlib.Path("C:/b b")),
               "output": str(pathlib.Path("C:/Hoàng"))}, got
print("OK")
"""


def test_runs_without_pyyaml_installed(tmp_path):
    """Every module imports, the committed config.yaml loads and videoauto.local.yaml round-trips
    with PyYAML absent (as on a machine where only pyproject.toml's dependencies were installed)."""
    proc = subprocess.run([sys.executable, "-c", BLOCKER, str(tmp_path), str(REPO / "config.yaml")],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stderr[-2000:]
