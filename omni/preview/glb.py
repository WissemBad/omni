"""Export a neutral Model (+Materials) to a binary glTF for the web preview and the optional Blender step.

Material names are exactly the VMT names, so the .blend built from this file keeps the 1:1 link
with the Source materials.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
from pygltflib import (ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, FLOAT, GLTF2, UNSIGNED_INT, Accessor, Asset, Attributes,
                       Buffer, BufferView, Image as GImage, Material as GMat, Mesh, Node, NormalMaterialTexture,
                       PbrMetallicRoughness, Primitive, Sampler, Scene, Texture, TextureInfo)

from ..core.ir import Material, Model
from ..sources.glacier.texture import Mip, to_rgba


def _png(rgba: np.ndarray, size: int) -> bytes:
    im = Image.fromarray(rgba)
    if max(im.size) > size:
        im.thumbnail((size, size))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _tex_rgba(source, key: str, size: int, normal: bool):
    t = source.load_texture(int(key, 16))
    if t is None:
        return None
    w, h, d = max(t.mips, key=lambda m: m[0] * m[1])
    # smaller mip is enough for a preview
    cands = [m for m in t.mips if max(m[0], m[1]) <= size] or [t.mips[-1]]
    w, h, d = cands[0]
    a = to_rgba(t.fmt, Mip(w, h, d))
    if normal and t.fmt in ("BC5", "BC4", "RG8"):
        from ..targets.source.textures import rebuild_normal
        a = rebuild_normal(a)
    return a


def export_glb(source, model: Model, materials: dict[str, Material], out: Path, tex_size: int = 1024,
               embed_textures: bool = True, overrides: dict | None = None) -> None:
    """``overrides``: {material key: {'base': rgba, 'normal': rgba}} replaces the textures read from the game
    (used to preview what the Source export really produced)."""
    g = GLTF2(asset=Asset(version="2.0"))
    blob = bytearray()

    def add_view(data: bytes, target=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        g.bufferViews.append(BufferView(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        return len(g.bufferViews) - 1

    def add_acc(arr: np.ndarray, ctype, typ, target, minmax=False) -> int:
        v = add_view(arr.tobytes(), target)
        acc = Accessor(bufferView=v, componentType=ctype, count=len(arr), type=typ)
        if minmax:
            acc.min, acc.max = arr.min(0).tolist(), arr.max(0).tolist()
        g.accessors.append(acc)
        return len(g.accessors) - 1

    g.samplers.append(Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))
    mat_index: dict[str, int] = {}

    def image_index(png: bytes) -> int:
        v = add_view(png)
        g.images.append(GImage(bufferView=v, mimeType="image/png"))
        g.textures.append(Texture(source=len(g.images) - 1, sampler=0))
        return len(g.textures) - 1

    for key, m in materials.items():
        pbr = PbrMetallicRoughness(metallicFactor=0.0, roughnessFactor=0.7)
        gm = GMat(name=m.name, pbrMetallicRoughness=pbr, doubleSided=True)
        ov = (overrides or {}).get(key)
        if ov and embed_textures:
            if "base" in ov:
                pbr.baseColorTexture = TextureInfo(index=image_index(_png(ov["base"], tex_size)))
                if ov["base"][..., 3].min() < 200:
                    gm.alphaMode, gm.alphaCutoff = "MASK", 0.5
            if "normal" in ov:
                gm.normalTexture = NormalMaterialTexture(index=image_index(_png(ov["normal"], tex_size)))
        for ref in (m.textures if embed_textures and not ov else []):
            if ref.role == "base" and pbr.baseColorTexture is None:
                a = _tex_rgba(source, ref.key, tex_size, False)
                if a is not None:
                    a = a.copy()
                    a[..., 3] = 255
                    pbr.baseColorTexture = TextureInfo(index=image_index(_png(a, tex_size)))
            elif ref.role == "normal" and gm.normalTexture is None:
                a = _tex_rgba(source, ref.key, tex_size, True)
                if a is not None:
                    gm.normalTexture = NormalMaterialTexture(index=image_index(_png(a, tex_size)))
        g.materials.append(gm)
        mat_index[key] = len(g.materials) - 1

    def zup_to_yup(v: np.ndarray) -> np.ndarray:
        return np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1).astype(np.float32)

    prims = []
    for sm in model.submeshes:
        attrs = Attributes(
            POSITION=add_acc(zup_to_yup(sm.positions), FLOAT, "VEC3", ARRAY_BUFFER, True),
            NORMAL=add_acc(zup_to_yup(sm.normals), FLOAT, "VEC3", ARRAY_BUFFER),
            TEXCOORD_0=add_acc(sm.uvs.astype(np.float32), FLOAT, "VEC2", ARRAY_BUFFER),
        )
        idx = sm.indices.astype(np.uint32)
        prims.append(Primitive(attributes=attrs, indices=add_acc(idx, UNSIGNED_INT, "SCALAR", ELEMENT_ARRAY_BUFFER),
                               material=mat_index.get(sm.material_key)))
    g.meshes.append(Mesh(name=model.name, primitives=prims))
    g.nodes.append(Node(mesh=0, name=model.name))
    g.scenes.append(Scene(nodes=[0]))
    g.scene = 0
    g.buffers.append(Buffer(byteLength=len(blob)))
    g.set_binary_blob(bytes(blob))
    out.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out))


def export_scene(nodes: list[dict], materials: list[dict], out: Path, tex_size: int, read_texture) -> None:
    """Generic scene export for the previews that need named nodes (bodygroup options) and materials that no
    node uses yet (other skins).  ``nodes``: {name, positions (Z-up metres), normals, uvs, indices, material}.
    ``materials``: {name, base: path|None, normal: path|None, alpha: OPAQUE|MASK|BLEND}; ``read_texture(path,
    max_size)`` returns RGBA. Textures shared by several materials are stored once."""
    g = GLTF2(asset=Asset(version="2.0"))
    blob = bytearray()

    def add_view(data: bytes, target=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        g.bufferViews.append(BufferView(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        return len(g.bufferViews) - 1

    def add_acc(arr: np.ndarray, ctype, typ, target, minmax=False) -> int:
        v = add_view(arr.tobytes(), target)
        acc = Accessor(bufferView=v, componentType=ctype, count=len(arr), type=typ)
        if minmax:
            acc.min, acc.max = arr.min(0).tolist(), arr.max(0).tolist()
        g.accessors.append(acc)
        return len(g.accessors) - 1

    g.samplers.append(Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))
    tex_of: dict[tuple, int] = {}

    def texture(path, normal: bool, keep_alpha: bool) -> int | None:
        key = (str(path), normal, keep_alpha)
        if key in tex_of:
            return tex_of[key]
        try:
            a = read_texture(path, tex_size)
        except Exception:  # noqa: BLE001
            return None
        im = Image.fromarray(a if keep_alpha else a[..., :3].copy())
        b = io.BytesIO()
        if keep_alpha:
            im.save(b, "PNG")
            mime = "image/png"
        else:
            im.save(b, "JPEG", quality=88)
            mime = "image/jpeg"
        v = add_view(b.getvalue())
        g.images.append(GImage(bufferView=v, mimeType=mime))
        g.textures.append(Texture(source=len(g.images) - 1, sampler=0))
        tex_of[key] = len(g.textures) - 1
        return tex_of[key]

    for m in materials:
        pbr = PbrMetallicRoughness(metallicFactor=0.0, roughnessFactor=0.7)
        gm = GMat(name=m["name"], pbrMetallicRoughness=pbr, doubleSided=True)
        if m.get("base"):
            t = texture(m["base"], False, m["alpha"] != "OPAQUE")
            if t is not None:
                pbr.baseColorTexture = TextureInfo(index=t)
        if m.get("normal"):
            t = texture(m["normal"], True, False)
            if t is not None:
                gm.normalTexture = NormalMaterialTexture(index=t)
        if m["alpha"] == "MASK":
            gm.alphaMode, gm.alphaCutoff = "MASK", 0.5
        elif m["alpha"] == "BLEND":
            gm.alphaMode = "BLEND"
        g.materials.append(gm)

    def zup_to_yup(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, np.float32)
        return np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1).astype(np.float32)

    for n in nodes:
        attrs = Attributes(POSITION=add_acc(zup_to_yup(n["positions"]), FLOAT, "VEC3", ARRAY_BUFFER, True),
                           NORMAL=add_acc(zup_to_yup(n["normals"]), FLOAT, "VEC3", ARRAY_BUFFER),
                           TEXCOORD_0=add_acc(np.asarray(n["uvs"], np.float32), FLOAT, "VEC2", ARRAY_BUFFER))
        prim = Primitive(attributes=attrs, indices=add_acc(np.asarray(n["indices"], np.uint32), UNSIGNED_INT, "SCALAR",
                                                           ELEMENT_ARRAY_BUFFER), material=n["material"])
        g.meshes.append(Mesh(name=n["name"], primitives=[prim]))
        g.nodes.append(Node(mesh=len(g.meshes) - 1, name=n["name"]))
    g.scenes.append(Scene(nodes=list(range(len(g.nodes)))))
    g.scene = 0
    g.buffers.append(Buffer(byteLength=len(blob)))
    g.set_binary_blob(bytes(blob))
    out.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out))
