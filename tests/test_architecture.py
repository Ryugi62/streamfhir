"""AC-15: dependencies point inwards only."""
import os
import re

from tests.conftest import ROOT

FORBIDDEN = {
    "domain": ("streamfhir.application", "streamfhir.adapters", "streamfhir.infrastructure",
               "urllib", "http", "socket", "json", "os"),
    "application": ("streamfhir.adapters", "streamfhir.infrastructure", "urllib", "http", "socket"),
}


def _imports(path):
    with open(path) as fh:
        src = fh.read()
    return re.findall(r"^\s*(?:from|import)\s+([\w\.]+)", src, re.M)


def test_ac15_inner_layers_do_not_import_outer_layers_or_io():
    for layer, banned in FORBIDDEN.items():
        folder = os.path.join(ROOT, "streamfhir", layer)
        for name in os.listdir(folder):
            if name.endswith(".py"):
                for mod in _imports(os.path.join(folder, name)):
                    assert not any(mod == b or mod.startswith(b + ".") for b in banned), (layer, name, mod)
