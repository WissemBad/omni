"""SMD (Studiomdl Data) writers: static reference mesh and physics hull."""
from __future__ import annotations

from pathlib import Path

import numpy as np

HEADER = "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\ntriangles\n"


def _fmt_rows(a: np.ndarray) -> list[str]:
    return ["0 %.5f %.5f %.5f %.5f %.5f %.5f %.5f %.5f" % tuple(r) for r in a]


def write_reference(path: Path, submeshes, material_names: dict[str, str], scale: float) -> int:
    """Triangles in the file's own winding (verified in game: reversing them shows inside-out models)."""
    from ...native import N
    if N is not None and hasattr(N, "smd_static"):
        lines, count = [HEADER], 0
        for sm in submeshes:
            uv = np.ascontiguousarray(sm.uvs, dtype=np.float64).copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            text, n = N.smd_static(material_names.get(sm.material_key, "default"),
                                   np.ascontiguousarray(sm.positions * scale, dtype=np.float64),
                                   np.ascontiguousarray(sm.normals, dtype=np.float64), uv,
                                   np.ascontiguousarray(sm.indices, dtype=np.int64))
            lines.append(text)
            count += n
        lines.append("end\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines), encoding="ascii")
        return count
    lines = [HEADER]
    count = 0
    for sm in submeshes:
        idx = sm.indices
        pos = sm.positions[idx] * scale
        nrm = sm.normals[idx]
        uv = sm.uvs[idx].copy()
        uv[:, 1] = 1.0 - uv[:, 1]
        rows = np.concatenate([pos, nrm, uv], axis=1)
        mat = material_names.get(sm.material_key, "default")
        for t in range(0, len(rows), 3):
            lines.append(mat + "\n")
            lines.append("\n".join(_fmt_rows(rows[t:t + 3])) + "\n")
        count += len(idx) // 3
    lines.append("end\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="ascii")
    return count


def write_hulls(path: Path, hulls: list[tuple[np.ndarray, np.ndarray]], scale: float) -> None:
    """Each hull is (vertices (N,3), triangles (M,3)); every hull becomes one convex piece (own material name)."""
    lines = [HEADER]
    for i, (v, f) in enumerate(hulls):
        v = v * scale
        for tri in f:
            lines.append("phys\n")
            for k in tri:
                lines.append("0 %.5f %.5f %.5f 0 0 1 0 0\n" % tuple(v[k]))
    lines.append("end\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="ascii")
