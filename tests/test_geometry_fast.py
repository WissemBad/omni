"""The fast paths (Rust SMD writer, vectorised dedupe) give exactly what the straightforward versions gave."""
from __future__ import annotations

import types

import numpy as np
import pytest

from omni.core import geom
from omni.native import N
from omni.targets.source import smd


def reference_write(submeshes, names, scale):
    lines = [smd.HEADER]
    for sm in submeshes:
        idx = sm.indices
        pos = sm.positions[idx] * scale
        uv = sm.uvs[idx].copy()
        uv[:, 1] = 1.0 - uv[:, 1]
        rows = np.concatenate([pos, sm.normals[idx], uv], axis=1)
        mat = names.get(sm.material_key, "default")
        for t in range(0, len(rows), 3):
            lines.append(mat + "\n")
            lines.append("\n".join("0 %.5f %.5f %.5f %.5f %.5f %.5f %.5f %.5f" % tuple(r) for r in rows[t:t + 3]) + "\n")
    lines.append("end\n")
    return "".join(lines)


def reference_dedupe(submeshes):
    seen, removed = set(), 0
    for sm in submeshes:
        tri = sm.indices.reshape(-1, 3)
        p = sm.positions[tri]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        n = n / ln
        c = np.round(p.mean(1) / 0.002).astype(np.int64)
        q = np.round(n * 3).astype(np.int64)
        mine = [tuple(k) for k in np.concatenate([c, q], axis=1)]
        keep = np.array([k not in seen for k in mine])
        seen.update(k for k, kp in zip(mine, keep) if kp)
        if not keep.all():
            removed += int((~keep).sum())
            sm.indices = tri[keep].reshape(-1)
    return removed


def mesh(rng, n_vertices=400, n_tris=700, key="m"):
    return types.SimpleNamespace(
        positions=rng.normal(size=(n_vertices, 3)), normals=rng.normal(size=(n_vertices, 3)), uvs=rng.random((n_vertices, 2)),
        indices=rng.integers(0, n_vertices, n_tris * 3), material_key=key)


@pytest.mark.skipif(N is None or not hasattr(N, "smd_static"), reason="native module without smd_static")
def test_rust_smd_writer_is_byte_identical(tmp_path):
    rng = np.random.default_rng(1)
    subs = [mesh(rng, key="a"), mesh(rng, 50, 30, key="b")]
    names = {"a": "mat_a", "b": "mat_b"}
    n = smd.write_reference(tmp_path / "ref.smd", subs, names, 39.37)
    assert n == 730
    assert (tmp_path / "ref.smd").read_text() == reference_write(subs, names, 39.37)


def clone(subs):
    return [types.SimpleNamespace(**vars(s)) for s in subs]


def test_dedupe_matches_the_reference_on_overlapping_layers():
    rng = np.random.default_rng(2)
    base = mesh(rng, 300, 500)
    twin = types.SimpleNamespace(positions=base.positions, normals=base.normals, uvs=base.uvs,
                                 indices=np.concatenate([base.indices[:600], rng.integers(0, 300, 300)]), material_key="b")
    inside = types.SimpleNamespace(positions=base.positions, normals=base.normals, uvs=base.uvs,
                                   indices=np.tile(base.indices[:9], 2), material_key="c")      # twins inside one submesh stay
    a, b = clone([base, twin, inside]), clone([base, twin, inside])
    assert geom.dedupe_overlapping(a) == reference_dedupe(b) > 0
    for x, y in zip(a, b):
        assert np.array_equal(x.indices, y.indices)


def test_dedupe_handles_empty_submeshes():
    empty = types.SimpleNamespace(positions=np.zeros((3, 3)), indices=np.zeros(0, dtype=np.int64))
    assert geom.dedupe_overlapping([empty]) == 0
