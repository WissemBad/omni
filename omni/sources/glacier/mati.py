"""MATI (material instance) decoder, 007 First Light.

Record layout (reverse engineered, see the legacy add-on):
    u16 nameOffset | u16 type | u32 sub | data (len by type)
    type 0x01 float(4) | 0x02 texture(8: flags,index) | 0x03 rgb(12) | 0x08 transform(32)
A texture record's index points into the MATI .meta reference list.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

_TYPELEN = {0x01: 4, 0x02: 8, 0x03: 12, 0x08: 32}


@dataclass
class MatiTexture:
    name: str
    ref_index: int


@dataclass
class MatiParam:
    name: str
    type: int
    values: list[float]


@dataclass
class Mati:
    strings: dict[int, str] = field(default_factory=dict)
    textures: list[MatiTexture] = field(default_factory=list)
    params: list[MatiParam] = field(default_factory=list)
    ok: bool = True


def _printable(b: int) -> bool:
    return 32 <= b < 127


def parse_mati(data: bytes) -> Mati:
    n = len(data)
    i = 8
    while i < n - 8 and not all(_printable(data[i + k]) for k in range(8)):
        i += 1
    while i > 0 and data[i - 1] != 0:
        i -= 1
    base = i - 1

    strings: dict[int, str] = {}
    j = i
    while j < n:
        if data[j] == 0:
            j += 1
            continue
        if not _printable(data[j]):
            break
        z = data.find(b"\x00", j)
        if z < 0:
            break
        strings[j - base] = data[j:z].decode("ascii", "replace")
        j = z + 1

    def walk(s: int):
        o, out = s, []
        while o < n:
            if o + 8 > n:
                return None
            nm, t, _sub = struct.unpack_from("<HHI", data, o)
            if t not in _TYPELEN:
                return None
            dl = _TYPELEN[t]
            if o + 8 + dl > n:
                return None
            out.append((nm, t, o + 8, dl))
            o += 8 + dl
        return out if o == n else None

    records = None
    for s in range(i, n):
        r = walk(s)
        if r and len(r) >= 2:
            records = r
            break

    res = Mati(strings=strings)
    if records is None:
        res.ok = False
        return res
    for nm, t, doff, dl in records:
        name = strings.get(nm, "@0x%X" % nm)
        if t == 0x02:
            _flags, idx = struct.unpack_from("<II", data, doff)
            res.textures.append(MatiTexture(name, idx))
        else:
            res.params.append(MatiParam(name, t, list(struct.unpack_from("<%df" % (dl // 4), data, doff))))
    return res
