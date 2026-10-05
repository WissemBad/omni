"""Collision hulls and volume estimation.

* One convex hull: exact enough for convex-ish props.
* Concave props (tables, shelves, chairs): game meshes are built from separate parts (top + legs ...),
  so one hull per connected part gives a good concave collision in milliseconds.
* CoACD (true convex decomposition) only on request: 10 s to several minutes per model.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull

from ...native import N

MAX_PROP_SIZE = 25.0    # metres; larger objects get no collision model
MAX_HULLS = 16          # studiomdl's default limit is 20 convex pieces; Source physics stays cheap below that


def _weld(subs) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices split at UV/normal seams so that the topology is that of the real surface."""
    pos = np.concatenate([s.positions for s in subs]).astype(np.float64)
    tris, off = [], 0
    for s in subs:
        tris.append(s.indices.reshape(-1, 3).astype(np.int64) + off)
        off += len(s.positions)
    tris = np.concatenate(tris)
    if N is not None:
        return N.weld(np.ascontiguousarray(pos), np.ascontiguousarray(tris))
    key = np.round(pos / 1e-4).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    verts = np.zeros((len(uniq), 3))
    np.add.at(verts, inv, pos)
    verts /= np.bincount(inv, minlength=len(uniq))[:, None]
    tris = inv[tris]
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])]
    return verts, tris


def mesh_volume(verts: np.ndarray, tris: np.ndarray) -> tuple[float, float]:
    """(|signed volume|, fraction of open edges). The volume is only meaningful when the mesh is closed."""
    if len(tris) == 0:
        return 0.0, 1.0
    if N is not None:
        return N.mesh_volume(np.ascontiguousarray(verts, np.float64), np.ascontiguousarray(tris, np.int64))
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    vol = abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return vol, float((counts == 1).sum()) / len(counts)


def convex_hull(points: np.ndarray):
    """Returns (vertices, triangles, volume) of the convex hull, triangles CCW seen from outside."""
    pts = np.unique(np.round(points.astype(np.float64), 5), axis=0)
    if len(pts) < 1:
        return None
    # flat / thin / tiny geometry: pad to a minimum thickness so the hull is never degenerate
    ext = pts.max(0) - pts.min(0)
    if len(pts) < 4 or ext.min() < 0.004:
        lo, hi = pts.min(0), pts.max(0)
        hi = np.maximum(hi, lo + 0.004)
        pts = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    try:
        h = ConvexHull(pts)
    except Exception:
        return None
    verts = pts[h.vertices]
    remap = np.zeros(len(pts), np.int64)
    remap[h.vertices] = np.arange(len(h.vertices))
    tris = remap[h.simplices]
    c = verts.mean(0)
    a, b, d = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    flip = np.einsum("ij,ij->i", np.cross(b - a, d - a), a - c) < 0       # make them CCW seen from outside
    tris[flip] = tris[flip][:, [0, 2, 1]]
    return verts.astype(np.float32), tris, float(h.volume)


def simplify_points(points: np.ndarray, limit: int = 4000) -> np.ndarray:
    if len(points) <= limit:
        return points
    return points[:: len(points) // limit + 1]


def split_parts(verts: np.ndarray, tris: np.ndarray, max_parts: int = MAX_HULLS) -> list:
    """Convex hull per connected part; small parts are attached to the nearest big one."""
    n = len(verts)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]]])
    ncomp, label = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)), directed=False)
    used = np.zeros(n, bool)
    used[tris.ravel()] = True
    comps = [np.flatnonzero((label == c) & used) for c in range(ncomp)]
    comps = [c for c in comps if len(c)]
    if len(comps) < 2:
        return []
    ext = [float(np.ptp(verts[c], axis=0).prod()) for c in comps]   # bbox volume as size proxy
    order = np.argsort(ext)[::-1]
    keep = [comps[i] for i in order[:max_parts]]
    rest = [comps[i] for i in order[max_parts:]]
    centers = np.array([verts[k].mean(0) for k in keep])
    groups = [list(k) for k in keep]
    for r in rest:
        j = int(np.argmin(np.linalg.norm(centers - verts[r].mean(0), axis=1)))
        groups[j].extend(r)
    out = []
    for g in groups:
        h = convex_hull(simplify_points(verts[np.asarray(g)]))
        if h:
            out.append(h)
    return out


def _quat_mat(q) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _sphere_points(n: int = 6) -> np.ndarray:
    th, ph = np.meshgrid(np.linspace(0, np.pi, n + 1), np.linspace(0, 2 * np.pi, 2 * n, endpoint=False))
    return np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], -1).reshape(-1, 3)


def _concave(points: np.ndarray, depth: float = 0.02, share: float = 0.15) -> bool:
    """True when more than ``share`` of the points lie deeper than ``depth`` (metres, or 5 % of the size)
    inside their convex hull."""
    try:
        h = ConvexHull(points)
    except Exception:  # noqa: BLE001 - flat or degenerate
        return False
    d = -(points @ h.equations[:, :3].T + h.equations[:, 3]).min(1)     # distance to the nearest hull plane
    size = float(np.ptp(points, axis=0).max())
    return float((d > max(depth, 0.05 * size)).mean()) > share


def _shape_points(s: dict, budget: list | None = None) -> list[np.ndarray]:
    """Game collision shape -> point clouds (one per convex piece), in the prim frame (metres)."""
    t = s["type"]
    if t == "mesh":
        verts = np.asarray(s["verts"], np.float64)
        tris = np.asarray(s["tris"], np.int64)
        if len(tris) == 0:
            return []
        n = len(verts)
        e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]]])
        ncomp, label = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)),
                                            directed=False)
        out = []
        for c in range(ncomp):
            sel = label == c
            ct = tris[sel[tris[:, 0]]]
            if not sel.any() or len(ct) == 0:
                continue
            comp = verts[sel]
            # a clearly concave part (a chair or a table modelled in one piece) is split by CoACD: cheap here,
            # collision meshes are low-poly. Concave = many vertices well inside the convex hull (works for
            # open meshes too, unlike a volume ratio).
            if budget is not None and budget[0] > 0 and 12 <= len(ct) <= 4000 and _concave(comp):
                budget[0] -= 1
                try:
                    idx = np.flatnonzero(sel)
                    remap = np.full(n, -1)
                    remap[idx] = np.arange(len(idx))
                    pieces = decompose(comp, remap[ct])
                    if pieces:
                        out += [p[0].astype(np.float64) for p in pieces]
                        continue
                except Exception:  # noqa: BLE001 - keep the plain hull
                    pass
            out.append(comp)
        return out
    if t == "convex":
        local = np.asarray(s["points"], np.float64)
    elif t == "box":
        hx, hy, hz = s["half"]
        local = np.array([[x, y, z] for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)])
    elif t == "sphere":
        local = _sphere_points() * s["radius"]
    elif t == "capsule":                    # PhysX capsules lie along their local X axis
        sp = _sphere_points() * s["radius"]
        hh = s["half_height"]
        local = np.concatenate([sp + [hh, 0, 0], sp - [hh, 0, 0]])
    else:
        return []
    return [local @ _quat_mat(s["quat"]).T + np.asarray(s["pos"], np.float64)]


COACD_PER_OBJECT = 2      # CoACD runs per object (~3 s each); further concave parts keep a plain hull


def game_size(coll: dict) -> float:
    """Largest extent (metres) of a game collision, cheap: to skip scenery before any hull work."""
    pts = []
    for s in coll.get("shapes", []):
        if s["type"] == "mesh":
            pts.append(np.asarray(s["verts"], np.float64))
        elif s["type"] == "convex":
            pts.append(np.asarray(s["points"], np.float64) + np.asarray(s["pos"]))
        else:
            pts.append(np.asarray([s["pos"]], np.float64))
    if not pts:
        return 0.0
    p = np.concatenate(pts)
    return float(np.ptp(p, axis=0).max())


def from_game(coll: dict, max_parts: int = MAX_HULLS):
    """The game's own PhysX collision (see native/src/aloc.rs) -> (hulls, solid volume m3). Convex shapes and
    primitives map 1:1 to Source convex pieces; triangle meshes give one piece per connected part. Above
    ``max_parts`` pieces, the smallest are merged into their nearest neighbour."""
    budget = [COACD_PER_OBJECT]
    clouds = [c for s in coll.get("shapes", []) for c in _shape_points(s, budget) if len(c)]
    if not clouds:
        return [], 0.0
    if len(clouds) > max_parts:
        size = [float(np.ptp(c, axis=0).prod()) for c in clouds]
        order = np.argsort(size)[::-1]
        keep = [clouds[i] for i in order[:max_parts]]
        centers = np.array([k.mean(0) for k in keep])
        for i in order[max_parts:]:
            j = int(np.argmin(np.linalg.norm(centers - clouds[i].mean(0), axis=1)))
            keep[j] = np.concatenate([keep[j], clouds[i]])
        clouds = keep
    hulls = [h for h in (convex_hull(simplify_points(c)) for c in clouds) if h]
    return hulls, float(sum(h[2] for h in hulls))


def decompose(verts: np.ndarray, tris: np.ndarray) -> list:
    """CoACD convex decomposition -> [(vertices, triangles, volume)]."""
    import coacd
    coacd.set_log_level("error")
    parts = coacd.run_coacd(
        coacd.Mesh(verts, tris), threshold=0.2, max_convex_hull=8, max_ch_vertex=48,
        preprocess_resolution=25, mcts_iterations=20, mcts_max_depth=2, mcts_nodes=8,
    )
    out = []
    for v, _f in parts:
        h = convex_hull(np.asarray(v))
        if h:
            out.append(h)
    return out


def build_collision(subs, method: str = "parts"):
    """-> (hulls, solid_volume_m3, method used). ``method``: hull | parts | coacd."""
    verts, tris = _weld(subs)
    hull = convex_hull(simplify_points(verts))
    if hull is None:
        return [], 0.0, "none"
    hull_vol = hull[2]
    vol, open_frac = mesh_volume(verts, tris)
    closed = open_frac < 0.02 and 0 < vol <= hull_vol * 1.05
    size = float((verts.max(0) - verts.min(0)).max())
    if size > MAX_PROP_SIZE:
        return [], 0.0, "scenery"          # streets, buildings, terrain: not a prop, and studiomdl chokes on them
    # the single hull is clearly wrong when the shape fills much less of it (concave or open shapes);
    # objects larger than a few metres are scenery, never thrown around: one hull is enough
    concave = ((closed and vol < 0.55 * hull_vol) or not closed) and 0.25 < size < 6.0
    if method != "hull" and concave:
        parts = []
        try:
            parts = split_parts(verts, tris) if method == "parts" else decompose(verts, tris)
        except Exception:
            parts = []
        # only worth it if the pieces really hug the shape better than the single hull
        if len(parts) > 1 and sum(p[2] for p in parts) < 0.8 * hull_vol:
            solid = vol if closed else sum(p[2] for p in parts)
            return parts, solid, method
    solid = vol if closed else hull_vol * 0.5
    return [hull], solid, "hull"
