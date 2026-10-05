"""Glacier resource .meta files (RPKG-Tool binary layout) and the hash->name list."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


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


def read_meta(resource_path: Path) -> Meta | None:
    for cand in (Path(str(resource_path) + ".meta"),):
        if cand.is_file():
            try:
                return parse_meta(cand.read_bytes())
            except (struct.error, IndexError):
                return None
    return None


def load_names(hash_list: Path, wanted: set[int] | None = None) -> dict[int, str]:
    """hash_list.txt lines: ``0100467DE88E7820.PRIM,[assembly:/path/file.ext?/sub.prim].prim``.

    Only the 64-bit hash (the part before the dot) is kept; ``wanted`` limits memory use.
    """
    out: dict[int, str] = {}
    with open(hash_list, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line or line[0] == "#" or len(line) < 18:
                continue
            h = line[:16]
            try:
                v = int(h, 16)
            except ValueError:
                continue
            if wanted is not None and v not in wanted:
                continue
            comma = line.find(",", 17)
            if comma < 0:
                continue
            out[v] = line[comma + 1:].strip()
    return out
