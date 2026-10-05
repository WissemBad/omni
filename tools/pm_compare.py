"""Compare a retargeted character with the stock GMod player model, bone region by bone region."""
import sys
import numpy as np
from omni.sources.glacier.adapter import GlacierSource
from omni.targets.source import playermodel as pm
from omni.targets.source.mdlmesh import read_mesh
from omni.targets.source.pm_build import _find_part

lod = int(sys.argv[1]); specs = sys.argv[2:]
src = GlacierSource(); tpl = pm.load_template("male"); names = tpl.names
pk = pm._vpk(); stem = "models/player/group01/male_01"
rpos, _n, _u, _i, rdom = read_mesh(pk[stem + ".mdl"].read(), pk[stem + ".vvd"].read(), pk[stem + ".dx90.vtx"].read())
pos, dom = [], []
for sp in specs:
    model, _ = src.load_model(_find_part(src, sp), lod)
    rt = pm.build_retarget(model.rig, tpl, pm.Z180)
    for sm in model.submeshes:
        pp = pm.pose_submesh(sm, rt, len(model.rig.names), 0)
        pos.append(pp.positions); dom.append(pp.bones[np.arange(len(pp.bones)), pp.weights.argmax(1)])
pos, dom = np.concatenate(pos), np.concatenate(dom)
print("height  mine %.1f  stock %.1f" % (pos[:, 2].max(), rpos[:, 2].max()))
print("%-10s %-22s %-22s %s" % ("region", "centroid mine", "centroid stock", "extent mine / stock (x,y,z)"))
for r in ("Pelvis", "Spine2", "Spine4", "Head1", "L_UpperArm", "L_Forearm", "L_Hand", "L_Thigh", "L_Calf", "L_Foot"):
    j = names.index("ValveBiped.Bip01_" + r)
    a, b = pos[dom == j], rpos[rdom == j]
    if len(a) < 5 or len(b) < 5:
        print("%-10s n/a (%d / %d verts)" % (r, len(a), len(b))); continue
    print("%-10s %-22s %-22s %s / %s" % (r, np.round(a.mean(0), 1), np.round(b.mean(0), 1), np.round(np.ptp(a, 0), 1), np.round(np.ptp(b, 0), 1)))
