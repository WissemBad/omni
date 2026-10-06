"""VTF 7.2 constants and the writer of raw blocks (the Rust core builds the file: DXT1/DXT3/DXT5/BGRA8888, one frame, full mip chain, no thumbnail)."""
from __future__ import annotations

from pathlib import Path

from ...native import N

DXT1, DXT3, DXT5, BGRA8888, DXT1A = 13, 14, 15, 12, 20

FLAG_CLAMPS = 0x4
FLAG_CLAMPT = 0x8
FLAG_NORMAL = 0x80
FLAG_NOMIP = 0x100
FLAG_EIGHTBITALPHA = 0x2000
FLAG_ONEBITALPHA = 0x1000


def write_vtf(path: Path, fmt: int, mips: list, flags: int = 0, reflectivity=(0.5, 0.5, 0.5)) -> None:
    """``mips``: [(w, h, bytes)] largest first. Written by the Rust core, atomically (parallel workers may produce
    the same shared texture at once, and a worker reading its header keeps the rename waiting)."""
    N.write_vtf(str(path), fmt, mips, flags, tuple(reflectivity))
