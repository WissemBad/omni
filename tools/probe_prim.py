"""Survey of PRIM structure over many files: field values, subtypes, uv ranges, layout sanity."""
import sys
import struct
import pathlib
import collections
import random

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
files = sorted((ROOT / "Assets/Sorted/chunk0/PRIM").glob("*.PRIM"))
random.seed(1)
sample = random.sample(files, int(sys.argv[1]) if len(sys.argv) > 1 else 300)

subtypes = collections.Counter()
props = collections.Counter()
unk0c = collections.Counter()
unk18 = collections.Counter()
aux_zero = collections.Counter()
uv_out = 0
tot = 0
gaps = collections.Counter()
for f in sample:
    d = f.read_bytes()
    mv = memoryview(d)
    (ho,) = struct.unpack_from("<Q", mv, 0)
    _, _, _, flags, _pad, rig, cnt, to = struct.unpack_from("<BBHIIIII", mv, ho)
    offs = struct.unpack_from("<%dI" % cnt, mv, to)
    for off in offs:
        (_, _, _, st, pr, lod, var, zb, zo, mid, wire) = struct.unpack_from("<BBHBBBBBBHI", mv, off)
        nv, vbo, ni, u0c, ibo, aux, u18 = struct.unpack_from("<7I", mv, off + 44)
        tsb = np.frombuffer(mv, "<f4", 4, off + 44 + 28 + 32)
        subtypes[(st, bool(flags & 8))] += 1
        props[pr] += 1
        unk0c[u0c] += 1
        unk18[u18] += 1
        aux_zero[aux == 0] += 1
        if st in (0, 1, 2) and nv:
            q = vbo + nv * 8 + (nv * 8 if st == 2 else 0)
            ntb = np.frombuffer(mv, np.uint8, nv * 16, q).reshape(nv, 16)
            uv = np.ascontiguousarray(ntb[:, 12:16]).view("<i2").reshape(nv, 2).astype(np.float32) / 32767 * tsb[:2] + tsb[2:4]
            tot += 1
            if uv.min() < -0.01 or uv.max() > 1.01:
                uv_out += 1
            # stream end vs index buffer
            end = q + nv * 16 + (nv * 4 if st == 2 else 0)
            gaps[("ibo-after-vbo" if ibo >= end else "ibo-inside", )] += 1
print("subtypes", dict(subtypes))
print("props", dict(props))
print("unk0c", dict(unk0c.most_common(6)))
print("unk18", dict(unk18.most_common(6)))
print("aux==0", dict(aux_zero))
print("meshes", tot, "with uv outside [0,1]", uv_out)
print(dict(gaps))
