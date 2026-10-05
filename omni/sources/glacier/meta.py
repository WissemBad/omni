"""Glacier resource .meta files (RPKG-Tool binary layout) and the hash->name list."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class Meta:
    resource_id: int
    type: str
    refs: list[tuple[int, int]] = field(default_factory=list)   # (hash, flag)


def parse_meta(data: bytes) -> Meta:
    rid = struct.unpack_from("<Q", data, 0)[0]
    ext = bytes(data[20:24])[::-1].decode("ascii", "replace").strip("\x00")
    refs_size = struct.unpack_from("<I", data, 24)[0]
    refs: list[tuple[int, int]] = []
    if refs_size > 0:
        cnt = struct.unpack_from("<H", data, 40)[0]
        flags = data[44:44 + cnt]
        base = 44 + cnt
        for i in range(cnt):
            refs.append((struct.unpack_from("<Q", data, base + i * 8)[0], flags[i]))
    return Meta(rid, ext, refs)


