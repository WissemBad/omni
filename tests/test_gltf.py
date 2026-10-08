"""glTF target: a skinned quad with a material and a two-bone rig is written as a valid .glb."""
from dataclasses import dataclass

import numpy as np
from pygltflib import GLTF2

from omni.core.ir import Material, Model, SubMesh, TextureData, TextureRef
from omni.targets.gltf.export import write_glb


@dataclass
class Rig:
    names: list
    parents: list

    def world(self):
        w = np.tile(np.eye(4), (2, 1, 1))
        w[1, 2, 3] = 1.0
        return w


class Source:
    def load_texture(self, h):
        return TextureData("%016X" % h, "RGBA8", 8, 8, [(8, 8, bytes([200, 100, 50, 255]) * 64)])


def test_skinned_model_with_pbr_material(tmp_path):
    sm = SubMesh(positions=np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], np.float32),
                 normals=np.tile(np.float32([0, -1, 0]), (4, 1)), uvs=np.zeros((4, 2), np.float32),
                 indices=np.array([0, 1, 2, 0, 2, 3], np.uint32), material_key="M",
                 joints=np.array([[0, 1, 0, 0]] * 4, np.uint16), weights=np.array([[0.5, 0.5, 0, 0]] * 4, np.float32))
    m = Model("K", "props/quad", [sm], skinned=True)
    m.rig = Rig(["root", "top"], [-1, 0])
    mat = Material("M", "quad_mat", textures=[TextureRef("base", "s", "0000000000000001"),
                                              TextureRef("orm", "ORM", "0000000000000002")])
    out = write_glb(Source(), m, {"M": mat}, tmp_path / "quad.glb", 64)
    g = GLTF2().load(str(out))
    assert len(g.meshes) == 1 and len(g.skins) == 1 and len(g.skins[0].joints) == 2
    assert g.materials[0].name == "quad_mat" and g.materials[0].pbrMetallicRoughness.metallicRoughnessTexture is not None
    assert g.materials[0].occlusionTexture is not None and len(g.images) == 3
    prim = g.meshes[0].primitives[0]
    assert prim.attributes.JOINTS_0 is not None and prim.attributes.WEIGHTS_0 is not None
