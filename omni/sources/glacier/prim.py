"""Vectorised decoder for Glacier 2 RenderPrimitive (.PRIM), 007 First Light layout.

Layout (cross-checked with the reference Blender add-on; every vertex stream
is read with one numpy call instead of a Python loop):

  file[0:8]  offset of the RenderPrimitive header
  header     PRIM_HEADER(4) flags(4) pad(4) boneRig(4) count(4) tableOff(4) min(12) max(12)
  (007: byte +5 is the LOD bitmask, byte +6 the property flags -- the reverse of Hitman)
  mesh       PRIM_OBJECT(44) + 7*u32 submesh fields + posScale/posBias/uvScaleBias (3*16)
             + clothId(4) [+ 5*u32 weighted trailer]
  vertices   int16x4 positions, then (weighted) 8B stream, then NTB+UV (16B) per vertex.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

SUB_STANDARD, SUB_LINKED, SUB_WEIGHTED = 0, 1, 2


@dataclass
class PrimMeshData:
    sub_type: int
    properties: int
    lod_mask: int
    zbias: int
    material_id: int
    bbox_min: tuple
    bbox_max: tuple
    positions: np.ndarray
    pos_w: np.ndarray             # raw 4th int16 lane (4th bone index on weighted meshes)
    normals: np.ndarray
    tangents: np.ndarray
    uvs: np.ndarray
    colors: np.ndarray | None
    indices: np.ndarray
    joints: np.ndarray | None = None
    weights: np.ndarray | None = None


@dataclass
class PrimData:
    flags: int
    bone_rig_index: int
    meshes: list[PrimMeshData] = field(default_factory=list)

    @property
    def weighted(self) -> bool:
        return bool(self.flags & 0b1000)

    @property
    def has_bones(self) -> bool:
        return bool(self.flags & 0b1)


def _unit_bytes(raw: np.ndarray) -> np.ndarray:
    """(N,4) uint8 -> (N,4) float32: xyz in [-1,1], w = raw handedness byte."""
    out = np.empty(raw.shape, np.float32)
    out[:, :3] = (raw[:, :3].astype(np.float32) - 128.0) / 127.5
    out[:, 3] = raw[:, 3]
    return out


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n < 1e-8] = 1.0
    return v / n


def parse_prim(data: bytes) -> PrimData:
    mv = memoryview(data)
    (hdr_off,) = struct.unpack_from("<Q", mv, 0)
    _, _, _, flags, _pad, rig, count, table_off = struct.unpack_from("<BBHIIIII", mv, hdr_off)
    prim = PrimData(flags=flags, bone_rig_index=rig)
    weighted = prim.weighted
    offsets = struct.unpack_from("<%dI" % count, mv, table_off)

    for off in offsets:
        (_, _, _, sub_type, lodmask, props, _var, zbias, _zo, mat_id, _wire) = struct.unpack_from("<BBHBBBBBBHI", mv, off)
        bmin = struct.unpack_from("<3f", mv, off + 16)
        bmax = struct.unpack_from("<3f", mv, off + 28)
        p = off + 44
        nv, vbo, ni, _u0c, ibo, _aux, _u18 = struct.unpack_from("<7I", mv, p)
        p += 28
        pos_scale = np.frombuffer(mv, "<f4", 4, p)
        pos_bias = np.frombuffer(mv, "<f4", 4, p + 16)
        tsb = np.frombuffer(mv, "<f4", 4, p + 32)
        cloth_id = struct.unpack_from("<I", mv, p + 48)[0]
        nuv = max(1, cloth_id >> 16)         # number of UV sets: the per-vertex NTB+UV record is 12 + 4*nuv bytes
        p += 48 + 4  # + cloth id

        pos_raw = np.frombuffer(mv, "<i2", nv * 4, vbo).reshape(nv, 4)
        pos = (pos_raw[:, :3].astype(np.float32) / 32767.0) * pos_scale[:3] + pos_bias[:3]
        idx = np.frombuffer(mv, "<u2", ni, ibo).astype(np.uint32) if ni else np.zeros(0, np.uint32)

        q = vbo + nv * 8
        colors = None
        if sub_type == SUB_WEIGHTED:
            q += nv * 8                        # unidentified 8B stream, skipped
        if sub_type in (SUB_STANDARD, SUB_LINKED, SUB_WEIGHTED):
            stride = 12 + 4 * nuv
            ntb = np.frombuffer(mv, np.uint8, nv * stride, q).reshape(nv, stride) if nv else np.zeros((0, stride), np.uint8)
            n = _unit_bytes(ntb[:, 0:4])
            t = _unit_bytes(ntb[:, 4:8])
            uv_raw = np.ascontiguousarray(ntb[:, 12:16]).view("<i2").reshape(nv, 2)
            uv = (uv_raw.astype(np.float32) / 32767.0) * tsb[:2] + tsb[2:4]
            q += nv * stride
            if sub_type == SUB_WEIGHTED:
                colors = np.frombuffer(mv, np.uint8, nv * 4, q).reshape(nv, 4).copy()
        else:
            n = np.tile(np.float32([0, 0, 1, 128]), (nv, 1))
            t = n.copy()
            uv = np.zeros((nv, 2), np.float32)

        mesh = PrimMeshData(
            sub_type=sub_type, properties=props, lod_mask=lodmask, zbias=zbias, material_id=mat_id,
            bbox_min=bmin, bbox_max=bmax, positions=pos, pos_w=pos_raw[:, 3].copy(),
            normals=_normalize(n[:, :3]), tangents=t, uvs=uv, colors=colors, indices=idx,
        )

        if weighted:
            _bi, _binfo, _cc, _co, skin_off = struct.unpack_from("<5I", mv, p)
            if skin_off and nv:
                sk = np.frombuffer(mv, np.uint8, nv * 8, skin_off).reshape(nv, 8)
                w = sk[:, 0:4].astype(np.float32) / 255.0
                packed = np.ascontiguousarray(sk[:, 4:8]).view("<u4").ravel()
                j = np.empty((nv, 4), np.uint16)
                j[:, 0] = packed & 0x3FF
                j[:, 1] = (packed >> 10) & 0x3FF
                j[:, 2] = (packed >> 20) & 0x3FF
                j[:, 3] = pos_raw[:, 3]
                mesh.joints, mesh.weights = j, w
        prim.meshes.append(mesh)
    return prim
