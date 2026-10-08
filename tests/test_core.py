import struct

import numpy as np
import pytest

from omni.core.config import CONFIG
from omni.core.naming import parse_ioi, slug
from omni.sources.glacier import roles
from omni.targets.source import vtf

CRANE = "010034F5BFC0DFF2"
HAVE_ASSETS = (CONFIG.assets_sorted / "chunk0" / "PRIM" / f"{CRANE}.PRIM").exists()


def test_parse_ioi_container_and_leaf():
    d, c, l = parse_ioi("[assembly:/_knt/environment/geometry/props/industrial/crane_portable_a.wl2?/portable_hydraulic_arm_c.prim].prim")
    assert d == ["props", "industrial"] and c == "crane_portable_a" and l == "portable_hydraulic_arm_c"


def test_parse_ioi_material():
    d, c, l = parse_ioi("[assembly:/_knt/_licensed/quixel/materials/props/rocks/quixel_granite_cliff_fox_a.mi].mi")
    assert l == "quixel_granite_cliff_fox_a" and c == ""


def test_slug_is_gmod_safe():
    assert slug("Héllo World!!") == "h_llo_world"


@pytest.mark.parametrize("slot,fam,expected", [
    ("mapTex_Basecolor", "basic", "base"), ("mapTex_SRM", "basic", "srm"), ("mapTexture2DNormal_01", "basic", "normal"),
    ("mapTexture2D_01", "basic", "base"), ("mapTexture2D_03", "colormask", "base"), ("mapTexture2D_03", "basic", "spec"),
])
def test_roles(slot, fam, expected):
    assert roles.resolve(slot, fam) == expected


def test_vtf_layout(tmp_path):
    mips = [(8, 8, bytes(32)), (4, 4, bytes(8)), (2, 2, bytes(8)), (1, 1, bytes(8))]
    out = tmp_path / "t.vtf"
    vtf.write_vtf(out, vtf.DXT1, mips)
    b = out.read_bytes()
    assert b[:4] == b"VTF\0" and struct.unpack_from("<I", b, 12)[0] == 80
    assert struct.unpack_from("<HH", b, 16) == (8, 8) and len(b) == 80 + 32 + 24


@pytest.mark.skipif(not HAVE_ASSETS, reason="game assets not available")
def test_prim_crane_decode():
    from omni.sources.glacier.prim import parse_prim
    p = parse_prim((CONFIG.assets_sorted / "chunk0" / "PRIM" / f"{CRANE}.PRIM").read_bytes())
    assert len(p.meshes) == 4 and not p.weighted
    for m in p.meshes:
        assert m.indices.max() < len(m.positions) and len(m.indices) % 3 == 0
        assert np.isfinite(m.uvs).all()
        assert abs(np.linalg.norm(m.normals, axis=1) - 1).max() < 1e-3


def _fake_prim(nuv: int) -> bytes:
    """One standard sub-mesh of three vertices, in the layout of the current game build."""
    stride = 12 + 4 * nuv
    ibo, vbo = 16, 24
    ntb = vbo + 3 * 8
    mesh = ntb + 3 * stride + (-(ntb + 3 * stride) % 16)
    buf = bytearray(mesh + 128 + 4 + 24)
    struct.pack_into("<3H", buf, ibo, 0, 1, 2)
    struct.pack_into("<12h", buf, vbo, 0, 0, 0, 0, 32767, 0, 0, 0, 0, 32767, 0, 0)
    struct.pack_into("<BBHBBBBBBHI", buf, mesh, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<I", buf, mesh + 12, 0xFFFFFFFF)
    struct.pack_into("<6f", buf, mesh + 20, 1, 1, 1, 3, 3, 1)
    struct.pack_into("<7I", buf, mesh + 44, 3, vbo, 3, 0, ibo, 0, 0)
    struct.pack_into("<I", buf, mesh + 72, 0x1385F0)
    struct.pack_into("<12f", buf, mesh + 76, 2, 2, 2, 0, 1, 1, 1, 0, 1, 1, 0, 0)
    struct.pack_into("<I", buf, mesh + 124, nuv << 16)
    table = mesh + 128
    struct.pack_into("<I", buf, table, mesh)
    hdr = table + 4
    struct.pack_into("<BBHIIIII", buf, hdr, 0, 0, 1, 0, 0, 0xFFFFFFFF, 1, table)
    struct.pack_into("<Q", buf, 0, hdr)
    return bytes(buf)


@pytest.mark.parametrize("nuv", [0, 1])
def test_prim_layout_with_data_words(nuv):
    """The words at +12 and after the counts shift the box, the scale and the bias; a mesh may have no UV set."""
    from omni.sources.glacier.prim import parse_prim
    (m,) = parse_prim(_fake_prim(nuv)).meshes
    assert np.allclose(m.positions, [[1, 1, 1], [3, 1, 1], [1, 3, 1]], atol=1e-3)
    assert m.bbox_min == (1.0, 1.0, 1.0) and m.bbox_max == (3.0, 3.0, 1.0)
    assert m.indices.tolist() == [0, 1, 2] and m.uvs.shape == (3, 2)


@pytest.mark.skipif(not (HAVE_ASSETS and CONFIG.studiomdl.exists()), reason="assets or compiler missing")
def test_end_to_end_crane():
    from omni.sources.glacier.adapter import GlacierSource
    from omni.targets.source.build import build_model
    r = build_model(GlacierSource(), CRANE)
    assert r.status in ("OK", "PARTIAL") and not r.errors and r.model.endswith(".mdl")


def test_sound_naming():
    from omni.sources.glacier.audio import _game_path, _speaker, _conversation
    assert _game_path("[assembly:/_knt/sound/originals/voices/english(us)/ai_dialog/a/b_001.wav].wes") == \
        "voices/english(us)/ai_dialog/a/b_001"
    assert _speaker("vox_cc_light_elbowdown_lh_civukf08_civukf08_003") == "civukf08"
    assert _conversation("[assembly:/_knt/localization/knt/conversations/ai_dialog/merc04/x_merc04.sweetdialog].dialogevent") == "ai_dialog/merc04"
