"""Minimal VTF 7.2 writer (DXT1/DXT3/DXT5/BGRA8888, single frame, full mip chain, no thumbnail)."""
from __future__ import annotations

import os
import struct
from pathlib import Path

DXT1, DXT3, DXT5, BGRA8888, DXT1A = 13, 14, 15, 12, 20

FLAG_CLAMPS = 0x4
FLAG_CLAMPT = 0x8
FLAG_NORMAL = 0x80
FLAG_NOMIP = 0x100
FLAG_EIGHTBITALPHA = 0x2000
FLAG_ONEBITALPHA = 0x1000


def write_vtf(path: Path, fmt: int, mips: list, flags: int = 0, reflectivity=(0.5, 0.5, 0.5)) -> None:
    """``mips``: [(w, h, bytes)] largest first. Written smallest first as the format requires."""
    w, h = mips[0][0], mips[0][1]
    hdr = struct.pack(
        "<4sIIIHHIHH4s3f4sfIBIBBH",
        b"VTF\0", 7, 2, 80, w, h, flags, 1, 0, b"\0" * 4,
        reflectivity[0], reflectivity[1], reflectivity[2], b"\0" * 4,
        1.0, fmt, len(mips), 0xFFFFFFFF, 0, 0, 1,
    )
    hdr += b"\0" * (80 - len(hdr))
    body = b"".join(m[2] for m in reversed(mips))
    path.parent.mkdir(parents=True, exist_ok=True)
    # atomic: parallel workers may produce the same shared texture at the same time
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(hdr + body)
    os.replace(tmp, path)
