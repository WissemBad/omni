"""TEXT/TEXD texture decoding (007 First Light, TextureMapHeaderV4).

A texture is a .TEXT (header + the small streaming mips) and an optional .TEXD
(the large mips), every mip being an independent LZ4 block (stored raw when LZ4
could not shrink it).  The result is the *raw BCn block data per mip*: it can be
re-wrapped into a VTF without any recompression, or decoded to RGBA on demand.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import lz4.block
import numpy as np

# RenderFormat code -> (name, bytes per 4x4 block, bytes per pixel for raw formats)
FORMATS = {
    0x4C: ("BC1", 8), 0x4F: ("BC2", 16), 0x52: ("BC3", 16),
    0x55: ("BC4", 8), 0x58: ("BC5", 16), 0x5E: ("BC7", 16),
    0x1C: ("RGBA8", 0), 0x37: ("RG8", 0), 0x45: ("A8", 0),
}
RAW_BPP = {"RGBA8": 4, "RG8": 2, "A8": 1}


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


def mip_nbytes(name: str, w: int, h: int) -> int:
    if name in RAW_BPP:
        return w * h * RAW_BPP[name]
    blk = dict((v[0], v[1]) for v in FORMATS.values())[name]
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * blk


def _inflate(chunk: bytes, size: int) -> bytes:
    if len(chunk) >= size:
        return bytes(chunk[:size])
    out = lz4.block.decompress(bytes(chunk), uncompressed_size=size)
    return out


def decode_mips(text: bytes, texd: bytes | None) -> tuple[TextHeader, list[Mip]]:
    """All mips available, largest first. Mips missing from both files are skipped."""
    hd = parse_text_header(text)
    from ...native import N
    if N is not None:                      # Rust: same algorithm, verified byte-identical on 400 textures
        _h, mips = N.decode_texture(text, texd)
        return hd, [Mip(w, h, raw) for w, h, raw in mips]
    name = hd.name
    if name not in dict((v[0], 1) for v in FORMATS.values()):
        raise ValueError("unsupported texture format " + name)
    start = 0x98 + hd.atlas
    mips: list[Mip] = []
    prev = 0
    for i in range(hd.mips):
        size_c = hd.comp[i] - prev
        prev = hd.comp[i]
        w, h = max(1, hd.width >> i), max(1, hd.height >> i)
        nb = mip_nbytes(name, w, h)
        if i < hd.first_text_mip:
            if texd is None:
                continue
            off = hd.comp[i - 1] if i else 0
            chunk = texd[off:off + size_c]
        else:
            off = start + (hd.comp[i - 1] - (hd.comp[hd.first_text_mip - 1] if hd.first_text_mip else 0) if i else 0)
            chunk = text[off:off + size_c]
        if size_c <= 0 or not chunk:
            continue
        try:
            raw = _inflate(chunk, nb)
        except lz4.block.LZ4BlockError:
            continue
        if len(raw) < nb:
            raw += bytes(nb - len(raw))
        mips.append(Mip(w, h, raw))
    if not mips:
        raise ValueError("no decodable mip")
    return hd, mips


def to_rgba(name: str, mip: Mip) -> np.ndarray:
    """Decode one mip to (h,w,4) uint8. BC4 -> R replicated, BC5 -> RG with Z rebuilt."""
    from ...native import N
    if N is not None:                      # Rust, block rows decoded in parallel (identical pixels)
        return N.to_rgba(name, mip.width, mip.height, mip.data)
    import texture2ddecoder as t2d
    w, h, d = mip.width, mip.height, mip.data
    if name == "RGBA8":
        return np.frombuffer(d, np.uint8).reshape(h, w, 4).copy()
    if name == "RG8":
        a = np.frombuffer(d, np.uint8).reshape(h, w, 2)
        out = np.zeros((h, w, 4), np.uint8)
        out[..., :2] = a
        out[..., 3] = 255
        return out
    if name == "A8":
        a = np.frombuffer(d, np.uint8).reshape(h, w)
        return np.stack([a, a, a, np.full_like(a, 255)], -1)
    fn = {"BC1": t2d.decode_bc1, "BC3": t2d.decode_bc3, "BC4": t2d.decode_bc4,
          "BC5": t2d.decode_bc5, "BC7": t2d.decode_bc7, "BC2": t2d.decode_bc3}[name]
    bgra = np.frombuffer(fn(d, w, h), np.uint8).reshape(h, w, 4)
    rgba = bgra[..., [2, 1, 0, 3]].copy()      # texture2ddecoder returns BGRA
    return rgba
