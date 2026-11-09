"""HITMAN 3 support on a synthetic game folder: HM3 PRIM / MATI / TEXT written per the documented layouts
(RPKG-Tool), packed into Runtime/chunk0.rpkg, then read through the whole source (store, names, catalog, models,
materials, textures, characters)."""
import struct

import numpy as np
import pytest
from synthetic import MATI_H, PRIM_H, TEXT_H, hash_list, hm3_mati, hm3_prim, hm3_text, write_game

from omni.native import N

pytestmark = pytest.mark.skipif(N is None or not hasattr(N, "GlacierStore"), reason="native module without the store")


def test_hm3_decoders():
    from omni.sources.glacier import hm3
    p = hm3.parse_prim(hm3_prim())
    m = p.meshes[0]
    assert len(p.meshes) == 1 and m.positions.shape == (4, 3) and list(m.indices) == [0, 1, 2, 0, 2, 3]
    assert np.allclose(m.positions[2], (1, 1, 0), atol=1e-4) and np.allclose(m.uvs[2], (1, 1), atol=1e-4)
    assert m.colors is not None and m.lod_mask == 0xFF
    w = hm3.parse_prim(hm3_prim(weighted=True)).meshes[0]
    assert w.joints is not None and int(w.joints[0, 0]) == 1 and w.weights[0, 0] == 1.0
    mt = hm3.parse_mati(hm3_mati())
    assert mt.ok and mt.cls == "standard" and mt.blend == "Opaque"
    assert [(t.name, t.ref_index) for t in mt.textures] == [("mapTexture2D_01", 0)]
    assert [(x.name, x.values) for x in mt.params] == [("mapRoughness", [0.5])]
    t = hm3.bond_text(hm3_text())
    assert struct.unpack_from("<H", t, 0x10)[0] == 0x4C and t[0x13] == 0


def test_hitman_source_on_a_synthetic_game(tmp_path, monkeypatch):
    from omni import games
    from omni.core.config import CONFIG
    from omni.sources.glacier.hitman import HitmanSource, names_dir
    monkeypatch.setattr(CONFIG, "workspace", tmp_path / "ws")
    game = write_game(tmp_path / "HITMAN 3")
    info = games.identify(game)
    assert info["id"] == "hitman3" and info["supported"]
    names = names_dir("hitman3") / "hash_list.txt"
    names.write_text(hash_list(), encoding="utf-8")
    src = HitmanSource(info)
    rows = list(src.catalog_rows())
    assert len(rows) == 1 and rows[0]["key"] == "%016X" % PRIM_H and not rows[0]["skinned"]
    model, mats = src.load_model(PRIM_H)
    assert len(model.submeshes) == 1 and model.submeshes[0].material_key == "%016X" % MATI_H
    mat = mats["%016X" % MATI_H]
    assert [(t.role, t.key) for t in mat.textures] == [("base", "%016X" % TEXT_H)]
    tex = src.load_texture(TEXT_H)
    assert tex.fmt == "BC1" and tex.width == 4 and len(tex.mips) == 1
    assert src.ioi("[assembly:/x.prim].pc_prim") >> 56 == 0
    assert src.characters() == []
