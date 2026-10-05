"""Exact granule positions for an Ogg Vorbis stream (what `revorb` does), without touching the audio packets.

ww2ogg rebuilds a standard Ogg Vorbis file from Wwise Vorbis but its page granule positions are approximate, so
players report a wrong duration and cut or pad the end. The granule of a page is the number of PCM samples
completed by the last packet ending on it: each audio packet yields (previous blocksize + current blocksize) / 4
samples, the blocksize being chosen by the packet's mode (read from the setup header). The last page is then
clamped to the true sample count stored by Wwise, which lets decoders trim the final block exactly.
"""
from __future__ import annotations

import struct
import zlib

# Ogg uses the non-reflected CRC-32 (poly 0x04C11DB7, init 0, no final xor). It equals zlib's reflected CRC
# computed on bit-reversed bytes, bit-reversed back: same result, at C speed.
_REV8 = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def _crc(data: bytes) -> int:
    raw = zlib.crc32(data.translate(_REV8), 0xFFFFFFFF) ^ 0xFFFFFFFF
    return int(f"{raw:032b}"[::-1], 2)


def _pages(data: bytes):
    """-> [(offset, header_len, body_len, segment table)]"""
    out, o = [], 0
    while o + 27 <= len(data):
        if data[o:o + 4] != b"OggS":
            raise ValueError("not an Ogg page")
        nseg = data[o + 26]
        seg = data[o + 27:o + 27 + nseg]
        hl = 27 + nseg
        out.append((o, hl, sum(seg), seg))
        o += hl + sum(seg)
    return out


def _mode_blockflags(setup: bytes) -> list[int]:
    """Vorbis modes are the last section of the setup header: read it backwards from the framing bit
    (mode = blockflag:1 windowtype:16 transformtype:16 mapping:8, preceded by mode_count-1 on 6 bits)."""
    bits = [(setup[i >> 3] >> (i & 7)) & 1 for i in range(len(setup) * 8)]
    f = len(bits) - 1
    while f >= 0 and not bits[f]:
        f -= 1                                   # f = framing bit

    def val(s, n):
        return sum(bits[s + k] << k for k in range(n))

    best = None
    for m in range(1, 65):
        start = f - 41 * m
        if start - 6 < 0:
            break
        if val(start + 1, 16) or val(start + 17, 16):   # windowtype / transformtype of the mode are always 0
            break
        if val(start - 6, 6) == m - 1:
            best = [bits[start + 41 * k] for k in range(m)]
    if best is None:
        raise ValueError("cannot read Vorbis modes")
    return best


def fix_granules(data: bytes, total_samples: int | None = None) -> bytes:
    pages = _pages(data)
    # packets in order, remembering on which page each one ends
    packets, cur, ends = [], bytearray(), []
    for pi, (o, hl, bl, seg) in enumerate(pages):
        p = o + hl
        for s in seg:
            cur += data[p:p + s]
            p += s
            if s < 255:
                packets.append(bytes(cur))
                ends.append(pi)
                cur = bytearray()
    if len(packets) < 3 or packets[0][:7] != b"\x01vorbis" or packets[2][:7] != b"\x05vorbis":
        raise ValueError("not a Vorbis stream")
    b = packets[0][28]
    bs = (1 << (b & 0x0F), 1 << (b >> 4))
    flags = _mode_blockflags(packets[2])
    mbits = max(0, (len(flags) - 1).bit_length())

    granule_at_page = {}
    total, prev = 0, None
    for k, pk in enumerate(packets):
        if k < 3:
            granule_at_page[ends[k]] = 0
            continue
        if not pk or pk[0] & 1:                  # empty or non-audio packet
            continue
        mode = (int.from_bytes(pk[:4].ljust(4, b"\0"), "little") >> 1) & ((1 << mbits) - 1)
        size = bs[flags[mode] if mode < len(flags) else 0]
        if prev is not None:
            total += prev // 4 + size // 4
        prev = size
        granule_at_page[ends[k]] = total

    out = bytearray(data)
    last = len(pages) - 1
    for pi, (o, hl, bl, seg) in enumerate(pages):
        g = granule_at_page.get(pi, -1)
        if pi == last and total_samples is not None and 0 <= total_samples <= total:
            g = total_samples
        struct.pack_into("<q", out, o + 6, g)
        if pi == last:
            out[o + 5] |= 0x04                   # end of stream
        struct.pack_into("<I", out, o + 22, 0)
        struct.pack_into("<I", out, o + 22, _crc(bytes(out[o:o + hl + bl])))
    return bytes(out)
