"""Binary glTF for the web previews and the optional Blender step, written by the Rust core (``N.Glb``).

Material names are exactly the VMT names, so the .blend built from this file keeps the 1:1 link with the Source
materials. Geometry uses the "viewer" frame (the one the web viewer was built on); the shareable export of
``targets/gltf`` uses the glTF convention instead.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core.ir import Material, Model
from ..native import N
from ..sources.glacier.texture import Mip, to_rgba


def _tex_rgba(source, key: str, size: int, normal: bool):
    t = source.load_texture(int(key, 16))
    if t is None or not t.mips:
        return None
    # a smaller mip is enough for a preview
    w, h, d = next((m for m in t.mips if max(m[0], m[1]) <= size), t.mips[-1])
    a = to_rgba(t.fmt, Mip(w, h, d))
    if normal and t.fmt in ("BC5", "BC4", "RG8"):
        from ..targets.source.textures import rebuild_normal
        a = rebuild_normal(a)
    return a


def export_glb(source, model: Model, materials: dict[str, Material], out: Path, tex_size: int = 1024,
               embed_textures: bool = True, overrides: dict | None = None) -> None:
    """``overrides``: {material key: {'base': rgba, 'normal': rgba}} replaces the textures read from the game
    (used to preview what the Source export really produced)."""
    g = N.Glb("viewer")
    index: dict[str, int] = {}
    for key, m in materials.items():
        base = normal = None
        alpha = "OPAQUE"
        ov = (overrides or {}).get(key)
        if ov and embed_textures:
            if "base" in ov:
                base = g.texture(ov["base"], tex_size)
                if ov["base"][..., 3].min() < 200:
                    alpha = "MASK"
            if "normal" in ov:
                normal = g.texture(ov["normal"], tex_size)
        elif embed_textures:
            for ref in m.textures:
                if ref.role == "base" and base is None:
                    a = _tex_rgba(source, ref.key, tex_size, False)
                    if a is not None:
                        a = a.copy()
                        a[..., 3] = 255
                        base = g.texture(a, tex_size)
                elif ref.role == "normal" and normal is None:
                    a = _tex_rgba(source, ref.key, tex_size, True)
                    if a is not None:
                        normal = g.texture(a, tex_size)
        index[key] = g.material(m.name, base=base, normal=normal, alpha=alpha, metallic=0.0, roughness=0.7)
    prims = [{"positions": sm.positions.astype(np.float32), "normals": sm.normals.astype(np.float32),
              "uvs": sm.uvs.astype(np.float32), "indices": np.ascontiguousarray(sm.indices, np.uint32),
              "material": index.get(sm.material_key)} for sm in model.submeshes if len(sm.indices) >= 3]
    name = model.name
    g.node(name, g.mesh(name, prims))
    g.save(out)


def export_scene(nodes: list[dict], materials: list[dict], out: Path, tex_size: int, read_texture) -> None:
    """Generic scene export for the previews that need named nodes (bodygroup options) and materials that no
    node uses yet (other skins).  ``nodes``: {name, positions (Z-up metres), normals, uvs, indices, material}.
    ``materials``: {name, base: path|None, normal: path|None, alpha: OPAQUE|MASK|BLEND}; ``read_texture(path,
    max_size)`` returns RGBA. Textures shared by several materials are stored once; opaque colour maps are JPEG."""
    g = N.Glb("viewer")
    cache: dict[tuple, int | None] = {}

    def texture(path, keep_alpha: bool) -> int | None:
        k = (str(path), keep_alpha)
        if k not in cache:
            try:
                a = read_texture(path, tex_size)
                cache[k] = g.texture(a, tex_size, 0 if keep_alpha else 88)
            except Exception:  # noqa: BLE001 - a missing texture leaves the material plain
                cache[k] = None
        return cache[k]

    for m in materials:
        base = texture(m["base"], m["alpha"] != "OPAQUE") if m.get("base") else None
        normal = texture(m["normal"], False) if m.get("normal") else None
        g.material(m["name"], base=base, normal=normal, alpha=m["alpha"], metallic=0.0, roughness=0.7)
    for n in nodes:
        prim = {"positions": np.asarray(n["positions"], np.float32), "normals": np.asarray(n["normals"], np.float32),
                "uvs": np.asarray(n["uvs"], np.float32), "indices": np.ascontiguousarray(n["indices"], np.uint32),
                "material": n["material"]}
        g.node(n["name"], g.mesh(n["name"], [prim]))
    g.save(out)
