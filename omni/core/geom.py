"""Geometry clean-up shared by every target."""
from __future__ import annotations

import numpy as np


def dedupe_overlapping(submeshes) -> int:
    """Remove triangles that sit exactly on a triangle of an earlier submesh with the same facing.

    Two coplanar layers fight for the depth buffer and flicker; back-to-back (opposite facing)
    pairs are legitimate double-sided surfaces and are kept. Returns the number of triangles removed.
    A triangle is identified by its centroid on a 2 mm grid and its normal rounded to a third; each triangle is
    compared with those *kept* in earlier submeshes (twins inside one submesh are left alone).
    """
    kept = np.empty(0, dtype=np.uint64)
    removed = 0
    for sm in submeshes:
        tri = sm.indices.reshape(-1, 3)
        if not len(tri):
            continue
        p = sm.positions[tri]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        n = n / ln
        c = np.round(p.mean(1) / 0.002).astype(np.int64)
        q = np.round(n * 3).astype(np.int64)
        keys = _hash_rows(np.concatenate([c, q], axis=1))
        keep = ~np.isin(keys, kept) if len(kept) else np.ones(len(keys), bool)
        kept = np.union1d(kept, keys[keep])
        if not keep.all():
            removed += int((~keep).sum())
            sm.indices = tri[keep].reshape(-1)
    return removed


def _hash_rows(rows: np.ndarray) -> np.ndarray:
    """One 64-bit value per row of integers (FNV-style mixing): equal rows give equal hashes."""
    h = np.full(len(rows), 0xCBF29CE484222325, dtype=np.uint64)
    prime = np.uint64(0x100000001B3)
    with np.errstate(over="ignore"):
        for col in rows.astype(np.int64).T:
            h = (h ^ col.astype(np.uint64)) * prime
            h ^= h >> np.uint64(29)
    return h
