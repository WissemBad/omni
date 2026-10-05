"""Writes small RPKG v2 packages for the extraction tests (same layout the extractor reads, see native/src/rpkg.rs)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import lz4.block

XOR = bytes([0xDC, 0x45, 0xA6, 0x9C, 0xD3, 0x72, 0x4C, 0xAB])


@dataclass
class Res:
    hash: int
    type: str
    data: bytes
    refs: list[tuple[int, int]] = field(default_factory=list)       # (hash, flag byte)
    scramble: bool = False
    compress: bool = False


def build(res: list[Res], chunk: int = 0, patch: int = 0, unneeded: list[int] | None = None, states: bool = True) -> bytes:
    blobs = []
    for r in res:
        b = lz4.block.compress(r.data, store_size=False) if r.compress else r.data
        if r.scramble:
            b = bytes(x ^ XOR[i % 8] for i, x in enumerate(b))
        blobs.append(b)
    meta = bytearray()
    for r in res:
        meta += r.type.encode()[::-1]
        refs = 4 + 9 * len(r.refs) if r.refs else 0
        meta += struct.pack("<I", refs)
        if states:
            meta += struct.pack("<I", 0)
        meta += struct.pack("<III", len(r.data), 0, 0xFFFFFFFF)
        if r.refs:
            meta += struct.pack("<I", len(r.refs) | 0xC0000000)
            meta += bytes(f for _h, f in r.refs)
            for h, _f in r.refs:
                meta += struct.pack("<Q", h)
    un = unneeded or []
    table = 20 * len(res)
    unneeded_blob = struct.pack("<I", len(un)) + b"".join(struct.pack("<Q", h) for h in un) if patch else b""
    start = 4 + 9 + 12 + len(unneeded_blob) + table + len(meta)
    out = bytearray(b"2KPR" + struct.pack("<IBBB", 0, chunk, 0, patch) + b"xx")
    out += struct.pack("<III", len(res), table, len(meta)) + unneeded_blob
    off = start
    for r, b in zip(res, blobs):
        flags = (len(b) if r.compress else 0) | (0x80000000 if r.scramble else 0)
        out += struct.pack("<QQI", r.hash, off, flags)
        off += len(b)
    out += meta
    for b in blobs:
        out += b
    return bytes(out)
