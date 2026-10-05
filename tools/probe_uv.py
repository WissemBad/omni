"""Which lane is the real UV0? Compare stored tangent with the tangent derived from each candidate UV lane."""
import struct
import pathlib
import random

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
files = sorted((ROOT / "Assets/Sorted/chunk0/PRIM").glob("*.PRIM"))
random.seed(3)
sample = random.sample(files, 300)


def unit(b):
    return (b[:, :3].astype(np.float32) - 128) / 127.5


res = {}
for f in sample:
    d = f.read_bytes(); mv = memoryview(d)
    (ho,) = struct.unpack_from("<Q", mv, 0)
    _, _, _, flags, _pad, rig, cnt, to = struct.unpack_from("<BBHIIIII", mv, ho)
    for off in struct.unpack_from("<%dI" % cnt, mv, to):
        st = struct.unpack_from("<B", mv, off + 4)[0]
        nv, vbo, ni, _, ibo, _, _ = struct.unpack_from("<7I", mv, off + 44)
        if st not in (0, 1) or nv < 8 or ni < 3:
            continue
        ps = np.frombuffer(mv, "<f4", 4, off + 72); pb = np.frombuffer(mv, "<f4", 4, off + 88)
        tsb = np.frombuffer(mv, "<f4", 4, off + 104)
        pos = np.frombuffer(mv, "<i2", nv * 4, vbo).reshape(nv, 4)[:, :3].astype(np.float32) / 32767 * ps[:3] + pb[:3]
        idx = np.frombuffer(mv, "<u2", ni, ibo).astype(np.int64).reshape(-1, 3)
        ntb = np.frombuffer(mv, np.uint8, nv * 16, vbo + nv * 8).reshape(nv, 16)
        lanes = [unit(ntb[:, 4 * i:4 * i + 4]) for i in range(3)]
        for name, sl in (("lane2", slice(8, 12)), ("lane3", slice(12, 16))):
            uv = np.ascontiguousarray(ntb[:, sl]).view("<i2").reshape(nv, 2).astype(np.float32) / 32767
            e1 = pos[idx[:, 1]] - pos[idx[:, 0]]; e2 = pos[idx[:, 2]] - pos[idx[:, 0]]
            d1 = uv[idx[:, 1]] - uv[idx[:, 0]]; d2 = uv[idx[:, 2]] - uv[idx[:, 0]]
            det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
            ok = np.abs(det) > 1e-9
            if ok.sum() < 4:
                continue
            tu = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2])[ok] / det[ok, None]
            tv = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1])[ok] / det[ok, None]
            for tn, tvec in (("U", tu), ("V", tv)):
                tvec = tvec / np.maximum(np.linalg.norm(tvec, axis=1, keepdims=True), 1e-12)
                for ln in (0, 1, 2):
                    sv = lanes[ln][idx[ok, 0]]
                    c = np.abs((tvec * sv).sum(1)).mean()
                    res.setdefault((name, tn, "storedlane%d" % ln), []).append(c)
for k in sorted(res):
    print(k, "%.3f" % np.mean(res[k]), len(res[k]))
