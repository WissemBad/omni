"""glTF 2.0 target (.glb): any source's models for Blender and other tools, next to the Garry's Mod export.

One self-contained .glb per model: geometry (Y up, metres), PBR metal/roughness materials (base colour, normal,
metallic-roughness and occlusion rebuilt from the game's own maps: Unreal ORM, Glacier SRM, separate roughness /
metallic maps), alpha mode from the material flags, and for skinned models the skeleton and skin weights (bind
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


def write_glb(source, model: Model, materials: dict[str, Material], out: Path, tex_size: int = 4096) -> Path:
    g = N.Glb("facing_z")
    index: dict[str, int] = {}
    for key, m in materials.items():
        base = normal = metal_rough_tex = occlusion = None
        alpha = "BLEND" if "translucent" in m.flags else "MASK" if "alpha_test" in m.flags else "OPAQUE"
        ref = _first(m, "base")
        a = _rgba(source, ref.key, tex_size) if ref is not None else None
        if a is not None:
            base = g.texture(a)
        else:
            alpha = "OPAQUE"
        ref = _first(m, "normal")
        n = _rgba(source, ref.key, tex_size, normal=True) if ref is not None else None
        if n is not None:
            normal = g.texture(n)
        mr, occ = metal_rough(source, m, tex_size)
        if mr is not None:
            metal_rough_tex = g.texture(mr)
        if occ is not None:
            occlusion = g.texture(occ)
        index[key] = g.material(m.name, base=base, normal=normal, metal_rough=metal_rough_tex, occlusion=occlusion,
                                alpha=alpha, double_sided="two_sided" in m.flags or "alpha_test" in m.flags,
                                metallic=1.0 if mr is not None else 0.0, roughness=1.0 if mr is not None else 0.7)
    rig = model.rig if model.skinned else None
    world = rig.world() if rig is not None and hasattr(rig, "world") else None
    skin = None
    if world is not None:
        names = [str(x) for x in (getattr(rig, "source_names", None) or rig.names)]
        skin = g.skeleton(names, [int(p) for p in rig.parents], np.ascontiguousarray(world, np.float64))
    prims = []
    for sm in model.submeshes:
        if len(sm.indices) < 3:
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
