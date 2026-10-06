"""Build one GMod player model from several skinned character parts."""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.config import CONFIG, Config
from ...core.naming import slug
from . import playermodel as pm
from .compile import compile_qc
from .materials import Options, TextureCache, convert_material


@dataclass
class PMOptions:
    name: str = "character"
    title: str = ""
    template: str = "male"             # male | female (decides the skeleton and the animation set)
    lod: int = 2                       # default LOD level of each part
    max_tris: int = 60000              # if the outfit is heavier, a lower LOD is chosen automatically
    mat: Options = field(default_factory=Options)


@dataclass
class PMResult:
    name: str
    status: str = "FAILED"
    model: str = ""
    triangles: int = 0
    lod: int = 0
    parts: list = field(default_factory=list)
    materials: int = 0
    seconds: float = 0.0
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)


_VISIBLE = {"base", "normal", "srm", "spec", "emissive", "orm"}   # a material with none of these is a simulation proxy


def _find_part(source, spec: str) -> int:
    """spec = 16-hex hash, or 'container/leaf' tokens ('kit_magnolia_tux/pants_kit_magnolia'):
    every token must appear in the game path, the last one preferably as the exact part name."""
    s = spec.strip()
    if len(s) == 16:
        try:
            return int(s, 16)
        except ValueError:
            pass
    tokens = [x for x in s.lower().split("/") if x]
    leaf = tokens[-1]
    hits = []
    for h in source.archive.index("PRIM"):
        n = source.names.name(h).lower()
        if n and ".weighted" in n and all(tok in n for tok in tokens):
            exact = f"/{leaf}.weighted" in n or f"?/{leaf}.weighted" in n
            hits.append((0 if exact else 1, len(n), h))
    if not hits:
        raise ValueError(f"no skinned part matches '{spec}'")
    return sorted(hits)[0][2]


def _prepare(source, part_specs: list[str], opts: PMOptions, cfg: Config, res: PMResult, addon: Path | None = None):
    """Load the parts, pose their own skeleton onto ValveBiped and convert their materials (into ``addon``, the
    source's addon by default). None when nothing can be built (the reason is in ``res.errors``)."""
    tpl = pm.load_template(opts.template)
    part_ids = [_find_part(source, s) for s in part_specs]
    res.parts = ["%016X" % h for h in part_ids]

    # pick the LOD: the requested one, or lower detail until the outfit fits the triangle budget
    lod = opts.lod
    while True:
        loaded = [source.load_model(h, lod) for h in part_ids]
        tris = sum(len(sm.indices) // 3 for m, _ in loaded for sm in m.submeshes)
        if tris <= opts.max_tris or lod >= 4:
            break
        lod += 1
    res.lod = lod
    if lod != opts.lod:
        res.notes.append(f"LOD raised to {lod} to fit {opts.max_tris} triangles")

    # the character's own skeleton (all parts share it), posed onto the ValveBiped reference pose
    sk = pm.merge_rigs([m.rig for m, _ in loaded if m.rig is not None])
    if not sk.names:
        res.errors.append("no skinned part (no skeleton)")
        return None
    P, src_of = pm.fit_pose(sk, tpl)
    S = pm.proportions(tpl, sk, P, src_of)
    targets = pm.weight_targets(sk, tpl)
    posed, materials = [], {}
    for model, mats in loaded:
        for sm in model.submeshes:
            if sm.zbias > 0 and sm.material_key in mats and "decal" in (mats[sm.material_key].source_name or "").lower():
                continue                    # dirt/decal layers make no sense on a player model
            posed.append(pm.skin_submesh(sm, model.rig, sk, P, S, targets, tpl))
        if model.rig is None:
            res.notes.append(f"{model.name}: rigid part, attached to the pelvis")
        materials.update(mats)
    if not posed:
        res.errors.append("no geometry")
        return None

    # materials -> VMT/VTF (same converter as props; skin and cloth are plain VertexLitGeneric)
    addon = addon or cfg.addon_dir(source.id)
    cd = f"omni/{source.id}"
    cache = TextureCache(source)
    names, dropped = {}, set()
    for mk, mat in materials.items():
        r = convert_material(mat, cache, addon / "materials", cd, opts.mat)
        names[mk] = mat.name
        if r.drop:
            dropped.add(mk)
    # texture-less materials are simulation/collision proxies (cloth pockets, linings...), never meant to be seen
    dropped |= {mk for mk, mat in materials.items() if not any(x.role in _VISIBLE for x in mat.textures)}
    posed = [p for p in posed if p.material_key not in dropped]
    res.materials = len(names)
    return {"tpl": tpl, "sk": sk, "P": P, "S": S, "posed": posed, "names": names, "materials": materials,
            "addon": addon, "cd": cd}


def build_playermodel(source, part_specs: list[str], opts: PMOptions, cfg: Config = CONFIG) -> PMResult:
    t0 = time.perf_counter()
    res = PMResult(name=opts.name)
    try:
        prep = _prepare(source, part_specs, opts, cfg, res)
        if prep is None:
            return res
        tpl, sk, P, S, posed, names = prep["tpl"], prep["sk"], prep["P"], prep["S"], prep["posed"], prep["names"]
        addon, cd = prep["addon"], prep["cd"]
        slug_name = slug(opts.name, 40)
        mpath = f"omni/{source.id}/pm/{slug_name}"
        work = cfg.sandbox / "modelsrc" / mpath
        work.mkdir(parents=True, exist_ok=True)
        res.triangles = pm.write_reference(work / "ref.smd", tpl, S, posed, names)
        pm.write_skeleton(work / "reference_male.smd", tpl)           # stock reference pose
        pm.write_skeleton(work / "proportions.smd", tpl, S)           # same bones, character proportions
        volumes = pm.write_physics(work / "phys.smd", tpl, S, posed)

        info = tpl.info
        head = tpl.index("ValveBiped.Bip01_Head1")
        eyes = [P[sk.index[e], :3, 3] for e in ("L_eye", "R_eye") if e in sk.index]
        overrides = {}
        eye_pos = np.array(info.eye_position)
        if eyes:
            eye_pos = np.mean(eyes, 0)
            overrides["eyes"] = (np.linalg.inv(S[head]) @ np.r_[eye_pos, 1.0])[:3]
        qc = [f'$modelname "{mpath}.mdl"', '$body "body" "ref.smd"', '$surfaceprop "flesh"',
              f'$cdmaterials "{cd}/"',
              "$eyeposition %.3f %.3f %.3f" % tuple(eye_pos),
              "$illumposition %.3f %.3f %.3f" % info.illum_position,
              ]
        qc += pm.hitbox_qc(tpl, S, posed) + pm.attachment_qc(tpl, overrides) + pm.bonemerge_qc(tpl)
        # proportion trick: delta (character skeleton - stock reference) added before every animation
        qc += ['$sequence "reference" "reference_male.smd" fps 1',
               '$animation "a_proportions" "proportions.smd" subtract "reference" 0',
               '$sequence "proportions" "a_proportions" predelta autoplay']
        qc += pm.ik_qc(tpl)
        qc += [f'$includemodel "{Path(tpl.anim_include).name}"']
        qc += pm.collision_qc(tpl, volumes)
        (work / "model.qc").write_text("\n".join(qc) + "\n")

        cr = compile_qc(work / "model.qc", cfg.sandbox, cfg.studiomdl, timeout=180)
        if not cr.ok:
            res.errors += cr.errors[:8] or [cr.log[-600:]]
            return res
        outdir = cfg.sandbox / "models" / Path(mpath).parent
        dest = addon / "models" / Path(mpath).parent
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in outdir.glob(Path(mpath).name + ".*"):
            shutil.copy2(f, dest / f.name)
            n += 1
        if not n:
            res.errors.append("compiler produced no files")
            return res
        res.model = f"models/{mpath}.mdl"
        res.status = "OK"
        res.notes += cr.warnings[:5]
        register(addon, opts.title or opts.name, res.model)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"{type(e).__name__}: {e}")
    finally:
        res.seconds = round(time.perf_counter() - t0, 2)
    return res


def register(addon: Path, title: str, model: str) -> None:
    """Keep a registry of converted player models and (re)generate the Lua that exposes them in GMod."""
    from ...core.filelock import file_lock
    reg = addon / "pm_registry.json"
    addon.mkdir(parents=True, exist_ok=True)
    with file_lock(addon / "pm_registry.lock"):         # parallel batch workers register at the same time
        try:
            items = json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {}
        except ValueError:
            items = {}
        items[model] = title
        tmp = reg.with_name(f"pm_registry.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, reg)
        _write_lua(addon, items)


def _write_lua(addon: Path, items: dict) -> None:
    lines = ["-- generated by omni: registers the converted player models", "local models = {"]
    for m, t in sorted(items.items()):
        lines.append('\t{ name = %s, model = %s },' % (json.dumps(t), json.dumps(m)))
    lines += ["}", "for _, m in ipairs(models) do",
              "\tplayer_manager.AddValidModel(m.name, m.model)",
              '\tplayer_manager.AddValidHands(m.name, "models/weapons/c_arms_citizen.mdl", 0, "00000000")',
              "\tlist.Set(\"PlayerOptionsModel\", m.name, m.model)", "end"]
    lua = addon / "lua" / "autorun" / "omni_playermodels.lua"
    lua.parent.mkdir(parents=True, exist_ok=True)
    lua.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_character_preview(source, part_specs: list[str], opts: PMOptions, cfg: Config, out_glb: Path,
                             tex_size: int = 512) -> dict:
    """GLB of the posed character (materials converted into ``cfg``'s scratch addon) and the metadata the
    character page shows (one base body, no bodygroups, one skin)."""
    import re as _re
    from ...preview.glb import export_scene
    from .vtfread import read_vtf
    res = PMResult(name=opts.name)
    prep = _prepare(source, part_specs, opts, cfg, res)
    if prep is None:
        raise RuntimeError(res.errors[0] if res.errors else "nothing to preview")
    mroot = prep["addon"] / "materials"
    materials, index, nodes = [], {}, []
    for i, pp in enumerate(prep["posed"]):
        vmt_name = prep["names"].get(pp.material_key, "")
        if vmt_name not in index:
            vmt = mroot / prep["cd"] / f"{vmt_name}.vmt"
            text = vmt.read_text() if vmt.exists() else ""
            entry = {"name": vmt_name, "base": None, "normal": None,
                     "alpha": "MASK" if "$alphatest" in text else ("BLEND" if "$translucent" in text else "OPAQUE")}
            for key, slot in (("$basetexture", "base"), ("$bumpmap", "normal")):
                m = _re.search(r'"%s"\s+"([^"]+)"' % _re.escape(key), text)
                if m and (mroot / (m.group(1) + ".vtf")).exists():
                    entry[slot] = mroot / (m.group(1) + ".vtf")
            materials.append(entry)
            index[vmt_name] = len(materials) - 1
        nodes.append({"name": f"base|{pp.material_key}|{i}", "positions": pp.positions / pm.UNITS_PER_METER,
                      "normals": pp.normals, "uvs": pp.uvs, "indices": pp.indices, "material": index[vmt_name]})
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    export_scene(nodes, materials, out_glb, tex_size, read_vtf)
    mats = []
    for mk, mat in prep["materials"].items():
        mats.append({"name": prep["names"].get(mk, mat.name), "key": mk, "source": mat.source_name,
                     "textures": [{"role": t.role, "slot": t.slot, "key": t.key} for t in mat.textures]})
    meta = {"model": f"models/omni/{source.id}/pm/{slug(opts.name, 40)}.mdl", "family": opts.name, "lod": 0,
            "groups": [], "base": [opts.name], "skins": 1,
            "presets": [{"name": opts.name, "variant": 0, "skin": 0, "bodygroups": {}}],
            "materials": mats, "skin_materials": [{}]}
    out_glb.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
