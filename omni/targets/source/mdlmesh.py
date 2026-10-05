"""Read the render mesh (LOD0) of a compiled Source model: .mdl + .vvd + .dx90.vtx.

Only used to compare converted characters with stock GMod player models (reference pose, proportions).
"""
from __future__ import annotations

import struct

import numpy as np


def read_mesh(mdl: bytes, vvd: bytes, vtx: bytes, lod: int = 0):
    """-> (positions (N,3), normals (N,3), uvs (N,2), indices (M,), bone (N,) dominant bone)."""
    # ---- VVD: optional fixup table, then 48-byte vertices
    _id, _ver, _chk, nlods = struct.unpack_from("<4siii", vvd, 0)
    nlodverts = struct.unpack_from("<8i", vvd, 16)
    nfix, fixstart, vstart, tstart = struct.unpack_from("<4i", vvd, 48)
    total = nlodverts[0]
    raw = np.frombuffer(vvd, np.dtype([("w", "<f4", 3), ("b", "u1", 3), ("n", "u1"), ("p", "<f4", 3),
                                       ("nr", "<f4", 3), ("uv", "<f4", 2)]), count=(len(vvd) - vstart) // 48, offset=vstart)
    if nfix:
        sel = []
        for i in range(nfix):
            flod, sv, nv = struct.unpack_from("<3i", vvd, fixstart + i * 12)
            if flod >= lod:
                sel.append(np.arange(sv, sv + nv))
        order = np.concatenate(sel)
    else:
        order = np.arange(total)
    pos = raw["p"][order]
    nrm = raw["nr"][order]
    uv = raw["uv"][order]
    wts = raw["w"][order]
    bidx = raw["b"][order]
    dom = bidx[np.arange(len(order)), wts.argmax(1)].astype(int)

    # ---- MDL: body parts -> models -> meshes (vertexoffset of each mesh inside its model)
    nbp, bpidx = struct.unpack_from("<2i", mdl, 232)
    mesh_vertex_offset = {}          # (bodypart, model, mesh) -> global vertex base
    for b in range(nbp):
        bo = bpidx + b * 16
        _n, nmodels, _base, modelindex = struct.unpack_from("<4i", mdl, bo)
        for m in range(nmodels):
            mo = bo + modelindex + m * 148
            nmeshes, meshindex, nverts, vertexindex = struct.unpack_from("<4i", mdl, mo + 72)
            base = vertexindex // 48                                   # model's first vertex in the VVD
            for k in range(nmeshes):
                so = mo + meshindex + k * 116
                voff = struct.unpack_from("<i", mdl, so + 12)[0]
                mesh_vertex_offset[(b, m, k)] = base + voff

    # ---- VTX (strip groups): indices are local to the strip-group vertex list -> origMeshVertID
    nbp2, bpo = struct.unpack_from("<2i", vtx, 28)
    tris = []
    for b in range(nbp2):
        bo = bpo + b * 8
        nmodels, modelo = struct.unpack_from("<2i", vtx, bo)
        for m in range(nmodels):
            mo = bo + modelo + m * 8
            nl, lodo = struct.unpack_from("<2i", vtx, mo)
            lo = mo + lodo + lod * 12
            nmeshes, meshoff, _sw = struct.unpack_from("<iif", vtx, lo)
            for k in range(nmeshes):
                mso = lo + meshoff + k * 9
                nsg, sgoff, _fl = struct.unpack_from("<iiB", vtx, mso)
                for g in range(nsg):
                    go = mso + sgoff + g * 25
                    nverts, vo, nidx, io, nstrips, stro, _gfl = struct.unpack_from("<6iB", vtx, go)
                    # Vertex = boneWeightIndex[3] numBones origMeshVertID(u16) boneID[3] => 9 bytes
                    vb = np.frombuffer(vtx, np.uint8, nverts * 9, go + vo).reshape(nverts, 9)
                    orig = vb[:, 4].astype(np.int64) | (vb[:, 5].astype(np.int64) << 8)
                    idx = np.frombuffer(vtx, "<u2", nidx, go + io).astype(np.int64)
                    base = mesh_vertex_offset[(b, m, k)]
                    # strips: flags bit0 = trilist, bit1 = tristrip
                    for s in range(nstrips):
                        so = go + stro + s * 27
                        sn, sio, _snv, _svo, _nb, sflags = struct.unpack_from("<4ihB", vtx, so)
                        seg = idx[sio:sio + sn]
                        if sflags & 1:
                            tris.append(base + orig[seg])
                        else:                                          # tristrip -> list
                            t = []
                            for i in range(len(seg) - 2):
                                t += ([seg[i], seg[i + 1], seg[i + 2]] if i % 2 == 0 else [seg[i + 1], seg[i], seg[i + 2]])
                            tris.append(base + orig[np.array(t, np.int64)])
    indices = np.concatenate(tris) if tris else np.zeros(0, np.int64)
    return pos, nrm, uv, indices, dom
