"""TEXT/TEXD texture decoding (007 First Light, TextureMapHeaderV4).

A texture is a .TEXT (header + the small streaming mips) and an optional .TEXD
(the large mips), every mip being an independent LZ4 block (stored raw when LZ4
could not shrink it).  The result is the *raw BCn block data per mip*: it can be
re-wrapped into a VTF without any recompression, or decoded to RGBA on demand.
Decoding is done by the Rust core (``native/src/texture.rs``).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# RenderFormat code -> (name, bytes per 4x4 block, bytes per pixel for raw formats)
FORMATS = {
    0x4C: ("BC1", 8), 0x4F: ("BC2", 16), 0x52: ("BC3", 16),
    0x55: ("BC4", 8), 0x58: ("BC5", 16), 0x5E: ("BC7", 16),
    0x1C: ("RGBA8", 0), 0x37: ("RG8", 0), 0x45: ("A8", 0),
}


@dataclass
class TextHeader:
    width: int
    height: int
    fmt: int
    mips: int
    first_text_mip: int
    atlas: int
    unc: list
    comp: list

    @property
    def name(self) -> str:
        return FORMATS.get(self.fmt, ("0x%02X" % self.fmt, 0))[0]


@dataclass
class Mip:
    width: int
    height: int
    data: bytes


def parse_text_header(data: bytes) -> TextHeader:
    if len(data) < 0x98:
        raise ValueError("TEXT too small")
    w, h, fmt, mips = struct.unpack_from("<HHHH", data, 0x0C)
    if not (0 < w <= 16384 and 0 < h <= 16384 and 0 < mips <= 14):
        raise ValueError("invalid TEXT header")
    unc = list(struct.unpack_from("<14I", data, 0x18))
    comp = list(struct.unpack_from("<14I", data, 0x50))
    atlas = struct.unpack_from("<I", data, 0x88)[0]
    return TextHeader(w, h, fmt, mips, data[0x91], atlas, unc, comp)


def decode_mips(text: bytes, texd: bytes | None) -> tuple[TextHeader, list[Mip]]:
    """All mips available, largest first. Mips missing from both files are skipped."""
    hd = parse_text_header(text)
    from ...native import N
    _h, mips = N.decode_texture(text, texd)
    return hd, [Mip(w, h, raw) for w, h, raw in mips]


def to_rgba(name: str, mip: Mip) -> np.ndarray:
    """Decode one mip to (h,w,4) uint8. BC4 -> R replicated, BC5 -> RG with Z rebuilt."""
    from ...native import N
    return N.to_rgba(name, mip.width, mip.height, mip.data)
