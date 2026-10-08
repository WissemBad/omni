"""How materials are read for every target: packed channels, roles from the class, UV tiling, normal convention."""
import struct

import numpy as np
import pytest

from omni.core.ir import Material, TextureData, TextureRef
from omni.native import AVAILABLE, N
from omni.sources.glacier import roles
from omni.sources.glacier.adapter import base_coords


def test_packed_channels_follow_the_class_names():
    assert roles.packed_channels("mapSpecular_R_SpecularLevel_G_Roughness_B_Metallic") == "SRM_"
    assert roles.packed_channels("mapSpecular_R_Metal_G_Roughness_B_AO") == "MRO_"
    assert roles.packed_channels("mapR_Specular_G_Gloss_B_Emissive") == "SGE_"
    # modulators named like surface terms are not surface terms
    assert roles.packed_channels("mapMask_R_Dirt_G_SpecularCavity_B_RoughnessGrease_") == ""
    assert roles.packed_channels("mapDiffuse") == ""


def test_roles_of_packed_and_layered_slots():
    assert roles.semantic_role("mapTexture2D_03", "mapR_Specular_G_Gloss_B_ColorMask") == "srm"
    assert roles.semantic_role("mapTexture2D_01", "mapDiffuse__RGB____Height__A_") == "base"
    assert roles.semantic_role("mapTexture2DNormal_02", "mapDetailNormal") == "detail_normal"
    assert roles.semantic_role("mapTexture2D_01", "mapDetailR_BaseColorG_Dirt") == "detail"
    assert roles.semantic_role("mapTexture2D_05", "mapAO") == "ao"
    assert roles.semantic_role("mapTexture2D_02", "mapDiffuseID_Mask") == "mask"


def test_base_coords_tile_the_uvs():
    uv = np.array([[0.0, 0.0], [1.0, 0.5]], np.float32)
    mat = Material("M", "m", params={"gm_mBaseCoords": [10.0, -0.0, -0.16, 0.0, 0.0, 10.0, -0.03, 1.0]})
    assert np.allclose(base_coords(uv, mat), [[-0.16, -0.03], [9.84, 4.97]])
    ident = Material("M", "m", params={"gm_mBaseCoords": [1.0, -0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0]})
    assert base_coords(uv, ident) is uv
    flat = Material("M", "m", params={"gm_mBaseCoords": [0.0] * 8})
    assert base_coords(uv, flat) is uv                       # degenerate: would collapse the mesh's UVs


class _Source:
    def __init__(self, textures):
        self.textures = textures

    def load_texture(self, h):
        return self.textures.get("%016X" % h)


def _rgba8(px, w=8, h=8):
    return TextureData("x", "RGBA8", w, h, [(w, h, bytes(px) * (w * h))])


@pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")
def test_surface_reads_the_declared_layout_and_gloss():
    from omni.targets import shading as sh
    tex = {"0000000000000001": _rgba8([255, 51, 102, 255])}   # MRO_: metal 1, roughness 0.2, AO 0.4
    mat = Material("M", "m", textures=[TextureRef("srm", "s", "0000000000000001", "MRO_")])
    s = sh.surface(mat, sh.TextureCache(_Source(tex)), 64)
    assert np.allclose(s.metal, 1.0) and np.allclose(s.rough, 0.2, atol=0.01) and np.allclose(s.ao, 0.4, atol=0.01)
    assert np.allclose(s.spec, 0.5)
    tex = {"0000000000000002": _rgba8([60, 60, 60, 204])}     # spec/gloss map: alpha = gloss 0.8
    mat = Material("M", "m", textures=[TextureRef("spec", "s", "0000000000000002")])
    s = sh.surface(mat, sh.TextureCache(_Source(tex)), 64)
    assert np.allclose(s.rough, 0.2, atol=0.01) and float(s.metal.max()) < 0.05   # ~4 % reflectance: a dielectric


@pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")
def test_source_normals_point_green_down():
    from omni.targets import shading as sh
    tex = TextureData("x", "RGBA8", 8, 8, [(8, 8, bytes([128, 200, 200, 255]) * 64)])
    assert sh.normal(tex, 64)[0, 0, 1] == 200
    assert sh.normal(tex, 64, flip_y=True)[0, 0, 1] == 55
    assert sh.normal(TextureData("x", "RGBA8", 4, 4, [(4, 4, b"\0" * 64)]), 64) is None   # placeholder


@pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")
def test_vtf_mips_are_anisotropic(tmp_path):
    p = tmp_path / "a.vtf"
    N.encode_vtf(str(p), np.full((16, 16, 4), 128, np.uint8), "dxt1", "srgb", 0, 0, 0, 0.0)
    assert struct.unpack_from("<I", p.read_bytes(), 20)[0] & 0x10
