"""Preview a retargeted player model as GLB, textured with what the Source export REALLY produced
(VTFs written by convert_material into a scratch workspace).  usage: pm_preview.py out.glb lod spec..."""
import sys, re
from pathlib import Path
import numpy as np
from omni.core.config import Config
from omni.core.ir import Model, SubMesh
from omni.sources.glacier.adapter import GlacierSource
from omni.preview.glb import export_glb
from omni.targets.source import playermodel as pm
from omni.targets.source.materials import Options, TextureCache, convert_material
from omni.targets.source.pm_build import _find_part
from omni.targets.source.vtfread import read_vtf

out = Path(sys.argv[1]); lod = int(sys.argv[2]); specs = sys.argv[3:]
scratch = Path(__file__).resolve().parents[2] / "workspace" / "_pm_preview"
src = GlacierSource(); tpl = pm.load_template("male")
subs, mats = [], {}
loaded = [src.load_model(_find_part(src, sp), lod) for sp in specs]
sk = pm.merge_rigs([m.rig for m, _ in loaded])
P, src_of = pm.fit_pose(sk, tpl)
S = pm.proportions(tpl, sk, P, src_of)
tg = pm.weight_targets(sk, tpl)
for model, m in loaded:
    for sm in model.submeshes:
        if not m.get(sm.material_key) or not any(x.role in ('base','normal','srm','spec','emissive') for x in m[sm.material_key].textures) or 'decal' in (m[sm.material_key].source_name or '').lower():
            continue
        pp = pm.skin_submesh(sm, model.rig, sk, P, S, tg, tpl)
        subs.append(SubMesh(positions=(pp.positions / pm.UNITS_PER_METER).astype(np.float32), normals=pp.normals.astype(np.float32),
                            uvs=pp.uvs, indices=pp.indices, material_key=pp.material_key))
    mats.update(m)
cache = TextureCache(src); ov = {}
for key, mat in mats.items():
    convert_material(mat, cache, scratch / "materials", "omni/007fl", Options())
    vmt = (scratch / "materials/omni/007fl" / f"{mat.name}.vmt").read_text()
    o = {}
    for k, name in (("$basetexture", "base"), ("$bumpmap", "normal")):
        mm = re.search(r'"%s"\s+"([^"]+)"' % re.escape(k), vmt)
        if mm and (scratch / "materials" / (mm.group(1) + ".vtf")).exists():
            a = read_vtf(scratch / "materials" / (mm.group(1) + ".vtf"), 1024)
            # the alpha channel only means opacity when the VMT says so (else: AO, iris mask, phong mask...)
            if name == "normal" or ("$alphatest" not in vmt and "$translucent" not in vmt):
                a = a.copy(); a[..., 3] = 255
            o[name] = a
    ov[key] = o
export_glb(src, Model("pm", "pm", subs), mats, out, tex_size=1024, overrides=ov)
print(out, sum(len(s.indices) // 3 for s in subs), "tris")
