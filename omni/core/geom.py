"""Geometry clean-up shared by every target."""
from __future__ import annotations

import numpy as np


def dedupe_overlapping(submeshes) -> int:
    """Remove triangles that sit exactly on a triangle of an earlier submesh with the same facing.

    Two coplanar layers fight for the depth buffer and flicker; back-to-back (opposite facing)
    pairs are legitimate double-sided surfaces and are kept. Returns the number of triangles removed.
    """
    seen: set = set()
    removed = 0
    for sm in submeshes:
        tri = sm.indices.reshape(-1, 3)
        p = sm.positions[tri]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        n = n / ln
        c = np.round(p.mean(1) / 0.002).astype(np.int64)         # 2 mm grid
        q = np.round(n * 3).astype(np.int64)
        keys = np.concatenate([c, q], axis=1)
        mine = [tuple(k) for k in keys]
        keep = np.array([k not in seen for k in mine])
        seen.update(k for k, kp in zip(mine, keep) if kp)
        if not keep.all():
            removed += int((~keep).sum())
            sm.indices = tri[keep].reshape(-1)
    return removed
