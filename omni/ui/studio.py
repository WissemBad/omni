"""Readers for the compiled Source files omni writes (.mdl .vvd .vtx .phy .vmt .vtf): the data behind the output
inspector of the web interface.  Everything here is read-only and only looks at files, never at the game.

Coordinates: Source is Z-up in inches. ``to_view`` converts to what the 3D viewer shows (Y-up, metres), the same
mapping ``preview/glb.py`` applies, so overlays (bones, hitboxes...) line up with the GLB meshes.
"""
from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..targets.source.mdlread import read_mdl, world_transforms

UNITS_PER_METER = 39.37

HITGROUP = {0: "générique", 1: "tête", 2: "torse", 3: "ventre", 4: "bras gauche", 5: "bras droit",
            6: "jambe gauche", 7: "jambe droite", 8: "équipement"}
MDL_FLAGS = {0x1: "hitbox auto", 0x2: "envmap", 0x4: "opaque forcé", 0x8: "translucide 2 passes", 0x10: "prop statique",
             0x20: "framebuffer", 0x80: "bump", 0x10000: "sans ombre"}


def to_view(p) -> np.ndarray:
    """Source (Z up, inches) -> viewer (Y up, metres)."""
    p = np.asarray(p, np.float64)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], axis=-1) / UNITS_PER_METER


def _cstr(d: bytes, off: int) -> str:
    end = d.find(b"\0", off)
    return d[off:end if end >= 0 else off + 64].decode("latin-1")


def _corners(lo, hi) -> np.ndarray:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


# ------------------------------------------------------------------------------------------------------ .mdl
def parse_mdl(d: bytes) -> dict:
    """Everything the inspector shows about a compiled model, as plain JSON-able data."""
    info = read_mdl(d)
    ver, chk = struct.unpack_from("<2i", d, 4)
    (length,) = struct.unpack_from("<i", d, 76)
    (flags,) = struct.unpack_from("<i", d, 152)
    mass, contents = struct.unpack_from("<fi", d, 328)
    ntex, texidx = struct.unpack_from("<2i", d, 204)
    ncd, cdidx = struct.unpack_from("<2i", d, 212)
    nref, nfam, skinidx = struct.unpack_from("<3i", d, 220)
    nbp, bpidx = struct.unpack_from("<2i", d, 232)
    nseq, seqidx = struct.unpack_from("<2i", d, 188)
    nanim, animidx = struct.unpack_from("<2i", d, 180)
    npp, ppidx = struct.unpack_from("<2i", d, 300)
    nhb, hbidx = struct.unpack_from("<2i", d, 172)

    textures = [_cstr(d, texidx + i * 64 + struct.unpack_from("<i", d, texidx + i * 64)[0]) for i in range(ntex)]
    cdtextures = [_cstr(d, struct.unpack_from("<i", d, cdidx + i * 4)[0]).replace("\\", "/").strip("/")
                  for i in range(ncd)]
    skins = (np.frombuffer(d, "<i2", nfam * nref, skinidx).reshape(nfam, nref).astype(int).tolist()
             if nfam and nref else [])

    bodyparts = []
    for b in range(nbp):
        bo = bpidx + b * 16
        nameidx, nmodels, base, modelindex = struct.unpack_from("<4i", d, bo)
        models = []
        for m in range(nmodels):
            mo = bo + modelindex + m * 148
            nmeshes, meshindex, nverts, _vi = struct.unpack_from("<4i", d, mo + 72)
            meshes = []
            for k in range(nmeshes):
                so = mo + meshindex + k * 116
                skinref, _mi, mv, _voff = struct.unpack_from("<4i", d, so)
                meshes.append({"skinref": skinref, "vertices": mv})
            models.append({"name": _cstr(d, mo), "vertices": nverts, "meshes": meshes})
        bodyparts.append({"name": _cstr(d, bo + nameidx), "base": base, "models": models})

    anims = []
    for i in range(nanim):
        fps, _fl, frames = struct.unpack_from("<fii", d, animidx + i * 100 + 8)
        anims.append({"fps": fps, "frames": frames})
    sequences = []
    for i in range(nseq):
        o = seqidx + i * 212
        try:
            labelidx, actidx, sflags, _activity = struct.unpack_from("<4i", d, o + 4)
            nevents = struct.unpack_from("<i", d, o + 24)[0]
            nblends, aiidx = struct.unpack_from("<2i", d, o + 56)
            a = struct.unpack_from("<h", d, o + aiidx)[0] if nblends else -1
            an = anims[a] if 0 <= a < len(anims) else {"fps": 0, "frames": 0}
            sequences.append({"name": _cstr(d, o + labelidx), "activity": _cstr(d, o + actidx) if actidx else "",
                              "loop": bool(sflags & 1), "frames": an["frames"], "fps": round(an["fps"], 2),
                              "blends": nblends, "events": nevents})
        except (struct.error, ValueError):
            break

    poseparams = []
    for i in range(npp):
        o = ppidx + i * 20
        nameidx, _fl, lo, hi, _loop = struct.unpack_from("<2i3f", d, o)
        poseparams.append({"name": _cstr(d, o + nameidx), "min": round(lo, 3), "max": round(hi, 3)})

    world = world_transforms(info.bones)
    hitboxes = []
    if nhb:
        _n, nbox, boxidx = struct.unpack_from("<3i", d, hbidx)
        for j in range(nbox):
            o = hbidx + boxidx + j * 68
            bone, group = struct.unpack_from("<2i", d, o)
            nameidx = struct.unpack_from("<i", d, o + 32)[0]
            lo = struct.unpack_from("<3f", d, o + 8)
            hi = struct.unpack_from("<3f", d, o + 20)
            m = world[bone] if bone < len(world) else np.eye(4)
            hitboxes.append({"bone": bone, "group": group, "name": _cstr(d, o + nameidx) if nameidx else "",
                             "size": [round((hi[i] - lo[i]) / UNITS_PER_METER * 100, 1) for i in range(3)],
                             "corners": to_view(_corners(lo, hi) @ m[:3, :3].T + m[:3, 3]).round(5).tolist()})

    bones = [{"name": b.name, "parent": b.parent, "flags": b.flags, "pos": to_view(w[:3, 3]).round(5).tolist()}
             for b, w in zip(info.bones, world)]
    attachments = []
    for a in info.attachments:
        w = world[a.bone] @ np.vstack([a.local, [0, 0, 0, 1]])
        o = w[:3, 3]
        attachments.append({"name": a.name, "bone": a.bone, "origin": to_view(o).round(5).tolist(),
                            "axes": [to_view(o + w[:3, i] * 2.0).round(5).tolist() for i in range(3)]})

    return {
        "name": info.name, "version": ver, "checksum": f"{chk & 0xFFFFFFFF:08X}", "length": length,
        "mass": round(mass, 2), "contents": contents, "surfaceprop": info.surfaceprop,
        "flags": [n for bit, n in MDL_FLAGS.items() if flags & bit],
        "hull": [list(map(float, info.hull_min)), list(map(float, info.hull_max))],
        "view": [list(map(float, info.view_bbmin)), list(map(float, info.view_bbmax))],
        "eye": to_view(info.eye_position).round(5).tolist(),
        "hull_box": to_view(_corners(info.hull_min, info.hull_max)).round(5).tolist(),
        "view_box": to_view(_corners(info.view_bbmin, info.view_bbmax)).round(5).tolist(),
        "bones": bones, "bodyparts": bodyparts, "textures": textures, "cdtextures": cdtextures, "skins": skins,
        "skinrefs": nref, "sequences": sequences, "poseparams": poseparams, "hitboxes": hitboxes,
        "attachments": attachments, "includes": info.includes,
        "ikchains": [{"name": n, "bones": [info.bones[b].name for b in bl if b < len(info.bones)]}
                     for n, bl, _ in info.ikchains],
        "ikautoplay": len(info.iklocks),
    }


# ------------------------------------------------------------------------------------------- .vvd / .vtx
@dataclass
class Geometry:
    pos: np.ndarray            # (N,3) Source units
    nrm: np.ndarray
    uv: np.ndarray
    meshes: list[dict]         # {b, m, k, skinref, idx: flat triangle list of global vertex ids}


def vtx_lods(vtx: bytes) -> tuple[int, list[float]]:
    """Number of LODs and their switch points (distance metric, as compiled by $lod)."""
    lods = struct.unpack_from("<i", vtx, 20)[0]
    sw: list[float] = []
    nbp, bpo = struct.unpack_from("<2i", vtx, 28)
    if nbp:
        nmodels, modelo = struct.unpack_from("<2i", vtx, bpo)
        if nmodels:
            nl, lodo = struct.unpack_from("<2i", vtx, bpo + modelo)
            sw = [struct.unpack_from("<iif", vtx, bpo + modelo + lodo + i * 12)[2] for i in range(nl)]
    return lods, sw


def _vtx_meshes(vtx: bytes, lod: int):
    """Yields (bodypart, model, mesh, flat triangle list of mesh-local vertex ids) for one LOD."""
    nbp, bpo = struct.unpack_from("<2i", vtx, 28)
    for b in range(nbp):
        bo = bpo + b * 8
        nmodels, modelo = struct.unpack_from("<2i", vtx, bo)
        for m in range(nmodels):
            mo = bo + modelo + m * 8
            nl, lodo = struct.unpack_from("<2i", vtx, mo)
            if lod >= nl:
                continue
            lo = mo + lodo + lod * 12
            nmeshes, meshoff, _sw = struct.unpack_from("<iif", vtx, lo)
            for k in range(nmeshes):
                mso = lo + meshoff + k * 9
                nsg, sgoff, _fl = struct.unpack_from("<iiB", vtx, mso)
                tris = []
                for g in range(nsg):
                    go = mso + sgoff + g * 25
                    nverts, vo, nidx, io_, nstrips, stro, _gfl = struct.unpack_from("<6iB", vtx, go)
                    vb = np.frombuffer(vtx, np.uint8, nverts * 9, go + vo).reshape(nverts, 9)
                    orig = vb[:, 4].astype(np.int64) | (vb[:, 5].astype(np.int64) << 8)
                    idx = np.frombuffer(vtx, "<u2", nidx, go + io_).astype(np.int64)
                    for s in range(nstrips):
                        so = go + stro + s * 27
                        sn, sio, _snv, _svo, _nb, sflags = struct.unpack_from("<4ihB", vtx, so)
                        seg = idx[sio:sio + sn]
                        if sflags & 1:                                  # triangle list
                            tris.append(orig[seg])
                        elif sn >= 3:                                   # triangle strip -> list
                            i = np.arange(sn - 2)
                            odd = (i % 2).astype(bool)
                            t = np.stack([np.where(odd, seg[i + 1], seg[i]), np.where(odd, seg[i], seg[i + 1]),
                                          seg[i + 2]], axis=1).reshape(-1)
                            tris.append(orig[t])
                yield b, m, k, (np.concatenate(tris) if tris else np.zeros(0, np.int64))


def read_geometry(mdl: bytes, vvd: bytes, vtx: bytes, lod: int = 0) -> Geometry:
    """Vertices (with the .vvd fixups of the LOD) and the triangles of every mesh, in the glTF winding."""
    _id, _ver, _chk, _nlods = struct.unpack_from("<4siii", vvd, 0)
    nlodverts = struct.unpack_from("<8i", vvd, 16)
    nfix, fixstart, vstart, _tstart = struct.unpack_from("<4i", vvd, 48)
    raw = np.frombuffer(vvd, np.dtype([("w", "<f4", 3), ("b", "u1", 4), ("p", "<f4", 3), ("nr", "<f4", 3),
                                       ("uv", "<f4", 2)]), count=(len(vvd) - vstart) // 48, offset=vstart)
    if nfix:
        sel = []
        for i in range(nfix):
            flod, sv, nv = struct.unpack_from("<3i", vvd, fixstart + i * 12)
            if flod >= lod:
                sel.append(np.arange(sv, sv + nv))
        order = np.concatenate(sel)
    else:
        order = np.arange(nlodverts[0])
    pos, nrm, uv = raw["p"][order].astype(np.float64), raw["nr"][order].astype(np.float64), raw["uv"][order]

    nbp, bpidx = struct.unpack_from("<2i", mdl, 232)
    offset: dict[tuple, tuple[int, int]] = {}
    for b in range(nbp):
        bo = bpidx + b * 16
        _n, nmodels, _base, modelindex = struct.unpack_from("<4i", mdl, bo)
        for m in range(nmodels):
            mo = bo + modelindex + m * 148
            nmeshes, meshindex, _nv, vertexindex = struct.unpack_from("<4i", mdl, mo + 72)
            for k in range(nmeshes):
                so = mo + meshindex + k * 116
                skinref, _mi, _mv, voff = struct.unpack_from("<4i", mdl, so)
                offset[(b, m, k)] = (vertexindex // 48 + voff, skinref)

    meshes = []
    for b, m, k, local in _vtx_meshes(vtx, lod):
        if not len(local) or (b, m, k) not in offset:
            continue
        base, skinref = offset[(b, m, k)]
        idx = base + local
        idx = idx[: len(idx) // 3 * 3]
        if not len(idx) or idx.max() >= len(pos):
            continue
        meshes.append({"b": b, "m": m, "k": k, "skinref": skinref, "idx": idx})

    # Source winds triangles the other way round: flip them when the geometry disagrees with the vertex normals
    agree = disagree = 0
    for me in meshes[:40]:
        t = me["idx"][:3000].reshape(-1, 3)
        fn = np.cross(pos[t[:, 1]] - pos[t[:, 0]], pos[t[:, 2]] - pos[t[:, 0]])
        vn = nrm[t].sum(1)
        s = (fn * vn).sum(1)
        agree += int((s > 0).sum())
        disagree += int((s < 0).sum())
    if disagree > agree:
        for me in meshes:
            me["idx"] = me["idx"].reshape(-1, 3)[:, ::-1].reshape(-1)
    return Geometry(pos, nrm, uv, meshes)


# ---------------------------------------------------------------------------------------------------- .vmt
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|([^\s"{}]+)')


def parse_vmt(text: str) -> tuple[str, dict, dict]:
    """-> (shader, parameters, nested blocks such as Proxies); keys are lower-cased."""
    toks = []
    for m in _TOKEN.finditer(text):
        t = m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3))
        if t is not None:
            toks.append(t)
    if not toks:
        return "", {}, {}
    pos = 2 if len(toks) > 1 and toks[1] == "{" else 1

    def block() -> tuple[dict, dict]:
        nonlocal pos
        params, blocks = {}, {}
        while pos < len(toks) and toks[pos] != "}":
            key = toks[pos].lower()
            pos += 1
            if pos < len(toks) and toks[pos] == "{":
                pos += 1
                blocks[key] = block()[0]
                pos += 1
            elif pos < len(toks):
                params[key] = toks[pos]
                pos += 1
        return params, blocks

    params, blocks = block()
    return toks[0], params, blocks


# ---------------------------------------------------------------------------------------------------- .vtf
VTF_FORMAT = {0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888", 4: "RGB565", 5: "I8", 6: "IA88", 7: "P8",
              8: "A8", 11: "ARGB8888", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5", 16: "BGRX8888",
              17: "BGR565", 18: "BGRX5551", 19: "BGRA4444", 20: "DXT1A", 21: "BGRA5551", 22: "UV88",
              23: "UVWQ8888", 24: "RGBA16161616F", 25: "RGBA16161616", 26: "UVLX8888"}
VTF_FLAGS = {0x1: "pointsample", 0x2: "trilinear", 0x4: "clamp S", 0x8: "clamp T", 0x10: "anisotropic",
             0x80: "normal", 0x100: "sans mip", 0x200: "sans LOD", 0x1000: "alpha 1 bit", 0x2000: "alpha 8 bits",
             0x4000: "envmap", 0x8000: "render target"}
_BPP = {0: 4, 1: 4, 2: 3, 3: 3, 4: 2, 5: 1, 6: 2, 7: 1, 8: 1, 11: 4, 12: 4, 16: 4, 17: 2, 18: 2, 19: 2, 21: 2,
        22: 2, 23: 4, 24: 8, 25: 8, 26: 4}


def _mip_bytes(fmt: int, w: int, h: int) -> int:
    if fmt in (13, 20):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if fmt in (14, 15):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    return w * h * _BPP[fmt]


def vtf_info(path: Path) -> dict:
    with open(path, "rb") as f:
        b = f.read(80)
    if b[:4] != b"VTF\0":
        raise ValueError("not a VTF")
    major, minor = struct.unpack_from("<2I", b, 4)
    w, h, flags, frames = struct.unpack_from("<HHIH", b, 16)
    refl = struct.unpack_from("<3f", b, 32)
    fmt = struct.unpack_from("<i", b, 52)[0]
    return {"width": w, "height": h, "format": VTF_FORMAT.get(fmt, f"#{fmt}"), "mips": b[56], "frames": frames,
            "version": f"{major}.{minor}", "flags": [n for bit, n in VTF_FLAGS.items() if flags & bit],
            "reflectivity": [round(x, 3) for x in refl], "bytes": path.stat().st_size,
            "alpha": fmt in (14, 15, 20, 12, 0, 1, 11, 19, 21, 6, 8, 24, 25)}


def decode_vtf(path: Path, max_dim: int = 1024) -> np.ndarray:
    """RGBA uint8 of the largest mip not exceeding ``max_dim`` (first frame). Rust core when available."""
    from ..native import N
    b = path.read_bytes()
    try:
        return N.decode_vtf(b, max_dim)
    except ValueError:
        pass                                       # an uncommon raw format: the Python reader below
    if b[:4] != b"VTF\0":
        raise ValueError("not a VTF")
    hdr = struct.unpack_from("<I", b, 12)[0]
    w, h, flags, frames = struct.unpack_from("<HHIH", b, 16)
    fmt = struct.unpack_from("<i", b, 52)[0]
    nmips = b[56]
    lowfmt = struct.unpack_from("<i", b, 57)[0]
    lw, lh = b[61], b[62]
    off = hdr + (_mip_bytes(lowfmt, lw, lh) if lowfmt >= 0 and lw and lh else 0)
    faces = 6 if flags & 0x4000 else 1
    sizes = [(max(1, w >> i), max(1, h >> i)) for i in range(nmips)]
    start: dict[int, int] = {}
    for i in reversed(range(nmips)):                       # smallest mip first on disk
        start[i] = off
        off += _mip_bytes(fmt, *sizes[i]) * frames * faces
    level = next((i for i, s in enumerate(sizes) if max(s) <= max_dim), nmips - 1)
    mw, mh = sizes[level]
    n = _mip_bytes(fmt, mw, mh)
    data = b[start[level]:start[level] + n]
    if len(data) < n:
        raise ValueError("truncated VTF")
    if fmt in (13, 14, 15, 20):
        raise ValueError("unreadable DXT data")
    else:
        raw = np.frombuffer(data, np.uint8)
        if fmt == 12:
            a = raw.reshape(mh, mw, 4)[..., [2, 1, 0, 3]]
        elif fmt == 0:
            a = raw.reshape(mh, mw, 4)
        elif fmt == 1:
            a = raw.reshape(mh, mw, 4)[..., [3, 2, 1, 0]]
        elif fmt == 11:
            a = raw.reshape(mh, mw, 4)[..., [1, 2, 3, 0]]
        elif fmt == 16:
            a = raw.reshape(mh, mw, 4)[..., [2, 1, 0, 0]].copy()
            a[..., 3] = 255
        elif fmt == 2:
            a = np.dstack([raw.reshape(mh, mw, 3), np.full((mh, mw), 255, np.uint8)])
        elif fmt == 3:
            a = np.dstack([raw.reshape(mh, mw, 3)[..., ::-1], np.full((mh, mw), 255, np.uint8)])
        elif fmt == 5:
            g = raw.reshape(mh, mw)
            a = np.dstack([g, g, g, np.full_like(g, 255)])
        elif fmt == 6:
            g = raw.reshape(mh, mw, 2)
            a = np.dstack([g[..., 0], g[..., 0], g[..., 0], g[..., 1]])
        elif fmt == 8:
            g = raw.reshape(mh, mw)
            a = np.dstack([np.full_like(g, 255), np.full_like(g, 255), np.full_like(g, 255), g])
        elif fmt == 22:
            g = raw.reshape(mh, mw, 2)
            a = np.dstack([g[..., 0], g[..., 1], np.full((mh, mw), 255, np.uint8), np.full((mh, mw), 255, np.uint8)])
        else:
            raise ValueError(f"unsupported VTF format {VTF_FORMAT.get(fmt, fmt)}")
    return np.ascontiguousarray(a)


def channel_view(rgba: np.ndarray, channel: str) -> np.ndarray:
    """RGBA to show for ``rgb`` (opaque colour), ``rgba``, or one channel as grey."""
    if channel == "rgba":
        return rgba
    if channel == "rgb":
        out = rgba.copy()
        out[..., 3] = 255
        return out
    i = "rgba".index(channel)
    g = rgba[..., i]
    return np.dstack([g, g, g, np.full_like(g, 255)])


def png_bytes(rgba: np.ndarray) -> bytes:
    from ..native import N
    return N.png_rgba(np.ascontiguousarray(rgba, np.uint8), "rgba", 0)


def uv_overlay(geo: Geometry, skinref: int, size: int) -> bytes:
    """Transparent PNG with the UV wireframe of every mesh that uses ``skinref``."""
    from PIL import ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    for me in geo.meshes:
        if me["skinref"] != skinref:
            continue
        t = me["idx"].reshape(-1, 3)
        if len(t) > 60000:
            t = t[:: len(t) // 60000 + 1]
        uv = geo.uv[t].astype(np.float64)
        uv[..., 0] = (uv[..., 0] % 1.0 if uv[..., 0].max() <= 1.0001 else uv[..., 0]) * (size - 1)
        uv[..., 1] = uv[..., 1] * (size - 1)
        for tri in uv:
            dr.line([tuple(tri[0]), tuple(tri[1]), tuple(tri[2]), tuple(tri[0])], fill=(80, 220, 255, 190), width=1)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------- .phy
def read_phy(d: bytes) -> dict | None:
    """Collision pieces of a .phy (IVP compact surfaces): triangles in Source units, in the local space of the
    piece's bone (``phy_pieces_to_view`` places them), + the solids' text header."""
    try:
        hsize, _id, nsolids, _chk = struct.unpack_from("<4i", d, 0)
        pos = hsize
        pieces = []
        for _ in range(nsolids):
            (ssize,) = struct.unpack_from("<i", d, pos)
            base = pos + 4
            # compact surface header (vphysics): id, version, modeltype, surfacesize, drag axis areas, axis map
            ivp = base + 28
            root = ivp + struct.unpack_from("<i", d, ivp + 32)[0]
            tris: list[np.ndarray] = []

            def walk(node: int):
                right, ledge = struct.unpack_from("<2i", d, node)
                if right == 0:
                    lo = node + ledge
                    point_off, _spacer, packed, ntri, _pad = struct.unpack_from("<iiIhh", d, lo)
                    parr = lo + point_off
                    for t in range(ntri):
                        to = lo + 16 + t * 16
                        e = [struct.unpack_from("<I", d, to + 4 + i * 4)[0] & 0xFFFF for i in range(3)]
                        tri = np.array([struct.unpack_from("<3f", d, parr + e_ * 16) for e_ in e])
                        tris.append(tri)
                else:
                    walk(node + 28)
                    walk(node + right)

            walk(root)
            if tris:
                v = np.array(tris).reshape(-1, 3)
                # IVP points are metres; checked against the mesh bounds: source = (x, z, -y)
                pieces.append(np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1) * UNITS_PER_METER)
            pos = base + ssize
        text = d[pos:].split(b"\0")[0].decode("latin-1", "replace") if pos < len(d) else ""
        return {"pieces": pieces, "text": text}
    except (struct.error, IndexError, ValueError, RecursionError):
        return None


def phy_pieces_to_view(mdl: bytes, pieces: list[np.ndarray]) -> list[np.ndarray]:
    """Collision pieces (Source units, bone space) to viewer space: a ragdoll's piece i belongs to the bone whose
    ``physicsbone`` is i; a prop has a single bone at the origin."""
    info = read_mdl(mdl)
    world = world_transforms(info.bones) if info.bones else []
    out = []
    for i, p in enumerate(pieces):
        bone = next((j for j, b in enumerate(info.bones) if b.physics_bone == i), 0)
        m = world[bone] if bone < len(world) else np.eye(4)
        out.append(to_view(p @ m[:3, :3].T + m[:3, 3]).astype(np.float32))
    return out
