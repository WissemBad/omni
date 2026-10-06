"""HITMAN World of Assassination (Glacier 2, "HM3") resource decoders where they differ from 007 First Light.

Layouts from the open-source RPKG-Tool (glacier-modding, written for HM3) — not verified on game data here:

PRIM  file[0:4] header offset; object header {u8 draw, u8 pack, u16 type, u32 flags (0x8 weighted, 0x200 high
      resolution), u32 BORG ref index, u32 object count, u32 object table offset, bbox}. Each object:
      {u16x2 header, u8 sub type (0 standard, 1 linked, 2 weighted), u8 properties (0x20 no colour stream),
      u8 LOD mask, u8 variant, u8 z bias, u8 z offset, u16 material ref index, u32 wire colour, u32 colour,
      bbox} then u32 pointer to the sub-mesh offset, vec4 position scale, vec4 position bias, vec2 UV scale,
      vec2 UV bias, u32 cloth flags. The sub-mesh repeats the object header, then vertex count, vertex offset,
      index count, extra index count, index offset, collision, cloth, UV channel count.
      Vertices: positions (float32 x3 when high resolution, else int16 x4 * scale / 32767 + bias), weighted: 12
      bytes {4 weights, 4 bone ids, 2 weights, 2 bone ids} per vertex, then normal/tangent/bitangent (u8 x4 each,
      2b/255-1) + UV int16 x2 per channel, then RGBA colours (weighted meshes, or without the 0x20 property).
MATI  u32 header offset -> {u32 class name offset, u32 texture count, 2 x u32, i32 ERES ref, 8 bytes, u32 (at +0x1C)
      instance offset}; the instance is a tree of 16-byte properties {4-char tag (reversed), u32 data, u32 count,
      u32 type (0 float, 1 string, 2 int, 3 children)}: TEXT {NAME slot, TXID ref index}, FLTV/COLO {NAME, VALU},
      BMOD blend mode, ATST alpha test, CULL culling.
TEXT  same container as 007 (TextureMapHeaderV4) but the format codes differ and the mip count is one byte:
      ``bond_text`` rewrites the header so the 007 decoders read it.
"""
from __future__ import annotations

import struct

import numpy as np

from .mati import Mati, MatiParam, MatiTexture
from .prim import PrimData, PrimMeshData

# HM3 texture format -> the 007 code the native decoder knows (RGBA8 is the same)
_TEXT_FORMAT = {0x49: 0x4C, 0x4F: 0x52, 0x52: 0x55, 0x55: 0x58, 0x5A: 0x5E, 0x34: 0x37, 0x42: 0x45, 0x1C: 0x1C}


def bond_text(data: bytes) -> bytes:
    """An HM3 TEXT with a 007 header (format code, one-byte mip count), so the 007 decoders read it."""
    if len(data) < 0x98:
        return data
    b = bytearray(data)
    fmt = struct.unpack_from("<H", b, 0x10)[0]
    if fmt in _TEXT_FORMAT:
        struct.pack_into("<H", b, 0x10, _TEXT_FORMAT[fmt])
    b[0x13] = 0                                   # mips_default: 007 reads the mip count as a u16
    return bytes(b)


def _unit(raw: np.ndarray) -> np.ndarray:
    return raw.astype(np.float32) * (2.0 / 255.0) - 1.0


def parse_prim(data: bytes) -> PrimData:
    mv = memoryview(data)
    hdr = struct.unpack_from("<I", mv, 0)[0]
    _d, _p, _t, flags, rig, count, table = struct.unpack_from("<BBHIIII", mv, hdr)
    prim = PrimData(flags=flags, bone_rig_index=rig)
    hires = bool(flags & 0x200)
    if count > 100000:
        raise ValueError("implausible PRIM object count")
    offsets = struct.unpack_from("<%dI" % count, mv, table)
    for off in offsets:
        (_h1, _h2, _ht, sub_type, props, lodmask, _var, zbias, _zo, mat_id) = struct.unpack_from("<BBHBBBBBBH", mv, off)
        bmin = struct.unpack_from("<3f", mv, off + 20)
        bmax = struct.unpack_from("<3f", mv, off + 32)
        sub_ptr = struct.unpack_from("<I", mv, off + 44)[0]
        pos_scale = np.frombuffer(mv, "<f4", 4, off + 48)
        pos_bias = np.frombuffer(mv, "<f4", 4, off + 64)
        uv_scale = np.frombuffer(mv, "<f4", 2, off + 80)
        uv_bias = np.frombuffer(mv, "<f4", 2, off + 88)
        sub = struct.unpack_from("<I", mv, sub_ptr)[0]
        sub_props = struct.unpack_from("<B", mv, sub + 5)[0]
        nv, vbo, ni, _nix, ibo, _coll, _cloth, nuv = struct.unpack_from("<8I", mv, sub + 44)
        nuv = max(1, nuv)
        idx = np.frombuffer(mv, "<u2", ni, ibo).astype(np.uint32) if ni else np.zeros(0, np.uint32)
        q = vbo
        if hires:
            pos = np.frombuffer(mv, "<f4", nv * 3, q).reshape(nv, 3).copy()
            pos_w = np.zeros(nv, np.int16)
            q += nv * 12
        else:
            raw = np.frombuffer(mv, "<i2", nv * 4, q).reshape(nv, 4)
            pos = (raw[:, :3].astype(np.float32) / 32767.0) * pos_scale[:3] + pos_bias[:3]
            pos_w = raw[:, 3].copy()
            q += nv * 8
        joints = weights = None
        if sub_type == 2:
            sk = np.frombuffer(mv, np.uint8, nv * 12, q).reshape(nv, 12)
            w6 = np.concatenate([sk[:, 0:4], sk[:, 8:10]], 1).astype(np.float32) / 255.0
            j6 = np.concatenate([sk[:, 4:8], sk[:, 10:12]], 1).astype(np.uint16)
            order = np.argsort(-w6, axis=1)[:, :4]
            weights = np.take_along_axis(w6, order, 1)
            joints = np.take_along_axis(j6, order, 1)
            q += nv * 12
        stride = 12 + 4 * nuv
        ntb = np.frombuffer(mv, np.uint8, nv * stride, q).reshape(nv, stride) if nv else np.zeros((0, stride), np.uint8)
        n = _unit(ntb[:, 0:3])
        t = np.empty((nv, 4), np.float32)
        t[:, :3] = _unit(ntb[:, 4:7])
        t[:, 3] = ntb[:, 7]
        uv_raw = np.ascontiguousarray(ntb[:, 12:16]).view("<i2").reshape(nv, 2)
        uv = (uv_raw.astype(np.float32) / 32767.0) * uv_scale + uv_bias
        q += nv * stride
        colors = None
        if sub_type == 2 or not (sub_props & 0x20):
            if q + nv * 4 <= len(data):
                colors = np.frombuffer(mv, np.uint8, nv * 4, q).reshape(nv, 4).copy()
        nn = np.linalg.norm(n, axis=1, keepdims=True)
        nn[nn < 1e-8] = 1.0
        prim.meshes.append(PrimMeshData(
            sub_type=sub_type, properties=props, lod_mask=lodmask, zbias=zbias, material_id=mat_id,
            bbox_min=bmin, bbox_max=bmax, positions=pos, pos_w=pos_w, normals=n / nn, tangents=t, uvs=uv,
            colors=colors, indices=idx, joints=joints, weights=weights))
    return prim


def _tag(b: bytes) -> str:
    return b[::-1].decode("latin-1")


def _cstr(d: bytes, off: int) -> str:
    if not 0 <= off < len(d):
        return ""
    end = d.find(b"\0", off)
    return d[off:end if end >= 0 else len(d)].decode("latin-1")


def parse_mati(data: bytes) -> Mati:
    """HM3 material instance -> the 007 Mati structure (textures with their meta ref index, float parameters),
    plus ``cls`` (class name), ``blend``, ``alpha_test`` and ``cull`` attributes."""
    m = Mati()
    try:
        hdr = struct.unpack_from("<I", data, 0)[0]
        cls_off, _ntex, _f1, _f2, eres = struct.unpack_from("<IIIIi", data, hdr)
        inst = struct.unpack_from("<I", data, hdr + 0x1C)[0]
        m.cls = _cstr(data, cls_off)
        m.eres = eres
        m.blend, m.alpha_test, m.cull = "", False, ""
        seen = 0

        def walk(pos: int, depth: int) -> dict:
            nonlocal seen
            seen += 1
            if depth > 12 or seen > 20000 or pos + 16 > len(data):
                return {}
            tag = _tag(data[pos:pos + 4])
            val, cnt, typ = struct.unpack_from("<III", data, pos + 4)
            node = {"tag": tag}
            if typ == 0:
                node["floats"] = [struct.unpack_from("<f", data, pos + 4)[0]] if cnt == 1 else \
                    list(struct.unpack_from("<%df" % min(cnt, 64), data, val)) if 0 < cnt and val + 4 * min(cnt, 64) <= len(data) else []
            elif typ == 1:
                node["str"] = _cstr(data, val)
            elif typ == 2:
                node["int"] = val
            elif typ == 3:
                node["children"] = [walk(val + 16 * k, depth + 1) for k in range(min(cnt, 4096))]
            return node

        root = walk(inst, 0)

        def visit(node: dict) -> None:
            kids = node.get("children") or []
            by = {}
            for k in kids:
                by.setdefault(k.get("tag"), k)
            tag = node.get("tag")
            if tag == "TEXT":
                name = (by.get("NAME") or {}).get("str", "")
                txid = (by.get("TXID") or {}).get("int")
                if name and txid is not None and txid != 0xFFFFFFFF:
                    m.textures.append(MatiTexture(name, int(txid)))
            elif tag in ("FLTV", "COLO"):
                name = (by.get("NAME") or {}).get("str", "")
                vals = (by.get("VALU") or {}).get("floats", [])
                if name and vals:
                    m.params.append(MatiParam(name, 1 if tag == "FLTV" else 3, [float(v) for v in vals]))
            elif tag == "BMOD":
                m.blend = node.get("str", "")
            elif tag == "ATST":
                m.alpha_test = bool(node.get("int"))
            elif tag == "CULL":
                m.cull = node.get("str", "")
            for k in kids:
                visit(k)
        visit(root)
        m.ok = True
    except (struct.error, IndexError, ValueError, UnicodeDecodeError):
        m.ok = False
    return m
