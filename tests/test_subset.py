"""Targeted extraction: the list is normalised by the Rust core and every line is traced back to its source."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from omni import subset
from omni.core.config import CONFIG
from omni.native import AVAILABLE, N

pytestmark = pytest.mark.skipif(not AVAILABLE or not hasattr(N, "parse_model_list"), reason="native module without modellist")


class FakeCatalog:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def _query(self, sql, args):
        if "rel = ?" in sql:
            return [r for r in self.rows if r["rel"] == args[0]]
        return []


def test_list_is_normalised():
    got = subset.parse("# note\nModels\Props\Chair.MDL\nmodels/props/chair.mdl\nfoo/bar.dx90.vtx\nmaterials/x.vmt\n")
    assert got == ["models/props/chair.mdl", "foo/bar.mdl"]


def test_lines_are_traced_back_without_an_export():
    src = SimpleNamespace(capabilities=("props", "characters"), title="Jeu",
                          characters=lambda: [{"id": "outfit_bond_tux", "title": "Bond", "variants": []}])
    cat = FakeCatalog([{"key": "AB12CD34EF567890", "rel": "props/chair", "cat": "props", "name": "chair"}])
    lines = subset.parse(f"models/other/g1/props/chair.mdl\nmodels/{CONFIG.namespace}/g1/pm/bond_tux.mdl\nmodels/x/g1/props/nope.mdl\n")
    out = subset.resolve(lines, ids=["g1"], source_of=lambda i: src, catalog_of=lambda i: cat)
    assert [(e.kind, e.key) for e in out[:2]] == [("prop", "AB12CD34EF567890"), ("character", "outfit_bond_tux")]
    assert not out[2].ok
    assert subset.summary(out)["games"] == {"g1": {"props": 1, "characters": 1}}
