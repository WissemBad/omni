"""Dump PRIM -> MATI -> TEXT/TEXD chain for a hash, with names."""
import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "omni"))
from omni.sources.glacier.meta import parse_meta, load_names  # noqa: E402
from omni.sources.glacier.mati import parse_mati  # noqa: E402
from omni.sources.glacier.prim import parse_prim  # noqa: E402

S = ROOT / "Assets/Sorted/chunk0"
h = sys.argv[1].upper()
meta = parse_meta((S / "PRIM" / f"{h}.PRIM.meta").read_bytes())
names = load_names(ROOT / "workspace/names/hash_list.txt")
nm = lambda x: names.get(x, "?")
print("PRIM", h, nm(int(h, 16)))
for r, f in meta.refs:
    print("  ref %016X flag %02X" % (r, f), nm(r))
prim = parse_prim((S / "PRIM" / f"{h}.PRIM").read_bytes())
for i, m in enumerate(prim.meshes):
    print("  mesh", i, "mat_id", m.material_id, "verts", len(m.positions), "tris", len(m.indices) // 3, "lod", m.lod_mask)
for r, f in meta.refs:
    p = S / "MATI" / f"{r:016X}.MATI"
    if not p.exists():
        continue
    mm = parse_meta((S / "MATI" / f"{r:016X}.MATI.meta").read_bytes())
    mt = parse_mati(p.read_bytes())
    print("MATI %016X" % r, nm(r), "ok", mt.ok)
    for ri, (rr, ff) in enumerate(mm.refs):
        print("   ref[%d] %016X f%02X %s" % (ri, rr, ff, nm(rr)))
    for t in mt.textures:
        rr = mm.refs[t.ref_index][0] if t.ref_index < len(mm.refs) else None
        print("   TEX", t.name, "->", t.ref_index, "%016X" % rr if rr else None)
    for q in mt.params:
        print("   PAR", q.name, [round(v, 3) for v in q.values])
