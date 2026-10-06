"""glTF 2.0 target (.glb): any source's models for Blender and other tools, next to the Garry's Mod export.

One self-contained .glb per model: geometry (Y up, metres), PBR metal/roughness materials (base colour, normal,
metallic-roughness and occlusion rebuilt from the game's own maps: Unreal ORM, Glacier SRM, separate roughness /
metallic maps), alpha mode from the material flags, and for skinned models the skeleton and skin weights (bind
pose). Written to ``<workspace>/exports/<source>/gltf/<model path>.glb``. Game-independent: it only reads the IR.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
from pygltflib import (ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, FLOAT, GLTF2, UNSIGNED_INT, UNSIGNED_SHORT, Accessor,
                       Asset, Attributes, Buffer, BufferView, Image as GImage, Material as GMat, Mesh, Node,
                       NormalMaterialTexture, OcclusionTextureInfo, PbrMetallicRoughness, Primitive, Sampler, Scene,
                       Skin, Texture, TextureInfo)

from ...core.config import CONFIG
from ...core.ir import Material, Model

# Z up, facing +Y (omni) -> Y up, facing +Z (glTF convention): (x, y, z) -> (-x, z, y), a proper rotation
_ZUP = np.array([[-1, 0, 0], [0, 0, 1], [0, 1, 0]], np.float64)


def _yup(v: np.ndarray) -> np.ndarray:
    return (np.asarray(v, np.float64) @ _ZUP.T).astype(np.float32)


def _rgba(source, key: str, size: int, normal: bool = False):
    from ...sources.glacier.texture import Mip, to_rgba
    t = source.load_texture(int(key, 16))
    if t is None or not t.mips:
        return None
    mips = sorted(t.mips, key=lambda m: -m[0] * m[1])
    w, h, d = next((m for m in mips if max(m[0], m[1]) <= size), mips[-1])
    a = to_rgba(t.fmt, Mip(w, h, d))
    if normal and t.fmt in ("BC5", "BC4", "RG8"):
        from ..source.textures import rebuild_normal
        a = rebuild_normal(a)
    return a


def _png(a: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(a)).save(b, "PNG", optimize=False)
    return b.getvalue()


def _fit(a: np.ndarray, shape) -> np.ndarray:
    if a.shape[:2] == shape:
        return a
    ys = np.linspace(0, a.shape[0] - 1, shape[0]).astype(int)
    xs = np.linspace(0, a.shape[1] - 1, shape[1]).astype(int)
    return a[ys][:, xs]


def _first(m: Material, role: str):
    return next((t for t in m.textures if t.role == role), None)


def metal_rough(source, m: Material, size: int):
    """(metallic-roughness RGBA (G rough, B metal), occlusion RGBA or None) from whatever maps the game has."""
    orm, srm = _first(m, "orm"), _first(m, "srm")
    rough_ref, metal_ref, ao_ref = _first(m, "rough"), _first(m, "metal"), _first(m, "ao")
    rough = metal = ao = None
    if orm is not None:
        a = _rgba(source, orm.key, size)
        if a is not None:
            order = (orm.slot or "ORM").upper()
            order = order if len(order) == 3 and "R" in order else "ORM"
            rough = a[..., order.find("R")]
            metal = a[..., order.find("M")] if "M" in order else None
            ao = a[..., order.find("O")] if "O" in order else (a[..., order.find("A")] if "A" in order else None)
    elif srm is not None:
        a = _rgba(source, srm.key, size)
        if a is not None:
            rough, metal = a[..., 1], a[..., 2]
    if rough is None and rough_ref is not None:
        a = _rgba(source, rough_ref.key, size)
        rough = a[..., 0] if a is not None else None
    if metal is None and metal_ref is not None and rough is not None:
        a = _rgba(source, metal_ref.key, size)
        metal = _fit(a[..., 0], rough.shape) if a is not None else None
    if ao is None and ao_ref is not None:
        a = _rgba(source, ao_ref.key, size)
        ao = a[..., 0] if a is not None else None
    mr = None
    if rough is not None:
        mr = np.zeros(rough.shape + (4,), np.uint8)
        mr[..., 1] = rough
        mr[..., 2] = metal if metal is not None else 0
        mr[..., 3] = 255
    occ = None
    if ao is not None:
        occ = np.repeat(ao[..., None], 4, -1)
        occ[..., 3] = 255
    return mr, occ


class _Writer:
    def __init__(self):
        self.g = GLTF2(asset=Asset(version="2.0", generator="omni"))
        self.blob = bytearray()
        self.g.samplers.append(Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))

    def view(self, data: bytes, target=None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        self.g.bufferViews.append(BufferView(buffer=0, byteOffset=len(self.blob), byteLength=len(data), target=target))
        self.blob.extend(data)
        return len(self.g.bufferViews) - 1

    def acc(self, arr: np.ndarray, ctype, typ, target=None, minmax=False) -> int:
        a = Accessor(bufferView=self.view(np.ascontiguousarray(arr).tobytes(), target), componentType=ctype,
                     count=len(arr), type=typ)
        if minmax:
            a.min, a.max = arr.min(0).tolist(), arr.max(0).tolist()
        self.g.accessors.append(a)
        return len(self.g.accessors) - 1

    def texture(self, rgba: np.ndarray) -> int:
        self.g.images.append(GImage(bufferView=self.view(_png(rgba)), mimeType="image/png"))
        self.g.textures.append(Texture(source=len(self.g.images) - 1, sampler=0))
        return len(self.g.textures) - 1

    def save(self, out: Path) -> None:
        self.g.buffers.append(Buffer(byteLength=len(self.blob)))
        self.g.set_binary_blob(bytes(self.blob))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".glb.part")
        self.g.save_binary(str(tmp))
        tmp.replace(out)


def write_glb(source, model: Model, materials: dict[str, Material], out: Path, tex_size: int = 4096) -> Path:
    w = _Writer()
    g = w.g
    mat_index: dict[str, int] = {}
    for key, m in materials.items():
        pbr = PbrMetallicRoughness(metallicFactor=1.0, roughnessFactor=1.0)
        gm = GMat(name=m.name, pbrMetallicRoughness=pbr, doubleSided="two_sided" in m.flags or "alpha_test" in m.flags)
        base = _first(m, "base")
        a = _rgba(source, base.key, tex_size) if base is not None else None
        if a is not None:
            pbr.baseColorTexture = TextureInfo(index=w.texture(a))
            if "translucent" in m.flags:
                gm.alphaMode = "BLEND"
            elif "alpha_test" in m.flags:
                gm.alphaMode, gm.alphaCutoff = "MASK", 0.5
        nrm = _first(m, "normal")
        n = _rgba(source, nrm.key, tex_size, normal=True) if nrm is not None else None
        if n is not None:
            gm.normalTexture = NormalMaterialTexture(index=w.texture(n))
        mr, occ = metal_rough(source, m, tex_size)
        if mr is not None:
            pbr.metallicRoughnessTexture = TextureInfo(index=w.texture(mr))
        else:
            pbr.metallicFactor, pbr.roughnessFactor = 0.0, 0.7
        if occ is not None:
            gm.occlusionTexture = OcclusionTextureInfo(index=w.texture(occ))
        g.materials.append(gm)
        mat_index[key] = len(g.materials) - 1

    rig = model.rig if model.skinned else None
    joints_world = None
    if rig is not None and hasattr(rig, "world"):
        joints_world = rig.world()
    prims = []
    for sm in model.submeshes:
        if len(sm.indices) < 3:
            continue
        attrs = Attributes(POSITION=w.acc(_yup(sm.positions), FLOAT, "VEC3", ARRAY_BUFFER, True),
                           NORMAL=w.acc(_yup(sm.normals), FLOAT, "VEC3", ARRAY_BUFFER),
                           TEXCOORD_0=w.acc(np.asarray(sm.uvs, np.float32), FLOAT, "VEC2", ARRAY_BUFFER))
        if sm.tangents is not None and len(sm.tangents) == len(sm.positions):
            t = np.asarray(sm.tangents, np.float64)
            xyz = _yup(t[:, :3]).astype(np.float64)
            ln = np.linalg.norm(xyz, axis=1, keepdims=True)
            ok = ln[:, 0] > 1e-6
            xyz = np.where(ok[:, None], xyz / np.maximum(ln, 1e-6), [1.0, 0.0, 0.0])
            hand = t[:, 3]
            # Glacier keeps the raw handedness byte (>= 128 positive), Unreal +-1
            sign = np.where(np.abs(hand) > 1.5, np.where(hand >= 128, 1.0, -1.0), np.where(hand < 0, -1.0, 1.0))
            attrs.TANGENT = w.acc(np.concatenate([xyz, sign[:, None]], 1).astype(np.float32), FLOAT, "VEC4", ARRAY_BUFFER)
        if joints_world is not None and sm.joints is not None and sm.weights is not None:
            j = np.zeros((len(sm.joints), 4), np.uint16)
            wt = np.zeros((len(sm.joints), 4), np.float32)
            k = min(4, sm.joints.shape[1])
            j[:, :k] = np.clip(sm.joints[:, :k], 0, len(joints_world) - 1)
            wt[:, :k] = sm.weights[:, :k]
            tot = wt.sum(1, keepdims=True)
            wt = np.where(tot > 0, wt / np.maximum(tot, 1e-6), np.array([1, 0, 0, 0], np.float32)).astype(np.float32)
            j[wt == 0] = 0                                  # unused influences point at the root
            attrs.JOINTS_0 = w.acc(j, UNSIGNED_SHORT, "VEC4", ARRAY_BUFFER)
            attrs.WEIGHTS_0 = w.acc(wt, FLOAT, "VEC4", ARRAY_BUFFER)
        prims.append(Primitive(attributes=attrs, material=mat_index.get(sm.material_key),
                               indices=w.acc(sm.indices.astype(np.uint32), UNSIGNED_INT, "SCALAR", ELEMENT_ARRAY_BUFFER)))
    g.meshes.append(Mesh(name=model.name.rsplit("/", 1)[-1], primitives=prims))
    mesh_node = Node(mesh=0, name=model.name.rsplit("/", 1)[-1])
    roots = []
    if joints_world is not None and any(p.attributes.JOINTS_0 is not None for p in prims):
        C = np.eye(4)
        C[:3, :3] = _ZUP
        Wy = np.array([C @ W @ np.linalg.inv(C) for W in joints_world])          # bone matrices in the Y-up frame
        base = len(g.nodes)
        names = list(getattr(rig, "source_names", None) or rig.names)
        for i, W in enumerate(Wy):
            p = rig.parents[i]
            local = np.linalg.inv(Wy[p]) @ W if p >= 0 else W
            g.nodes.append(Node(name=str(names[i]), matrix=local.T.reshape(-1).tolist()))
        for i, p in enumerate(rig.parents):
            if p >= 0:
                g.nodes[base + p].children = (g.nodes[base + p].children or []) + [base + i]
            else:
                roots.append(base + i)
        ibm = np.array([np.linalg.inv(W).T for W in Wy], np.float32).reshape(-1, 16)
        g.skins.append(Skin(joints=list(range(base, base + len(Wy))), inverseBindMatrices=w.acc(ibm, FLOAT, "MAT4"),
                            skeleton=roots[0] if roots else None))
        mesh_node.skin = 0
    g.nodes.append(mesh_node)
    g.scenes.append(Scene(nodes=roots + [len(g.nodes) - 1]))
    g.scene = 0
    w.save(out)
    return out


def out_dir(source_id: str) -> Path:
    return CONFIG.workspace / "exports" / source_id / "gltf"


def export_models(source, keys: list[str], tex_size: int = 4096, progress=None, cancel=None) -> dict:
    """Export the models ``keys`` of ``source``; returns {written, failed: [(key, error)], folder}."""
    root = out_dir(source.id)
    done, failed = 0, []
    for i, k in enumerate(keys):
        if cancel is not None and cancel.is_set():
            break
        try:
            model, mats = source.load_model(int(k, 16))
            write_glb(source, model, mats, root / f"{model.name}.glb", tex_size)
            done += 1
        except Exception as e:  # noqa: BLE001 - one model never stops the export
            failed.append((k, f"{type(e).__name__}: {e}"))
        if progress:
            progress(i + 1, len(keys))
    return {"written": done, "failed": failed, "folder": str(root)}
