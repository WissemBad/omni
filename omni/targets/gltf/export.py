"""glTF 2.0 target (.glb): any source's models for Blender and other tools, next to the Garry's Mod export.

One self-contained .glb per model: geometry (Y up, metres), PBR metal/roughness materials (base colour, normal,
metallic-roughness, occlusion and emissive rebuilt from the game's own maps, read like every other target does:
see ``targets/shading.py``), alpha mode from the material flags, and for skinned models the skeleton and skin weights (bind
pose). The file itself is written by the Rust core (``N.Glb``); models are exported by worker processes. Written to ``<exports>/<source>/gltf/<model path>.glb``.
Game-independent: it only reads the IR.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ...core.config import CONFIG
from ...core.ir import Material, Model
from ...native import N
from .. import shading as sh


def metal_rough(surf, shape=None):
    """(metallic-roughness RGBA (G rough, B metal), occlusion RGBA or None) from the shared surface terms."""
    if surf is None:
        return None, None
    rough = np.clip(surf.rough, 0.0, 1.0)
    mr = np.zeros(rough.shape + (4,), np.uint8)
    mr[..., 1] = np.clip(rough * 255.0 + 0.5, 0, 255)
    mr[..., 2] = np.clip(surf.metal * 255.0 + 0.5, 0, 255)
    mr[..., 3] = 255
    occ = None
    if surf.ao is not None:
        occ = np.repeat(np.clip(surf.ao * 255.0 + 0.5, 0, 255).astype(np.uint8)[..., None], 4, -1)
        occ[..., 3] = 255
    return mr, occ


def write_glb(source, model: Model, materials: dict[str, Material], out: Path, tex_size: int = 4096) -> Path:
    g = N.Glb("facing_z")
    cache = sh.TextureCache(source)
    index: dict[str, int] = {}
    dropped: set[str] = set()
    for key, m in materials.items():
        lk = sh.look(m, cache)
        if lk.drop:                          # opaque decal layer: it would paint a solid patch over the surface
            dropped.add(key)
            continue
        alpha = "BLEND" if lk.translucent else "MASK" if lk.alpha_test else "OPAQUE"
        a = lk.albedo(cache, tex_size)
        base = g.texture(a) if a is not None else None
        if base is None:
            alpha = "OPAQUE"
        nref = sh.first(m, "normal")
        n = sh.normal(cache.get(nref.key), tex_size) if nref is not None else None
        normal = g.texture(n) if n is not None else None
        surf = sh.surface(m, cache, tex_size, lk)
        mr, occ = metal_rough(surf)
        e = sh.emissive(m, cache, tex_size)
        index[key] = g.material(m.name, base=base, normal=normal,
                                metal_rough=g.texture(mr) if mr is not None else None,
                                occlusion=g.texture(occ) if occ is not None else None,
                                emissive=g.texture(e) if e is not None else None,
                                alpha=alpha, double_sided="two_sided" in m.flags or lk.alpha_test,
                                metallic=1.0 if mr is not None else 0.0, roughness=1.0 if mr is not None else 0.7)
    rig = model.rig if model.skinned else None
    world = rig.world() if rig is not None and hasattr(rig, "world") else None
    skin = None
    if world is not None:
        names = [str(x) for x in (getattr(rig, "source_names", None) or rig.names)]
        skin = g.skeleton(names, [int(p) for p in rig.parents], np.ascontiguousarray(world, np.float64))
    prims = []
    for sm in model.submeshes:
        if len(sm.indices) < 3 or sm.material_key in dropped:
            continue
        p = {"positions": np.asarray(sm.positions, np.float32), "normals": np.asarray(sm.normals, np.float32),
             "uvs": np.asarray(sm.uvs, np.float32), "indices": np.ascontiguousarray(sm.indices, np.uint32),
             "material": index.get(sm.material_key)}
        if sm.tangents is not None and len(sm.tangents) == len(sm.positions):
            p["tangents"] = np.asarray(sm.tangents, np.float32)
        if skin is not None and sm.joints is not None and sm.weights is not None:
            k = min(4, sm.joints.shape[1])
            j = np.zeros((len(sm.joints), 4), np.uint16)
            w = np.zeros((len(sm.joints), 4), np.float32)
            j[:, :k] = sm.joints[:, :k]
            w[:, :k] = sm.weights[:, :k]
            p["joints"], p["weights"] = j, w
        prims.append(p)
    short = model.name.rsplit("/", 1)[-1]
    g.node(short, g.mesh(short, prims), skin)
    g.save(out)
    return out


def out_dir(source_id: str) -> Path:
    return CONFIG.gltf_dir(source_id)


def export_one(source, key: str, tex_size: int = 4096) -> str:
    """Write the .glb of one model; returns the model path."""
    model, mats = source.load_model(int(key, 16))
    write_glb(source, model, mats, out_dir(source.id) / f"{model.name}.glb", tex_size)
    return model.name


def export_models(source, keys: list[str], tex_size: int = 4096, progress=None, cancel=None, workers: int = 0) -> dict:
    """Export the models ``keys`` of ``source`` in worker processes; returns {written, failed: [(key, error)],
    folder}. One model never stops the export; ``progress(done, total)`` follows the results."""
    from ...pipeline import clamp_workers, run_gltf_batch
    failed: list[tuple[str, str]] = []
    done = 0

    def on_result(r: dict) -> None:
        nonlocal done
        done += 1
        if r["status"] != "OK":
            failed.append((r["key"], "; ".join(r.get("errors") or ["failed"])))
        if progress:
            progress(done, len(keys))
    stats = run_gltf_batch(source.id, keys, clamp_workers(workers or (os.cpu_count() or 4) // 2), tex_size, on_result, cancel)
    return {"written": stats.get("OK", 0), "failed": failed, "folder": str(out_dir(source.id)),
            "cancelled": bool(stats.get("cancelled"))}
