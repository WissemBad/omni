"""BORG (bone rig) decoder, 007 First Light: bone names, hierarchy and local bind pose."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Rig:
    names: list[str]
    parents: list[int]
    local_t: np.ndarray            # (N,3)  Glacier frame
    local_q: np.ndarray            # (N,4)  x y z w
    bodypart: list[int] = field(default_factory=list)

    def world(self) -> np.ndarray:
        """(N,4,4) bone-to-model matrices of the bind pose (mesh frame: Z up, metres).

        Local poses are stored directly in the mesh frame (the legacy add-on's axis swap and its extra
        -90 degrees on the root cancel out for positions) with inverse (conjugated) rotations; verified:
        with these, thighs/shins/arms are vertical/diagonal as in the mesh and bone origins land inside the
        matching body parts.
        """
        n = len(self.names)
        out = np.zeros((n, 4, 4))
        for i in range(n):
            m = np.eye(4)
            q = self.local_q[i].copy()
            q[:3] *= -1.0                       # stored rotations are inverse (parent-to-child) quaternions
            m[:3, :3] = _qmat(q)
            m[:3, 3] = self.local_t[i]
            p = self.parents[i]
            out[i] = out[p] @ m if p >= 0 else m
        return out


def _qmat(q) -> np.ndarray:
    x, y, z, w = [float(v) for v in q]
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def parse_borg(d: bytes) -> Rig:
    (hdr,) = struct.unpack_from("<Q", d, 0)
    n, _anim, defs_off, bind_off = struct.unpack_from("<4I", d, hdr)
    names, parents, bp = [], [], []
    for i in range(n):
        o = defs_off + i * (12 + 4 + 12 + 34 + 2)
        parent = struct.unpack_from("<i", d, o + 12)[0]
        raw = d[o + 28:o + 62]
        names.append(raw.split(b"\0")[0].decode("utf-8", "replace"))
        parents.append(parent)
        bp.append(struct.unpack_from("<h", d, o + 62)[0])
    bind = np.frombuffer(d, "<f4", n * 8, bind_off).reshape(n, 8)
    return Rig(names, parents, bind[:, 4:7].copy(), bind[:, 0:4].copy(), bp)
