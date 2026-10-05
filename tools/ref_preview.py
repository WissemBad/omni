"""Export a stock GMod model (from garrysmod_dir.vpk) as GLB, to compare with converted characters.
usage: ref_preview.py out.glb models/player/group01/male_01"""
import sys
from pathlib import Path
import numpy as np
from omni.core.ir import Model, SubMesh
from omni.preview.glb import export_glb
from omni.targets.source import playermodel as pm
from omni.targets.source.mdlmesh import read_mesh

out, stem = Path(sys.argv[1]), sys.argv[2]
pk = pm._vpk()
pos, nrm, uv, idx, bone = read_mesh(pk[stem + ".mdl"].read(), pk[stem + ".vvd"].read(), pk[stem + ".dx90.vtx"].read())
print("verts", len(pos), "tris", len(idx) // 3, "bbox", pos.min(0).round(1), pos.max(0).round(1))
sm = SubMesh(positions=(pos / pm.UNITS_PER_METER).astype(np.float32), normals=nrm.astype(np.float32), uvs=uv.astype(np.float32),
             indices=idx.astype(np.uint32))
export_glb(None, Model("ref", "ref", [sm]), {}, out)
