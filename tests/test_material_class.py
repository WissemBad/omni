"""Texture roles of Glacier materials come from the material class (MATE) when the class names the slot."""
import pytest

from omni.native import AVAILABLE, N
from omni.sources.glacier import roles


def test_class_meaning_beats_the_texture_file_name():
    # slot 04 of a basic material is the emissive map even when the placeholder texture is called diffuse_a
    leaf = "[assembly:/constants/color_black.texture?/diffuse_a.tex](ascolormap).tex"
    assert roles.resolve("mapTexture2D_04", "basic", leaf, "", "mapEmissive") == "emissive"
    assert roles.resolve("mapTexture2D_04", "basic", leaf, "") == "base"             # what the file name alone said
    assert roles.resolve("mapTexture2D_03", "basic", "", "", "mapSpecular") == "spec"
    assert roles.resolve("mapTexture2D_02", "colormask", "", "", "mapRGB_Mask") == "mask"
    assert roles.resolve("mapTexture2DLinear_01", "basic", "", "", "mapSpecular_R_SpecularLevel_G_Roughness_B_Metallic") == "srm"
    assert roles.resolve("mapTexture2DCompoundNormal_01", "basic", "", "", "mapDetail") == "detail_normal"
    assert roles.resolve("mapTexture2D_01", "basic", "", "", "mapDiffuse_A_SpecOcclusion") == "base"
    assert roles.resolve("mapTexture2D_02", "basic", "", "", "mapImperfect_Details_Mask") == "mask"
    assert roles.resolve("mapTexture2D_02", "basic", "", "", "mapBrokenGlassUVOffset") == "other"
    assert roles.resolve("mapTex_Basecolor", "basic", "", "", "mapDiffuse") == "base"            # explicit names stay explicit


@pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")
def test_native_reader_pairs_slots_with_their_meaning():
    data = b"\x01\x02"
    for s in ("mapTexture2D_01", "mapDiffuse", "ConstantVector1D_01_Value", "mapTexture2D_04", "mapEmissive", "mapTex_SRM"):
        data += s.encode() + b"\0\0"
    assert N.mate_slots(data) == [("mapTexture2D_01", "mapDiffuse"), ("mapTexture2D_04", "mapEmissive"), ("mapTex_SRM", None)]
    assert N.mate_slots(b"") == [] and N.mate_slots(b"\xff" * 64) == []
