"""Build one source model into a GMod addon folder: materials -> SMD/QC -> studiomdl -> addon."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.config import CONFIG, Config
from ...core import geom
from . import collision, smd
from .compile import compile_qc
import copy

from .materials import Options, TextureCache, convert_material, guess_surfaceprop

UNITS_PER_METER = 39.37
# surfaceprop -> (material density kg/m3, typical wall thickness m). Props are often hollow shells
# (fridge, cabinet): mass = min(solid volume x density, surface x wall thickness x density).
MATTER = {"metal": (7800, 0.0015), "wood": (650, 0.02), "glass": (2500, 0.004), "concrete": (2300, 0.15),
          "rock": (2600, 0.15), "ceramic": (2300, 0.006), "carpet": (150, 0.03), "plastic": (1100, 0.003),
          "cardboard": (700, 0.003), "paper": (800, 0.01), "rubber": (1100, 0.01), "dirt": (1500, 0.1),
          "foliage": (300, 0.002), "default": (800, 0.01)}
MASS_MIN, MASS_MAX = 0.5, 50000.0     # GMod/VPhysics accepts up to 50000 kg


@dataclass
class BuildOptions:
    physics: bool = True
    mass: float | None = None
    scale: float = UNITS_PER_METER
    mat: Options = field(default_factory=Options)
    skinned_as_static: bool = True
    collision: str = "game"            # game (game PhysX shapes, else parts) | parts | hull | coacd
    lods: bool = True                  # the game's own lower levels of detail become Source $lod models
    variants: bool = True              # colour/material variants of the game's templates become skins


@dataclass
class BuildResult:
    key: str
    status: str = "FAILED"            # OK | PARTIAL | FAILED | SKIPPED (nothing to convert)
    model: str = ""
    triangles: int = 0
    materials: int = 0
    seconds: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    unknown_slots: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _model_path(source_id: str, rel: str, key: str) -> str:
    base = f"omni/{source_id}/"
    path = base + rel
    if len(path) > 110:
        parts = rel.split("/")
        path = base + parts[-1][:30] + "_" + key[-6:].lower()
    return path


# Source $lod switch points (screen-size metric, already relative to the model's size): the game's LOD1/2/3
LOD_SWITCH = (12, 30, 60)


def _game_lods(source, h: int, model, materials: dict, names: dict, dropped: set, work: Path, scale: float,
               res: "BuildResult") -> list[str]:
    """Write the game's lower LODs as Source LOD models. A level is kept only when it really is lighter
    (< 75 % of the previous one's triangles); materials are those already converted for LOD0."""
    if len(model.lods) < 2:
        return []
    qc, prev = [], res.triangles
    for lvl in [l for l in model.lods if l > model.lod]:
        if len(qc) // 4 >= len(LOD_SWITCH):
            break
        try:
            lm, _m = source.load_model(h, lvl)
        except Exception:  # noqa: BLE001 - LODs are optional
            continue
        if lm.lod != lvl:
            continue
        geom.dedupe_overlapping(lm.submeshes)
        subs = []
        for sm in lm.submeshes:
            mk = sm.material_key + "|decal" if sm.zbias > 0 and sm.material_key + "|decal" in materials else sm.material_key
            if mk in dropped or mk not in names or len(sm.indices) < 3:
                continue
            sm.material_key = mk
            subs.append(sm)
        tris = sum(len(s.indices) // 3 for s in subs)
        if not subs or tris > 0.75 * prev:
            continue
        fn = f"lod{lvl}.smd"
        smd.write_reference(work / fn, subs, names, scale)
        qc += [f"$lod {LOD_SWITCH[len(qc) // 4]}", "{", f'\treplacemodel "ref.smd" "{fn}"', "}"]
        res.notes.append(f"LOD{lvl}: {tris} triangles")
        prev = tris
    return qc


MAX_MATERIALS = 128                    # studiomdl limit (skin variants included)
BIG_MESH = 50_000                      # triangles above which a studiomdl timeout is blamed on the mesh


def compile_timeout(triangles: int) -> int:
    """Seconds studiomdl may take: 60 s base, one more per 1,500 triangles, 10 minutes at most."""
    return int(min(600, 60 + triangles / 1500))


def _variant_skins(source, h: int, model, names: dict, dropped: set, cache, addon: Path, cd: str,
                   opts: "BuildOptions", res: "BuildResult") -> list[str]:
    """Colour/material variants the game's templates apply to this mesh -> $texturegroup (skin 0 = the
    mesh's own materials). Only materials that change in some variant are remapped."""
    try:
        variants = source.prop_variants(h)
    except Exception as e:  # noqa: BLE001 - variants are a bonus, never a failure
        res.notes.append(f"variants skipped: {type(e).__name__}")
        return []
    if not variants:
        return []
    used = {sm.material_key for sm in model.submeshes}
    rows = []
    for var in variants:
        row = {}
        for slot, (final, params) in var.items():
            mk = "%016X" % slot
            if mk not in used or mk in dropped or mk not in names:
                continue
            mat = source.load_material_variant(final, params)
            if mat.key == mk:
                continue
            r = convert_material(mat, cache, addon / "materials", cd, opts.mat)
            if r.drop:
                continue
            row[names[mk]] = mat.name
        if row and row not in rows:
            rows.append(row)
    if not rows:
        return []
    cols = sorted({c for row in rows for c in row})
    while rows and len(cols) + len({v for row in rows for v in row.values()}) > MAX_MATERIALS:
        rows.pop()
    qc = ['$texturegroup "skinfamilies"', "{", "\t{ " + " ".join(f'"{c}"' for c in cols) + " }"]
    for row in rows:
        qc.append("\t{ " + " ".join(f'"{row.get(c, c)}"' for c in cols) + " }")
    qc.append("}")
    res.notes.append(f"{len(rows) + 1} skins from the game's variants")
    return qc


def deploy_file(src: Path, dst: Path) -> None:
    """Move a compiled file from the sandbox into the addon (same disk: a rename, no copy); copy if it cannot be
    moved (other volume, file held by GMod...)."""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_model(source, key: str, cfg: Config = CONFIG, opts: BuildOptions | None = None) -> BuildResult:
    opts = opts or BuildOptions()
    res = BuildResult(key=key)
    t0 = time.perf_counter()
    try:
        h = int(key, 16)
        model, materials = source.load_model(h)
        res.seconds["load"] = round(time.perf_counter() - t0, 3)
        if not model.submeshes:
            res.errors.append("no geometry")
            return res
        if model.skinned and not opts.skinned_as_static:
            res.errors.append("skinned models only supported as static statues")
            return res
        res.notes += model.warnings
        dup = geom.dedupe_overlapping(model.submeshes)
        if dup:
            res.notes.append(f"{dup} duplicated coplanar triangle(s) removed (z-fighting)")
            model.submeshes = [sm for sm in model.submeshes if len(sm.indices) >= 3]
        if model.skinned:
            res.notes.append("skinned model exported as a static pose (statue)")

        addon = cfg.addon_dir(source.id)
        cd = f"omni/{source.id}"

        # overlay layers (game z-bias) get their own "$decal" variant of the material
        for sm in model.submeshes:
            if sm.zbias > 0 and sm.material_key in materials:
                dk = sm.material_key + "|decal"
                if dk not in materials:
                    m2 = copy.deepcopy(materials[sm.material_key])
                    m2.key, m2.name = dk, m2.name + "_dcl"
                    m2.flags.add("overlay")
                    materials[dk] = m2
                sm.material_key = dk

        t = time.perf_counter()
        cache = TextureCache(source)
        names, translucent, dropped = {}, set(), set()
        for mk, mat in materials.items():
            r = convert_material(mat, cache, addon / "materials", cd, opts.mat)
            names[mk] = mat.name
            if r.drop:
                dropped.add(mk)
            if r.translucent:
                translucent.add(mk)
            res.unknown_slots += r.unknown_slots
            res.notes += [f"{mat.name}: {n}" for n in r.notes]
        if dropped:
            model.submeshes = [sm for sm in model.submeshes if sm.material_key not in dropped]
            res.notes.append(f"{len(dropped)} decal material(s) without usable alpha removed")
            if not model.submeshes:
                res.status = "SKIPPED"
                res.notes.append("only decal layers (nothing to draw)")
                return res
        res.materials = len(materials)
        res.seconds["materials"] = round(time.perf_counter() - t, 3)

        mpath = _model_path(source.id, model.name, key)
        work = cfg.sandbox / "modelsrc" / mpath
        work.mkdir(parents=True, exist_ok=True)
        t = time.perf_counter()
        res.triangles = smd.write_reference(work / "ref.smd", model.submeshes, names, opts.scale)
        lod_qc = _game_lods(source, h, model, materials, names, dropped, work, opts.scale, res) if opts.lods else []

        # surfaceprop of each solid (non-overlay) submesh, from its material's original path
        area_by_prop: dict[str, float] = {}
        shell_mass = 0.0
        for sm in model.submeshes:
            if sm.zbias > 0:
                continue
            tri = sm.positions[sm.indices.reshape(-1, 3)]
            area = float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)
            m = materials.get(sm.material_key)
            prop = guess_surfaceprop(m.source_name if m else "", m.name if m else "")
            area_by_prop[prop] = area_by_prop.get(prop, 0.0) + area
            dens, thick = MATTER.get(prop, MATTER["default"])
            shell_mass += area * thick * dens
        named = {k: v for k, v in area_by_prop.items() if k != "default"}
        surf = max(named or area_by_prop or {"default": 1}, key=lambda k: (named or area_by_prop or {"default": 1})[k])
        if surf == "default":
            surf = guess_surfaceprop(model.name)

        qc = [f'$modelname "{mpath}.mdl"', "$staticprop", f'$surfaceprop "{surf}"',
              '$body "body" "ref.smd"', f'$cdmaterials "{cd}/"', '$sequence "idle" "ref.smd" loop fps 1'] + lod_qc
        if translucent and len(translucent) < len(materials):
            qc.append("$mostlyopaque")       # opaque parts drawn in the opaque pass, glass sorted after
        if opts.variants and hasattr(source, "prop_variants"):
            qc += _variant_skins(source, h, model, names, dropped, cache, addon, cd, opts, res)
        phys_qc: list[str] = []
        if opts.physics:
            solids = [s for s in model.submeshes if s.zbias == 0] or model.submeshes
            hulls, solid_vol, method = [], 0.0, ""
            if opts.collision == "game" and hasattr(source, "load_collision"):
                # the game's own PhysX shapes (dynamic variant first): what the object really collides with
                game = source.load_collision(h)
                if game and collision.game_size(game) > collision.MAX_PROP_SIZE:
                    method = "scenery"
                elif game:
                    hulls, solid_vol = collision.from_game(game)
                    method = "game" + (" dynamic" if game.get("dynamic") else "")
            if not hulls and method != "scenery":
                hulls, solid_vol, method = collision.build_collision(solids, "parts" if opts.collision == "game" else opts.collision)
            if hulls:
                dens = MATTER.get(surf, MATTER["default"])[0]
                mass = opts.mass or float(np.clip(min(solid_vol * dens, shell_mass or 1e9), MASS_MIN, MASS_MAX))
                smd.write_hulls(work / "phys.smd", [(h[0], h[1]) for h in hulls], opts.scale)
                phys_qc = ['$collisionmodel "phys.smd"', "{", f"\t$mass {mass:.2f}"]
                if len(hulls) > 1:
                    phys_qc.append("\t$concave")
                phys_qc.append("}")
                res.notes.append(f"collision: {method} ({len(hulls)} piece(s)), {mass:.1f} kg, {surf}")
            else:
                res.notes.append("no collision model (scenery-sized object)" if method == "scenery" else "no collision hull")
        (work / "model.qc").write_text("\n".join(qc + phys_qc) + "\n")
        res.seconds["write"] = round(time.perf_counter() - t, 3)

        outdir = cfg.sandbox / "models" / Path(mpath).parent
        dest = addon / "models" / Path(mpath).parent
        stem = Path(mpath).name

        def clear(folder: Path) -> None:
            for f in folder.glob(stem + ".*"):
                f.unlink(missing_ok=True)

        t = time.perf_counter()
        clear(outdir)                                   # nothing left from an earlier run can be copied by mistake
        timeout = compile_timeout(res.triangles)
        cr = compile_qc(work / "model.qc", cfg.sandbox, cfg.studiomdl, timeout=timeout)
        # the physics compiler hangs or fails on some degenerate/huge hulls: keep the model, drop its collision.
        # A timeout on a big mesh is the mesh itself, not the hulls: retrying without them would only cost it twice.
        if not cr.ok and phys_qc and not (cr.timed_out and res.triangles > BIG_MESH):
            (work / "model.qc").write_text("\n".join(qc) + "\n")
            res.notes.append("collision removed: studiomdl could not build it (" + (cr.errors[:1] or ["?"])[0][:80] + ")")
            clear(outdir)
            cr = compile_qc(work / "model.qc", cfg.sandbox, cfg.studiomdl, timeout=timeout)
        res.seconds["compile"] = round(time.perf_counter() - t, 3)
        if not cr.ok:
            res.errors += cr.errors[:8] or [cr.log[-400:]]
            return res

        dest.mkdir(parents=True, exist_ok=True)
        clear(dest)                                     # a .phy of an earlier build must not outlive its model
        copied = 0
        for f in outdir.glob(stem + ".*"):
            deploy_file(f, dest / f.name)
            copied += 1
        if not copied:
            res.errors.append("compiler produced no files")
            return res
        shutil.rmtree(work, ignore_errors=True)         # SMD sources (MBs each) are only kept when a build fails
        res.model = f"models/{mpath}.mdl"
        res.status = "PARTIAL" if (res.unknown_slots or cr.warnings) else "OK"
        if cr.warnings:
            res.notes += cr.warnings[:5]
    except Exception as e:  # noqa: BLE001 - report per-asset failures instead of aborting the batch
        res.errors.append(f"{type(e).__name__}: {e}")
    finally:
        res.seconds["total"] = round(time.perf_counter() - t0, 3)
    return res
