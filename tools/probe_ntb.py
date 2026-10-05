"""Which 4-byte lane of the 16B NTB+UV block is what? Check unit length / orthogonality / smoothness."""
import sys, struct, pathlib, random

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
files = sorted((ROOT / "Assets/Sorted/chunk0/PRIM").glob("*.PRIM"))
random.seed(2)
sample = random.sample(files, 200)


def unit(b):
    return (b[:, :3].astype(np.float32) - 128) / 127.5


stats = {k: [] for k in ("len0", "len1", "len2", "dot01", "dot02", "dot12", "n_vs_geo")}
uvsmooth = {"lane2": [], "lane3": []}
for f in sample:
    d = f.read_bytes(); mv = memoryview(d)
    (ho,) = struct.unpack_from("<Q", mv, 0)
    _, _, _, flags, _pad, rig, cnt, to = struct.unpack_from("<BBHIIIII", mv, ho)
    for off in struct.unpack_from("<%dI" % cnt, mv, to):
        st = struct.unpack_from("<B", mv, off + 4)[0]
        nv, vbo, ni, _, ibo, _, _ = struct.unpack_from("<7I", mv, off + 44)
        if st not in (0, 1) or nv < 8:
            continue
        ps = np.frombuffer(mv, "<f4", 4, off + 72); pb = np.frombuffer(mv, "<f4", 4, off + 88)
        tsb = np.frombuffer(mv, "<f4", 4, off + 104)
        pos = np.frombuffer(mv, "<i2", nv * 4, vbo).reshape(nv, 4)[:, :3].astype(np.float32) / 32767 * ps[:3] + pb[:3]
        idx = np.frombuffer(mv, "<u2", ni, ibo).astype(np.int64).reshape(-1, 3)
        ntb = np.frombuffer(mv, np.uint8, nv * 16, vbo + nv * 8).reshape(nv, 16)
        l = [unit(ntb[:, 0:4]), unit(ntb[:, 4:8]), unit(ntb[:, 8:12])]
        for i in range(3):
            stats["len%d" % i].append(np.linalg.norm(l[i], axis=1).mean())
        stats["dot01"].append(np.abs((l[0] * l[1]).sum(1)).mean())
        stats["dot02"].append(np.abs((l[0] * l[2]).sum(1)).mean())
        stats["dot12"].append(np.abs((l[1] * l[2]).sum(1)).mean())
        # geometric normal agreement for lane 0
        e1 = pos[idx[:, 1]] - pos[idx[:, 0]]; e2 = pos[idx[:, 2]] - pos[idx[:, 0]]
        gn = np.cross(e1, e2); gl = np.linalg.norm(gn, axis=1, keepdims=True); gl[gl == 0] = 1; gn /= gl
        vn = l[0][idx[:, 0]]
        stats["n_vs_geo"].append(np.abs((gn * vn).sum(1)).mean())
        # uv smoothness along edges: lane2 as int16 pair vs lane3
        for name, sl in (("lane2", slice(8, 12)), ("lane3", slice(12, 16))):
            uv = np.ascontiguousarray(ntb[:, sl]).view("<i2").reshape(nv, 2).astype(np.float32)
            de = np.abs(uv[idx[:, 1]] - uv[idx[:, 0]]).mean()
            uvsmooth[name].append(de / (np.abs(uv).mean() + 1))
for k, v in stats.items():
    print(k, "%.3f" % np.mean(v))
for k, v in uvsmooth.items():
    print(k, "edge-delta/mag %.3f" % np.mean(v))
