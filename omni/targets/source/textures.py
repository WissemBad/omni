"""Texture conversion: game textures -> Source VTF files.

Two paths:
  * passthrough: BC1/BC2/BC3 mips are stored in the VTF unchanged (zero loss, zero CPU);
  * encode: the full-size image (after the material's colour work) goes to the Rust core, which builds the whole
    mip chain itself (colour maps averaged in linear light, normal maps renormalised, alpha-tested maps keeping
    their coverage), encodes DXT1/DXT5 or BGRA8888 and writes the VTF with its reflectivity.
A pure-Python path (PIL mips + quicktex) remains for machines without the Rust core.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ...core.ir import TextureData
from ...native import R
from ...sources.glacier.texture import Mip, to_rgba
from . import vtf

# DXT encoder effort (Rust/libsquish): 0 range fit (fast), 1 cluster fit, 2 iterative cluster fit (best, ~3x slower)
ENCODER_QUALITY = 2


def _cap(mips, max_size: int):
    kept = [m for m in mips if max(m[0], m[1]) <= max_size]
    return kept or [mips[-1]]


def passthrough(tex: TextureData, out: Path, max_size: int, flags: int = 0, alpha: bool = False) -> bool:
    """BC1/BC2/BC3 are stored in a VTF unchanged: zero quality loss, zero CPU."""
    fmt = {"BC1": vtf.DXT1, "BC2": vtf.DXT3, "BC3": vtf.DXT5}.get(tex.fmt)
    if fmt is None:
        return False
    mips = _cap(tex.mips, max_size)
    if fmt == vtf.DXT1 and alpha:
        fmt, flags = vtf.DXT1A, flags | vtf.FLAG_ONEBITALPHA
    if fmt not in (vtf.DXT1, vtf.DXT1A):
        flags |= vtf.FLAG_EIGHTBITALPHA
    vtf.write_vtf(out, fmt, mips, flags, reflectivity(tex))
    return True


def reflectivity(tex: TextureData) -> tuple[float, float, float]:
    """Average colour in linear light (VTF header, used by VRAD for bounced light), from a small mip."""
    try:
        small = [m for m in tex.mips if max(m[0], m[1]) <= 64] or [tex.mips[-1]]
        a = to_rgba(tex.fmt, Mip(*small[0]))[..., :3].astype(np.float32) / 255.0
        lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        return tuple(float(x) for x in lin.reshape(-1, 3).mean(0))
    except Exception:  # noqa: BLE001 - a header value: never worth failing a conversion
        return (0.5, 0.5, 0.5)


def has_alpha(tex: TextureData) -> bool:
    """True when the texture actually uses transparency (checked on a ~128px mip, cheap)."""
    if tex.fmt in ("BC4", "BC5", "RG8", "A8"):
        return False
    cand = [m for m in tex.mips if max(m[0], m[1]) <= 128] or [tex.mips[-1]]
    w, h, d = cand[0]
    a = to_rgba(tex.fmt, Mip(w, h, d))[..., 3]
    # Real opacity (leaves, hair strands, grilles, labels) is bimodal: pixels are either clear or opaque.
    # A smooth alpha channel (cloth, skin, eyes, shoes) carries other data (roughness/AO/masks), not coverage.
    clear = float((a < 40).mean())
    solid = float((a > 215).mean())
    return clear > 0.005 and (clear + solid) > 0.9


def decode_top(tex: TextureData, max_size: int) -> np.ndarray:
    """RGBA of the largest game mip that fits ``max_size``."""
    w, h, data = _cap(tex.mips, max_size)[0]
    return to_rgba(tex.fmt, Mip(w, h, data))


def decode_mips_rgba(tex: TextureData, max_size: int) -> list[np.ndarray]:
    """[top image]: the mip chain is rebuilt from it at encode time (kept as a list for the callers)."""
    return [decode_top(tex, max_size)]


def keep_coverage(mips: list[np.ndarray], ref: float = 0.5) -> list[np.ndarray]:
    """Python twin of the coverage-preserving mips of the Rust encoder (used by the fallback path only):
    plain averaging makes thin strands/leaves fall under the alpha-test threshold, so each smaller mip's alpha is
    rescaled to keep the share of pixels passing the test of the full-size image."""
    cut = ref * 255.0
    target = float((mips[0][..., 3] >= cut).mean())
    if target <= 0.0 or target >= 1.0:
        return mips
    out = [mips[0]]
    for m in mips[1:]:
        a = m[..., 3].astype(np.float32)
        t = float(np.quantile(a, 1.0 - target))
        if t > 1.0:
            m = m.copy()
            m[..., 3] = np.clip(a * (cut / t), 0, 255).astype(np.uint8)
        out.append(m)
    return out


def resize(a: np.ndarray, w: int, h: int) -> np.ndarray:
    if a.shape[1] == w and a.shape[0] == h:
        return a
    return np.asarray(Image.fromarray(a).resize((w, h), Image.BILINEAR))


def _py_chain(img: np.ndarray) -> list[np.ndarray]:
    h, w = img.shape[:2]
    mips = [img]
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        mips.append(resize(img, w, h))
    return mips


def _py_dxt(a: np.ndarray, alpha: bool) -> bytes:
    import quicktex
    import quicktex.s3tc.bc1 as _bc1
    import quicktex.s3tc.bc3 as _bc3
    h, w = a.shape[:2]
    p = np.pad(a, ((0, (-h) % 4), (0, (-w) % 4), (0, 0)), mode="edge") if (h % 4 or w % 4) else a
    p = np.ascontiguousarray(p)
    raw = quicktex.RawTexture.frombytes(p.tobytes(), p.shape[1], p.shape[0])
    enc = _bc3.BC3Encoder(5) if alpha else _bc1.BC1Encoder(5)
    return enc.encode(raw).tobytes()


def encode(rgba_mips: list[np.ndarray], out: Path, alpha: bool, flags: int = 0, lossless: bool = False,
           kind: str | None = None, max_size: int = 0, coverage: float = 0.0) -> None:
    """Write ``out`` from the full-size image ``rgba_mips[0]`` (smaller levels are rebuilt). DXT1 (opaque) / DXT5
    (alpha), or BGRA8888 when ``lossless``. ``kind``: srgb (colour, default), linear (data maps), normal (default
    when ``flags`` has the normal-map bit)."""
    top = np.ascontiguousarray(rgba_mips[0], np.uint8)
    kind = kind or ("normal" if flags & vtf.FLAG_NORMAL else "srgb")
    fmt = "bgra8888" if lossless else ("dxt5" if alpha else "dxt1")
    if R.has("encode_vtf"):
        R.encode_vtf(str(out), top, fmt, kind, max_size, flags & ~vtf.FLAG_NORMAL, ENCODER_QUALITY, coverage)
        return
    # pure-Python fallback
    if max_size and max(top.shape[:2]) > max_size:
        k = max(top.shape[:2]) / max_size
        top = resize(top, max(1, int(top.shape[1] / k)), max(1, int(top.shape[0] / k)))
    mips = _py_chain(top)
    if coverage:
        mips = keep_coverage(mips, coverage)
    data = []
    for a in mips:
        h, w = a.shape[:2]
        data.append((w, h, np.ascontiguousarray(a[..., [2, 1, 0, 3]]).tobytes() if lossless else _py_dxt(a, alpha)))
    if alpha or lossless:
        flags |= vtf.FLAG_EIGHTBITALPHA
    if kind == "normal":
        flags |= vtf.FLAG_NORMAL
    vtf.write_vtf(out, vtf.BGRA8888 if lossless else (vtf.DXT5 if alpha else vtf.DXT1), data, flags)


def rebuild_normal(rgba: np.ndarray) -> np.ndarray:
    """BC5 normal (RG only) -> RGB tangent-space normal with reconstructed Z."""
    xy = rgba[..., :2].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - (xy ** 2).sum(-1), 0.0, 1.0))
    out = rgba.copy()
    out[..., 2] = np.clip((z * 0.5 + 0.5) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out
