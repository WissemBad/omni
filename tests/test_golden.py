"""Golden outputs of the writers: a fixed input must produce the same bytes (VTF, SMD, glTF). A format change is then a
visible, deliberate diff of tests/golden/hashes.json instead of a silent one."""
import numpy as np
import pytest

from omni.native import AVAILABLE, N

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")


def rgba16() -> np.ndarray:
    return (np.arange(16 * 16 * 4, dtype=np.uint32) * 7 % 256).astype(np.uint8).reshape(16, 16, 4)


@pytest.mark.parametrize("fmt", ["dxt1", "dxt5"])
def test_vtf_encoding(tmp_path, golden, fmt):
    out = tmp_path / f"{fmt}.vtf"
    N.encode_vtf(str(out), rgba16(), fmt, "srgb", 0, 0, 1, 0.0)
    golden(f"vtf-{fmt}-16x16", out.read_bytes())


def test_smd_static_mesh(golden):
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], np.float64)
    normals = np.tile(np.array([0, 0, 1.0]), (4, 1))
    uvs = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], np.float64)
    text, triangles = N.smd_static("crate", positions, normals, uvs, np.array([0, 1, 2, 1, 3, 2], np.int64))
    assert triangles == 2
    golden("smd-static-quad", text.encode())


def test_glb_of_a_skinned_quad(tmp_path, golden):
    from test_gltf import Rig, Source

    from omni.core.ir import Material, Model, SubMesh, TextureRef
    from omni.targets.gltf.export import write_glb
    sm = SubMesh(positions=np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], np.float32),
                 normals=np.tile(np.float32([0, -1, 0]), (4, 1)), uvs=np.zeros((4, 2), np.float32),
                 indices=np.array([0, 1, 2, 0, 2, 3], np.uint32), material_key="M",
                 joints=np.array([[0, 1, 0, 0]] * 4, np.uint16), weights=np.array([[0.5, 0.5, 0, 0]] * 4, np.float32))
    m = Model("K", "props/quad", [sm], skinned=True)
    m.rig = Rig(["root", "top"], [-1, 0])
    mat = Material("M", "quad_mat", textures=[TextureRef("base", "s", "0000000000000001")])
    out = write_glb(Source(), m, {"M": mat}, tmp_path / "quad.glb", 64)
    golden("glb-skinned-quad", out.read_bytes())
