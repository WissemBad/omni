"""Read back a VTF written by this tool (DXT1/DXT1A/DXT3/DXT5/BGRA8888): used to preview the real output."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import texture2ddecoder as t2d


def read_vtf(path: Path, max_dim: int = 1024) -> np.ndarray:
    """RGBA of the largest mip not exceeding ``max_dim``."""
    b = path.read_bytes()
    w, h = struct.unpack_from("<HH", b, 16)
    fmt = struct.unpack_from("<I", b, 52)[0]
    nmips = b[56]
    bpp = {13: 0.5, 20: 0.5, 14: 1.0, 15: 1.0, 12: 4.0}[fmt]
    # data starts at the header end, smallest mip first
    sizes = []
    for i in range(nmips):
        mw, mh = max(1, w >> i), max(1, h >> i)
        n = max(1, (mw + 3) // 4) * max(1, (mh + 3) // 4) * (8 if bpp == 0.5 else 16) if fmt != 12 else mw * mh * 4
        sizes.append((mw, mh, n))
    off = struct.unpack_from("<I", b, 12)[0]
    pos = {}
    for (mw, mh, n) in reversed(sizes):
        pos[(mw, mh)] = (off, n)
        off += n
    cand = [s for s in sizes if max(s[0], s[1]) <= max_dim] or [sizes[-1]]
    mw, mh, n = cand[0]
    o, n = pos[(mw, mh)]
    data = b[o:o + n]
    if fmt == 12:
        a = np.frombuffer(data, np.uint8).reshape(mh, mw, 4)
        return a[..., [2, 1, 0, 3]].copy()
    fn = {13: t2d.decode_bc1, 20: t2d.decode_bc1, 14: t2d.decode_bc3, 15: t2d.decode_bc3}[fmt]
    a = np.frombuffer(fn(data, mw, mh), np.uint8).reshape(mh, mw, 4)
    return a[..., [2, 1, 0, 3]].copy()
