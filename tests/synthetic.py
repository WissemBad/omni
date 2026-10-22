"""A synthetic HITMAN 3 game, written to disk the way the real one is laid out (Runtime/chunk0.rpkg, HM3 PRIM / MATI /
TEXT per the documented layouts, Retail/HITMAN3.exe). One prop, one material, one texture: enough for every layer of
omni (identify, prepare, source, catalog, API) to run in CI without any game file."""
from __future__ import annotations

import struct
from pathlib import Path

from rpkg_builder import Res, build

PRIM_H, MATI_H, TEXT_H, BORG_H = 0x0011_2233_4455_6601, 0x0011_2233_4455_6602, 0x0011_2233_4455_6603, 0x0011_2233_4455_6604


def hm3_prim(weighted: bool = False) -> bytes:
    """One quad (4 vertices, 2 triangles), int16 positions with scale/bias."""
    nv, ni = 4, 6
    out = bytearray(b"\0" * 8)
    hdr = len(out)
    out += struct.pack("<BBHIIII", 0, 0, 1, 0x8 if weighted else 0, 1 if weighted else 0xFFFFFFFF, 1, 0)
    out += struct.pack("<6f", -1, -1, 0, 1, 1, 0)
    table = len(out)
    out += struct.pack("<I", 0)
    obj = len(out)

    def object_header(sub_type):
        return struct.pack("<BBHBBBBBBHII", 0, 0, 2, sub_type, 0, 0xFF, 0, 0, 0, 0, 0, 0) + struct.pack("<6f", -1, -1, 0, 1, 1, 0)
    out += object_header(2 if weighted else 0)
    ptr_at = len(out)
    out += struct.pack("<I", 0)
    out += struct.pack("<4f", 1, 1, 1, 1) + struct.pack("<4f", 0, 0, 0, 0) + struct.pack("<2f", 1, 1) + struct.pack("<2f", 0, 0)
    out += struct.pack("<I", 0)
    if weighted:
        out += struct.pack("<4I", 0, 0, 0, 0)
    ptr = len(out)
    out += struct.pack("<I", 0)
    sub = len(out)
    out += object_header(2 if weighted else 0)
    sub_fields = len(out)
    out += struct.pack("<9I", nv, 0, ni, 0, 0, 0, 0, 1, 0)
    vbo = len(out)
    for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        out += struct.pack("<4h", x * 32767, y * 32767, 0, 0)
    if weighted:
        for _ in range(nv):
            out += bytes([255, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
        out += bytes([128, 128, 255, 255, 255, 128, 128, 255, 128, 255, 128, 255]) + struct.pack("<2h", u * 32767, v * 32767)
    for _ in range(nv):
        out += bytes([255, 255, 255, 255])
    ibo = len(out)
    out += struct.pack("<6H", 0, 1, 2, 0, 2, 3)
    struct.pack_into("<I", out, 0, hdr)
    struct.pack_into("<I", out, hdr + 16, table)
    struct.pack_into("<I", out, table, obj)
    struct.pack_into("<I", out, ptr_at, ptr)
    struct.pack_into("<I", out, ptr, sub)
    struct.pack_into("<I", out, sub_fields + 4, vbo)
    struct.pack_into("<I", out, sub_fields + 16, ibo)
    return bytes(out)


def hm3_mati() -> bytes:
    """Instance { NAME, TEXT { NAME mapTexture2D_01, TXID 0 }, FLTV { NAME, VALU 0.5 }, BMOD "Opaque" }."""
    strings = bytearray()

    def s(text):
        off = len(strings)
        strings.extend(text.encode() + b"\0")
        return off
    o_cls, o_inst, o_slot, o_val, o_bmod = s("standard"), s("inst"), s("mapTexture2D_01"), s("mapRoughness"), s("Opaque")
    head = 4
    hdr = head + len(strings)
    props_at = hdr + 0x20

    def prop(tag, data, count, typ):
        return tag[::-1].encode() + struct.pack("<III", data, count, typ)
    # layout: root(16) | root children 4x16 | TEXT children 2x16 | FLTV children 2x16
    root_kids = props_at + 16
    text_kids = root_kids + 4 * 16
    fltv_kids = text_kids + 2 * 16
    body = prop("INST", root_kids, 4, 3)
    body += prop("NAME", head + o_inst, 0, 1) + prop("TEXT", text_kids, 2, 3) + prop("FLTV", fltv_kids, 2, 3) + prop("BMOD", head + o_bmod, 0, 1)
    body += prop("NAME", head + o_slot, 0, 1) + prop("TXID", 0, 1, 2)
    body += prop("NAME", head + o_val, 0, 1) + struct.pack("<4s", b"ULAV") + struct.pack("<fII", 0.5, 1, 0)
    out = struct.pack("<I", hdr) + bytes(strings)
    out += struct.pack("<IIIIi", head + o_cls, 1, 0, 0, -1) + b"\0" * 8 + struct.pack("<I", props_at)
    return out + body


def hm3_text() -> bytes:
    """4x4 BC1 (HM3 code 0x49), one mip stored raw in the TEXT."""
    d = bytearray(0x98)
    struct.pack_into("<HHIIHHH", d, 0, 1, 0, 0, 0, 4, 4, 0x49)
    d[0x12], d[0x13] = 1, 7                       # mip count, mips_default
    struct.pack_into("<I", d, 0x18, 8)
    struct.pack_into("<I", d, 0x50, 8)
    struct.pack_into("<II", d, 0x88, 0, 0x98)
    return bytes(d) + bytes([0xFF, 0xFF, 0, 0, 0, 0, 0, 0])


NAMES = {
    "prim": "[assembly:/_pro/environment/geometry/props/crate.wl2?/crate_a.prim].pc_prim",
    "mati": "[assembly:/_pro/environment/materials/crate.mi].pc_mi",
    "text": "[assembly:/_pro/environment/textures/crate.texture?/diffuse_a.tex](ascolormap).pc_tex",
}


def write_game(folder: Path) -> Path:
    """Create ``folder`` as a HITMAN 3 installation holding the synthetic resources; returns it."""
    (folder / "Runtime").mkdir(parents=True, exist_ok=True)
    (folder / "Retail").mkdir(exist_ok=True)
    (folder / "Retail" / "HITMAN3.exe").write_bytes(b"MZ")
    (folder / "Runtime" / "chunk0.rpkg").write_bytes(build([
        Res(PRIM_H, "PRIM", hm3_prim(), [(MATI_H, 0x1F)], compress=True),
        Res(MATI_H, "MATI", hm3_mati(), [(TEXT_H, 0x1F)]),
        Res(TEXT_H, "TEXT", hm3_text()),
    ]))
    return folder


def hash_list() -> str:
    """The names file of the game (Hitman-Hashes format), as the preparation job writes it."""
    rows = [f"{PRIM_H:016X}.PRIM,{NAMES['prim']}", f"{MATI_H:016X}.MATI,{NAMES['mati']}", f"{TEXT_H:016X}.TEXT,{NAMES['text']}"]
    return "# test\n" + "\n".join(rows) + "\n" + "x" * 10
