"""AC-15: dependencies point inwards only."""
import os
import re

from tests.conftest import ROOT

FORBIDDEN = {
    "domain": ("streamfhir.application", "streamfhir.adapters", "streamfhir.infrastructure",
               "urllib", "http", "socket", "json", "os"),
    "application": ("streamfhir.adapters", "streamfhir.infrastructure", "urllib", "http", "socket"),
}


def _resolve(mod, layer):
    """Turn relative imports (from ..adapters import x) into absolute module names."""
    if not mod.startswith("."):
        return mod
    dots = len(mod) - len(mod.lstrip("."))
    parts = ["streamfhir", layer][: 2 - (dots - 1)]
    rest = mod.lstrip(".")
    return ".".join(parts + ([rest] if rest else []))


def _imports(path, layer):
    with open(path) as fh:
        src = fh.read()
    return [_resolve(m, layer) for m in re.findall(r"^\s*(?:from|import)\s+([\w\.]+)", src, re.M)]


def test_relative_import_resolution():
    assert _resolve("..adapters.fhir_mapper", "domain") == "streamfhir.adapters.fhir_mapper"
    assert _resolve(".indicators", "domain") == "streamfhir.domain.indicators"


def test_ac15_inner_layers_do_not_import_outer_layers_or_io():
    for layer, banned in FORBIDDEN.items():
        folder = os.path.join(ROOT, "streamfhir", layer)
        for name in os.listdir(folder):
            if name.endswith(".py"):
                for mod in _imports(os.path.join(folder, name), layer):
                    assert not any(mod == b or mod.startswith(b + ".") for b in banned), (layer, name, mod)
