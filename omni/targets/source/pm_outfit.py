"""Player models from game outfits: one GMod model per outfit family.

The variations of an outfit family (``outfit_<name>_v0``, ``_v1`` ...) become:

* bodygroups - every mesh worn by at least one variation is a bodygroup option. Meshes are sorted by body
  region (head, hair, headwear, vest, torso, back, arms, hands, legs, feet, accessories); in a region,
  meshes that are never worn together are alternatives of one bodygroup, meshes worn together (shirt under
  a jacket) get separate bodygroups. A bodygroup gets a "none" option when some variation has nothing there.
  Meshes worn by every variation go in the always-visible body.
* skins - one per distinct set of colours/materials. Each material slot of each mesh is its own Source
  material (skin tables remap materials by name), and every skin family maps it to the material instance
  and parameter values of that variation (game material overwrites + outfit colours, see outfit.py).
* presets - the bodygroup values + skin reproducing each game variation, saved next to the model
  (``<name>.json``) and used by the previewer.

``prepare`` does everything up to the converted materials and posed meshes; ``compile_plan`` writes the
SMDs/QC and runs studiomdl; ``export_preview`` writes the GLB + JSON shown by the UI from the same plan,
so the preview is exactly what GMod gets.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.config import CONFIG, Config
from ...core.naming import slug
from . import playermodel as pm
from .compile import compile_qc
from .materials import TextureCache, convert_material
from .pm_build import PMResult, register, _VISIBLE

REGIONS = [
    ("Head", r"(^|_)head(_|$)"),
    ("Hair", r"hair|beard|moustache|mustache|eyebrow"),
    ("Headwear", r"beanie|(^|_)cap(_|$)|(^|_)hat(_|$)|helmet|balaclava|mask|(^|_)hood(_|$)|headset|headwear|earpiece|"
                 r"bandana|beret|glasses|goggles|headband"),
    ("Vest", r"vest|chestrig|harness|body_?armou?r|armou?r|plate_carrier"),
    ("Torso", r"shirt|tanktop|(^|_)top(_|$)|sweater|hoodie|jacket|coat|parka|suit|dress|blouse|raglan|polo|uniform|"
              r"kimono|labcoat|waistcoat|jumpsuit|flightsuit|apron|turtleneck|torso|cardigan|blazer"),
    ("Back", r"backpack|bckpck|(^|_)bags?(_|$)|(^|_)pack(_|$)|quiver"),
    ("Arms", r"(^|_)arms?(_|$)|forearm|bicep"),
    ("Hands", r"glove|(^|_)hands?(_|$)|nails"),
    ("Legs", r"pants|jeans|shorts|skirt|trousers|(^|_)legs?(_|$)|sweatpants|tights|stockings|leggings"),
    ("Feet", r"boot|shoe|sneaker|trainer|heel|sandal|loafer|clog|flipflop|(^|_)feet(_|$)|sock"),
    ("Accessories", r""),
]
MAX_SKINS = 32                       # studiomdl MAXSTUDIOSKINS
MAX_MATERIALS = 128                  # studiomdl: "Too many materials used, max 128"


def region_of(label: str) -> int:
    low = label.lower()
    for i, (_name, rx) in enumerate(REGIONS):
        if re.search(rx, low):
            return i
    return len(REGIONS) - 1


@dataclass
class Piece:
    prim: int
    label: str
    region: int
    wearers: set = field(default_factory=set)           # indices of the outfits wearing it
    slots: dict = field(default_factory=dict)           # outfit index -> {source MATI: Slot}


@dataclass
class Group:
    name: str
    region: int
    members: list                                       # prims
    blank: bool = False


def plan_groups(outfits) -> tuple[list[Piece], list[int], list[Group]]:
    """-> (pieces, always-worn prims, bodygroups)."""
    pieces: dict[int, Piece] = {}
    for k, o in enumerate(outfits):
        for part in o.parts:
            p = pieces.setdefault(part.prim, Piece(part.prim, part.label, region_of(part.label)))
            p.wearers.add(k)
            p.slots.setdefault(k, part.slots)
    n = len(outfits)
    base = [p.prim for p in pieces.values() if len(p.wearers) == n]
    rest = sorted((p for p in pieces.values() if len(p.wearers) < n),
                  key=lambda p: (p.region, 0 not in p.wearers, -len(p.wearers), p.label))
    groups: list[Group] = []
    for p in rest:
        for g in groups:
            if g.region == p.region and not any(pieces[m].wearers & p.wearers for m in g.members):
                g.members.append(p.prim)
                break
        else:
            groups.append(Group(REGIONS[p.region][0], p.region, [p.prim]))
    seen: dict[str, int] = {}
    for g in groups:
        seen[g.name] = seen.get(g.name, 0) + 1
        if seen[g.name] > 1:
            g.name = f"{g.name} {seen[g.name]}"
        worn = set().union(*(pieces[m].wearers for m in g.members))
        g.blank = len(worn) < n
        # option 0 must be what the first variation wears (GMod starts every bodygroup at 0)
        g.members.sort(key=lambda m: (0 not in pieces[m].wearers, -len(pieces[m].wearers)))
    return list(pieces.values()), base, groups


def _short(text: str, n: int) -> str:
    return slug(text, n).strip("_") or "x"


@dataclass
class Plan:
    source: object
    cfg: Config
    name: str
    outfits: list
    tpl: object
    pieces: list
    by_prim: dict
    base: list
    groups: list
    lod: int
    sk: object
    P: np.ndarray
    S: np.ndarray
    posed: dict                         # prim -> [PosedPart] (material_key = column material)
    columns: dict                       # (prim, source MATI) -> column material name
    skins: list                         # [ {column: vmt name} ]
    skin_of: list                       # outfit index -> skin index
    vmt_cd: str
    mpath: str
    notes: list = field(default_factory=list)
    materials: dict = field(default_factory=dict)   # vmt name -> source material (key, name, textures) for the UI

    @property
    def addon(self) -> Path:
        return self.cfg.addon_dir(self.source.id)

    def default_parts(self) -> list:
        parts = [pp for pr in self.base for pp in self.posed[pr]]
        for g in self.groups:
            if 0 in self.by_prim[g.members[0]].wearers:
                parts += self.posed[g.members[0]]
        return parts

    def presets(self) -> list[dict]:
        out = []
        for k, o in enumerate(self.outfits):
            bg = {}
            for g in self.groups:
                worn = [i for i, m in enumerate(g.members) if k in self.by_prim[m].wearers]
                bg[g.name] = worn[0] if worn else len(g.members)          # "blank" is the last option
            out.append({"name": o.name, "variant": o.variant, "skin": self.skin_of[k], "bodygroups": bg})
        return out

    def option_labels(self, g: Group) -> list[str]:
        """Bodygroup option names; two meshes can come from parts with the same name (a mesh swapped by an
        outfit, the same garment in two body types): they are numbered."""
        out, seen = [], {}
        for m in g.members:
            lab = self.by_prim[m].label
            seen[lab] = seen.get(lab, 0) + 1
            out.append(lab if seen[lab] == 1 else f"{lab} ({seen[lab]})")
        return out + (["none"] if g.blank else [])

    def meta(self) -> dict:
        return {"model": f"models/{self.mpath}.mdl", "family": self.outfits[0].family, "lod": self.lod,
                "groups": [{"name": g.name, "options": self.option_labels(g)} for g in self.groups],
                "base": [self.by_prim[m].label for m in self.base], "skins": len(self.skins), "presets": self.presets(),
                "materials": list(self.materials.values())}


def prepare(source, outfit_keys: list[int], opts, cfg: Config = CONFIG, name: str = "") -> Plan:
    """Resolve the outfits, plan bodygroups/skins, convert every material variant, pose every mesh."""
    from ...sources.glacier.borg import parse_borg
    from ...sources.glacier.outfit import OutfitResolver
    resolver = OutfitResolver(source)
    outfits = [o for o in (resolver.resolve(k) for k in outfit_keys) if o.parts]
    if not outfits:
        raise ValueError("no outfit with visible parts")
    name = name or outfits[0].family
    body = outfits[0].body
    tpl = pm.load_template("female" if body.startswith("fem") else "male")
    pieces, base, groups = plan_groups(outfits)
    by_prim = {p.prim: p for p in pieces}
    notes = []

    # LOD: the heaviest variation must fit the triangle budget
    lod = opts.lod
    while True:
        models = {p.prim: source.load_model(p.prim, lod) for p in pieces}
        tris = {pr: sum(len(sm.indices) // 3 for sm in m.submeshes) for pr, (m, _x) in models.items()}
        heaviest = max(sum(tris[p.prim] for p in pieces if k in p.wearers) for k in range(len(outfits)))
        if heaviest <= opts.max_tris or lod >= 4:
            break
        lod += 1
    if lod != opts.lod:
        notes.append(f"LOD raised to {lod} to fit {opts.max_tris} triangles")
    # meshes without their own BORG are skinned to the rig the outfit declares
    for p in pieces:
        m, _x = models[p.prim]
        if m.rig is None and any(sm.joints is not None for sm in m.submeshes):
            for k in sorted(p.wearers):
                bp = source.archive.find("BORG", outfits[k].rig) if outfits[k].rig else None
                if bp is not None:
                    m.rig = parse_borg(bp.read_bytes())
                    break

    # skeleton: the characters' own rig, posed onto ValveBiped (proportion trick)
    sk = pm.merge_rigs([m.rig for m, _x in models.values() if m.rig is not None])
    if not sk.names:
        raise ValueError("no skinned part")
    P, src_of = pm.fit_pose(sk, tpl)
    S = pm.proportions(tpl, sk, P, src_of)
    targets = pm.weight_targets(sk, tpl)

    # materials: one Source material per (mesh, slot) column, one variant per skin
    addon = cfg.addon_dir(source.id)
    cd = f"omni/{source.id}"
    slug_name = slug(name, 40)
    vmt_cd = f"{cd}/pm/{slug_name}"
    vdir = addon / "materials" / vmt_cd
    if vdir.exists():
        shutil.rmtree(vdir, ignore_errors=True)
    cache = TextureCache(source)
    converted: dict[str, tuple[str, bool]] = {}          # material variant key -> (vmt name, visible)

    infos: dict[str, dict] = {}

    def variant(final: int, params: dict):
        mat = source.load_material_variant(final, params)
        if mat.key not in converted:
            r = convert_material(mat, cache, addon / "materials", cd, opts.mat, vmt_cd=vmt_cd)
            visible = not r.drop and any(x.role in _VISIBLE for x in mat.textures)
            converted[mat.key] = (mat.name, visible)
            if visible:
                cls = next((f[6:] for f in mat.flags if f.startswith("class:")), "")
                infos[mat.name] = {"name": mat.name, "key": mat.key[:16], "source_name": mat.source_name, "class": cls,
                                   "params": {k: [round(float(x), 4) for x in v] for k, v in list(mat.params.items())[:60]},
                                   "textures": [{"role": t.role, "slot": t.slot, "key": t.key} for t in mat.textures]}
        return converted[mat.key]

    # per outfit: (mesh, slot) -> material variant
    raw: list[dict[tuple[int, int], str]] = []
    hidden: set[tuple[int, int]] = set()
    for k in range(len(outfits)):
        row = {}
        for p in pieces:
            k_src = k if k in p.wearers else min(p.wearers)
            for src_mati, slot in p.slots[k_src].items():
                vname, visible = variant(slot.final, slot.params)
                if not visible and k == 0:
                    hidden.add((p.prim, src_mati))
                row[(p.prim, src_mati)] = vname
        raw.append(row)
    slots_all = [c for c in raw[0] if c not in hidden]
    # identical colour sets collapse into one skin
    sigs, skin_rows, skin_of = [], [], []
    for row in raw:
        sig = tuple(row[c] for c in slots_all)
        if sig not in sigs:
            sigs.append(sig)
            skin_rows.append(row)
        skin_of.append(sigs.index(sig))

    def materials_for(nskins: int):
        """SMD material per (mesh, slot) + skin table, with as few Source materials as possible: a slot that
        never changes uses its variant directly (shared by every slot with that variant); slots that change
        the same way across skins share one column material."""
        rows = skin_rows[:nskins]
        seq = {c: tuple(r[c] for r in rows) for c in slots_all}
        names, col_of_seq = {}, {}
        for c in slots_all:
            if len(set(seq[c])) == 1:
                names[c] = seq[c][0]
            else:
                if seq[c] not in col_of_seq:
                    col_of_seq[seq[c]] = f"c{len(col_of_seq):02d}_{_short(seq[c][0], 44)}"
                names[c] = col_of_seq[seq[c]]
        table = [{col: s[i] for s, col in col_of_seq.items()} for i in range(len(rows))]
        used = set(names.values()) | {v for row in table[1:] for v in row.values()}
        return names, table, len(used)

    nskins = min(len(skin_rows), MAX_SKINS)
    names, table, count = materials_for(nskins)
    while count > MAX_MATERIALS and nskins > 1:
        nskins -= 1
        names, table, count = materials_for(nskins)
    if nskins < len(skin_rows):
        notes.append(f"{len(skin_rows)} colour sets, {nskins} kept as skins (Source: {MAX_SKINS} skins, "
                     f"{MAX_MATERIALS} materials per model)")
        skin_of = [s if s < nskins else 0 for s in skin_of]
    for row in table:                                     # column materials = copies of the skin-0 variants
        for col, v in row.items():
            src_vmt, dst = vdir / f"{v}.vmt", vdir / f"{col}.vmt"
            if src_vmt.exists() and not dst.exists():
                dst.write_text(src_vmt.read_text())
    columns = names
    skins = table

    # meshes, posed and bound to the ValveBiped proportions skeleton
    posed = {}
    for p in pieces:
        model, mats = models[p.prim]
        out = []
        for sm in model.submeshes:
            if sm.zbias > 0 and sm.material_key in mats and "decal" in (mats[sm.material_key].source_name or "").lower():
                continue
            col = (p.prim, int(sm.material_key, 16)) if sm.material_key else None
            if col is None or col not in columns or col in hidden:
                continue
            if model.rig is None:                 # no skeleton found for this part: it cannot follow the body
                note = f"part {p.label} skipped: no skeleton (BORG) found"
                if note not in notes:
                    notes.append(note)
                continue
            pp = pm.skin_submesh(sm, model.rig, sk, P, S, targets, tpl)
            pp.material_key = columns[col]
            out.append(pp)
        posed[p.prim] = out
    return Plan(source, cfg, name, outfits, tpl, pieces, by_prim, base, groups, lod, sk, P, S, posed, columns,
                skins, skin_of, vmt_cd, f"omni/{source.id}/pm/{slug_name}", notes, infos)


def compile_plan(plan: Plan, res: PMResult, title: str = "") -> None:
    cfg, tpl, S, P, sk = plan.cfg, plan.tpl, plan.S, plan.P, plan.sk
    work = cfg.sandbox / "modelsrc" / plan.mpath
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    names = {c: c for c in plan.columns.values()}
    bodies = []
    base_parts = [pp for pr in plan.base for pp in plan.posed[pr]]
    if base_parts:
        pm.write_reference(work / "base.smd", tpl, S, base_parts, names)
        bodies.append('$body "body" "base.smd"')
    for gi, g in enumerate(plan.groups):
        bodies += [f'$bodygroup "{g.name}"', "{"]
        for oi, prim in enumerate(g.members):
            fn = f"g{gi:02d}_{oi:02d}_{_short(plan.by_prim[prim].label, 40)}.smd"
            pm.write_reference(work / fn, tpl, S, plan.posed[prim], names)
            bodies.append(f'\tstudio "{fn}"')
        if g.blank:
            bodies.append("\tblank")
        bodies.append("}")
    default_parts = plan.default_parts()
    res.triangles = sum(len(pp.indices) // 3 for pp in default_parts)
    if not default_parts:
        res.errors.append("no visible geometry")
        return
    pm.write_skeleton(work / "reference_male.smd", tpl)
    pm.write_skeleton(work / "proportions.smd", tpl, S)
    volumes = pm.write_physics(work / "phys.smd", tpl, S, default_parts)

    info = tpl.info
    head = tpl.index("ValveBiped.Bip01_Head1")
    eyes = [P[sk.index[e], :3, 3] for e in ("L_eye", "R_eye") if e in sk.index]
    overrides, eye_pos = {}, np.array(info.eye_position)
    if eyes:
        eye_pos = np.mean(eyes, 0)
        overrides["eyes"] = (np.linalg.inv(S[head]) @ np.r_[eye_pos, 1.0])[:3]
    qc = [f'$modelname "{plan.mpath}.mdl"'] + bodies + ['$surfaceprop "flesh"', f'$cdmaterials "{plan.vmt_cd}/"',
                                                        "$eyeposition %.3f %.3f %.3f" % tuple(eye_pos),
                                                        "$illumposition %.3f %.3f %.3f" % info.illum_position]
    varying = list(plan.skins[0]) if plan.skins else []
    if len(plan.skins) > 1 and varying:
        # row 0 = the column materials of the SMDs (copies of the skin-0 variants); the other rows remap them
        qc += ['$texturegroup "skinfamilies"', "{", "\t{ " + " ".join(f'"{c}"' for c in varying) + " }"]
        for row in plan.skins[1:]:
            qc.append("\t{ " + " ".join(f'"{row[c]}"' for c in varying) + " }")
        qc.append("}")
    qc += pm.hitbox_qc(tpl, S, default_parts) + pm.attachment_qc(tpl, overrides) + pm.bonemerge_qc(tpl)
    qc += ['$sequence "reference" "reference_male.smd" fps 1',
           '$animation "a_proportions" "proportions.smd" subtract "reference" 0',
           '$sequence "proportions" "a_proportions" predelta autoplay']
    qc += pm.ik_qc(tpl)
    qc += [f'$includemodel "{Path(tpl.anim_include).name}"']
    qc += pm.collision_qc(tpl, volumes)
    (work / "model.qc").write_text("\n".join(qc) + "\n")

    cr = compile_qc(work / "model.qc", cfg.sandbox, cfg.studiomdl, timeout=300)
    if not cr.ok:
        res.errors += cr.errors[:8] or [cr.log[-600:]]
        return
    outdir = cfg.sandbox / "models" / Path(plan.mpath).parent
    dest = plan.addon / "models" / Path(plan.mpath).parent
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in outdir.glob(Path(plan.mpath).name + ".*"):
        shutil.copy2(f, dest / f.name)
        copied += 1
    if not copied:
        res.errors.append("compiler produced no files")
        return
    (dest / f"{Path(plan.mpath).name}.json").write_text(json.dumps(plan.meta(), indent=1), encoding="utf-8")
    res.model = f"models/{plan.mpath}.mdl"
    res.status = "OK"
    res.notes += cr.warnings[:5]
    register(plan.addon, title or plan.name, res.model)


def build_outfit_pm(source, outfit_keys: list[int], opts, cfg: Config = CONFIG, name: str = "",
                    title: str = "") -> PMResult:
    """``opts``: pm_build.PMOptions (lod, max_tris, mat); the skeleton template follows the outfits' body type."""
    t0 = time.perf_counter()
    res = PMResult(name=name or "outfit")
    try:
        plan = prepare(source, outfit_keys, opts, cfg, name)
        res.name = plan.name
        res.lod = plan.lod
        res.parts = ["%016X" % p.prim for p in plan.pieces]
        res.materials = len({v for row in plan.skins for v in row.values()})
        res.notes += plan.notes + [f"{len(plan.groups)} bodygroups, {len(plan.skins)} skins, {len(plan.outfits)} variations"]
        compile_plan(plan, res, title)
    except Exception as e:  # noqa: BLE001
        import traceback
        res.errors.append(f"{type(e).__name__}: {e}")
        res.errors.append(traceback.format_exc()[-1500:])
    finally:
        res.seconds = round(time.perf_counter() - t0, 2)
    return res


# ------------------------------------------------------------------------------------ preview
def export_preview(plan: Plan, out_glb: Path, tex_size: int = 512) -> dict:
    """GLB with one node per bodygroup option and every skin material, plus the JSON the UI needs to switch
    bodygroups/skins/presets. Textures are read back from the converted VTFs (what GMod will show)."""
    from ...preview.glb import export_scene
    from .vtfread import read_vtf
    mroot = plan.addon / "materials"
    mat_index: dict[str, int] = {}
    materials = []

    def material(vmt_name: str) -> int:
        if vmt_name in mat_index:
            return mat_index[vmt_name]
        vmt = (mroot / plan.vmt_cd / f"{vmt_name}.vmt")
        text = vmt.read_text() if vmt.exists() else ""
        entry = {"name": vmt_name, "base": None, "normal": None,
                 "alpha": "MASK" if "$alphatest" in text else ("BLEND" if "$translucent" in text else "OPAQUE")}
        for key, slot in (("$basetexture", "base"), ("$bumpmap", "normal")):
            m = re.search(r'"%s"\s+"([^"]+)"' % re.escape(key), text)
            if m and (mroot / (m.group(1) + ".vtf")).exists():
                entry[slot] = mroot / (m.group(1) + ".vtf")
        materials.append(entry)
        mat_index[vmt_name] = len(materials) - 1
        return mat_index[vmt_name]

    nodes = []
    def add_parts(tag: str, parts):
        for i, pp in enumerate(parts):
            nodes.append({"name": f"{tag}|{pp.material_key}|{i}", "positions": pp.positions / pm.UNITS_PER_METER,
                          "normals": pp.normals, "uvs": pp.uvs, "indices": pp.indices,
                          "material": material(pp.material_key)})

    add_parts("base", [pp for pr in plan.base for pp in plan.posed[pr]])
    for gi, g in enumerate(plan.groups):
        for oi, prim in enumerate(g.members):
            add_parts(f"g{gi}o{oi}", plan.posed[prim])
    skins = [{c: material(v) for c, v in row.items() if c in {n["name"].split("|")[1] for n in nodes}} for row in plan.skins]
    export_scene(nodes, materials, out_glb, tex_size, read_vtf)
    meta = plan.meta()
    meta["skin_materials"] = skins
    out_glb.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
