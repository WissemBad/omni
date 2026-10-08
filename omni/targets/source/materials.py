"""Neutral Material -> Source VMT + VTFs (VertexLitGeneric).

Every VMT key written here is derived from the source material's data; nothing is added "just in case":

  $basetexture               base colour. BC1/BC3 copied byte-for-byte; BC7 re-encoded (Source cannot read BC7).
  $bumpmap                   normal map (BC5 -> RGB with Z rebuilt, green flipped to Source's DirectX convention).
  $phong                     only when the material has specular data (SRM or spec map).
  $phongexponenttexture      R = Blinn-Phong exponent converted from PBR roughness, G = metallic
                             (drives $phongalbedotint per pixel).
  $normalmapalphaphongmask   specular intensity per pixel, stored in the normal map alpha.
  $phongboost 2              lets smooth metals reach a full-strength highlight (mask max = 1).
  $phongfresnelranges        dielectric: weak head-on, strong at grazing angles; metal: constant.
  $phongalbedotint           metals have coloured highlights; only if the material has metal pixels.
  $envmap + mask             reflections only for metals and glass (env_cubemap = the map's cubemaps).
  $selfillum + mask          only when the emissive map is not black.
  $alphatest / coverage / nocull   alpha-cut cards (foliage, grilles): single planes seen from both sides.
  $translucent               glass and blended decals.
  $decal                     overlay layers (game z-bias > 0): depth offset, removes z-fighting flicker.

How the game's material is read (colour constants, alpha meaning, packed maps, normal convention) is
shared with the other targets: see ``targets/shading.py``.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.ir import Material
from .. import shading as sh
from ..shading import TextureCache
from . import textures as tx
from . import vtf

__all__ = ["MatResult", "Options", "TextureCache", "convert_material", "guess_surfaceprop"]


@dataclass
class Options:
    max_size: int = 2048               # base colour; 4096 = native for the few 4K textures (--tex-quality max)
    max_size_normal: int = 1024        # normal maps: detail below ~1 px/mm is invisible in GMod, 4x lighter than 2048
    max_size_secondary: int = 512      # derived low-frequency maps (roughness/metal, emissive mask): same look, 1/4 the weight
    max_size_spec: int = 1024          # spec mask carried in the normal map alpha
    phong: bool = True
    envmap: bool = True
    lossless_normals: bool = False     # BGRA8888 normal maps: no DXT block artefacts, 4x the size


@dataclass
class MatResult:
    name: str
    vmt: str
    roles: dict = field(default_factory=dict)
    unknown_slots: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    translucent: bool = False
    drop: bool = False                 # decal layer with nothing usable to draw -> remove its triangles


# Source surfaceprops (sounds, impact effects, friction). Order matters: first match wins.
_SURFACE_HINTS = [
    (r"glass|window|bottle|mirror", "glass"),
    (r"tire|tyre|rubber", "rubber"),
    (r"leaf|leaves|foliage|bush|hedge|plant|flower|ivy|grass", "foliage"),
    (r"wood|plank|timber|parquet|bark|trunk|branch|crate", "wood"),
    (r"metal|steel|\biron|chrome|alumin|brass|copper|rust|weapon|\bgun|pipe", "metal"),
    (r"concrete|cement|asphalt|plaster|stucco", "concrete"),
    (r"stone|rock|granite|marble|brick|cliff", "rock"),
    (r"tile|ceramic|porcelain|pottery|vase|sink|toilet", "ceramic"),
    (r"carpet|rug|fabric|cloth|leather|sofa|cushion|curtain|towel|blanket|textile|mattress", "carpet"),
    (r"plastic|foam|vinyl|polymer", "plastic"),
    (r"cardboard|carton", "cardboard"),
    (r"paper|book|magazine|newspaper|poster", "paper"),
    (r"dirt|sand|gravel|soil|mud", "dirt"),
]


def _material_words(text: str) -> str:
    """Keep only the meaningful part of a game path ('.../materials/generic/metal/chrome_a.mi' ->
    'generic metal chrome a'), so that words like 'envIRONment' never match."""
    t = text.lower()
    for anchor in ("/materials/", "/geometry/", "assembly:/"):
        if anchor in t:
            t = t.split(anchor, 1)[1]
            break
    return " " + re.sub(r"[^a-z0-9]+", " ", t) + " "


def guess_surfaceprop(*texts: str) -> str:
    s = " ".join(_material_words(x) for x in texts if x)
    for rx, prop in _SURFACE_HINTS:
        if re.search(rx, s):
            return prop
    return "default"


def roughness_to_exponent(r: np.ndarray) -> np.ndarray:
    """PBR roughness -> Blinn-Phong exponent (alpha = r^2, n = 2/alpha^2 - 2), clamped to Source's 1..150."""
    a = np.clip(r, 0.05, 1.0) ** 2
    return np.clip(2.0 / (a * a) - 2.0, 1.0, 150.0)


def _vmt_vec(v) -> str:
    return "[" + " ".join(f"{x:.3f}" for x in v) + "]"


def _fresh(path: Path, alpha: bool = False, size: int = 0) -> bool:
    """An already converted texture can be reused, unless the material now needs an alpha channel that the
    file does not have, or it was written at another quality setting (``size``: largest side expected)."""
    head = b""
    for i in range(20):
        try:
            with open(path, "rb") as f:
                head = f.read(57)
            break
        except FileNotFoundError:
            return False
        except PermissionError:            # another worker is replacing it right now (Windows): wait for it
            time.sleep(0.01 + i * 0.005)
    if len(head) < 57:
        return False
    if head[56] > 1 and not int.from_bytes(head[20:24], "little") & vtf.FLAG_ANISOTROPIC:
        return False                       # written before the anisotropic flag: rewrite it
    if alpha and int.from_bytes(head[52:56], "little", signed=True) == vtf.DXT1:
        return False
    if size and max(int.from_bytes(head[16:18], "little"), int.from_bytes(head[18:20], "little")) != size:
        return False
    return True


def _top(tex, max_size: int) -> int:
    """Largest side of the biggest game mip that fits ``max_size`` (the size the converted file will have)."""
    m = tx._cap(tex.mips, max_size)[0]
    return max(m[0], m[1])


def convert_material(mat: Material, cache: TextureCache, mat_root: Path, cd: str, opts: Options,
                     vmt_cd: str | None = None) -> MatResult:
    """Write ``<mat_root>/<vmt_cd or cd>/<name>.vmt``; textures go to ``<mat_root>/<cd>/tex`` (shared by every
    model of the source). ``cd`` e.g. ``omni/007fl``."""
    vmt_cd = vmt_cd or cd
    res = MatResult(mat.name, f"{vmt_cd}/{mat.name}", unknown_slots=list(mat.unknown_slots))
    out_dir = mat_root / cd
    out_dir.mkdir(parents=True, exist_ok=True)
    vmt: dict[str, str] = {}

    lk = sh.look(mat, cache)
    res.notes += lk.notes
    res.roles = {t.role: t.key for t in mat.textures}
    alpha_test, translucent, decal = lk.alpha_test, lk.translucent, lk.decal

    # ---- base colour ---------------------------------------------------------------
    base_ref, base_tex = lk.base_ref, lk.base_tex
    if base_tex is not None:
        uses_alpha = lk.uses_alpha
        coverage = alpha_test and base_tex.fmt not in ("BC1", "BC3")   # re-encoded anyway: fix the alpha mips
        bname = f"b_{base_ref.key[-8:].lower()}{lk.key}{'_c' if coverage else ''}"
        bpath = out_dir / "tex" / f"{bname}.vtf"
        if not _fresh(bpath, uses_alpha, _top(base_tex, opts.max_size)):
            if lk.key or coverage or not tx.passthrough(base_tex, bpath, opts.max_size, alpha=uses_alpha):
                tx.encode([lk.albedo(cache, opts.max_size)], bpath, alpha=uses_alpha, coverage=0.5 if coverage else 0.0)
        vmt["$basetexture"] = f"{cd}/tex/{bname}"
    else:
        vmt["$basetexture"] = "models/debug/debugwhite"
        vmt["$color2"] = _vmt_vec(lk.constant)
        res.notes.append("no base texture")

    # ---- specular data -------------------------------------------------------------------------
    surf = sh.surface(mat, cache, opts.max_size_spec, lk)
    want_phong = opts.phong and surf is not None and not decal
    if want_phong:
        spec, rough, metal = surf.spec, np.clip(surf.rough, 0.0, 1.0), surf.metal
        # highlight intensity: dielectrics scale with their specular level, metals are always strong;
        # rough surfaces spread the same energy over a wide lobe, so their peak is dimmer.
        gloss = 1.0 - rough
        intensity = (1.0 - metal) * spec * gloss ** 1.5 + metal * gloss ** 0.75
        mask = np.clip(intensity, 0.0, 1.0)
        metal_mean = float(metal.mean())
        rough_mean = float(rough.mean())
        # uniform surfaces need no per-pixel maps: a constant costs nothing (and halves the normal map: DXT1 not DXT5)
        flat_mask = float(mask.std()) < 0.04
        flat_surface = float(rough.std()) < 0.04 and float(metal.std()) < 0.04

    # ---- normal (+ specular mask in alpha) ---------------------------------------------------
    nrm_ref = sh.first(mat, "normal")
    nrm_tex = cache.get(nrm_ref.key) if nrm_ref else None
    if not sh.usable(nrm_tex):
        nrm_tex = None                      # 1x1/4x4 placeholder = flat normal, nothing to add
    if nrm_tex is not None or want_phong:
        sref = surf.ref if want_phong else None
        # "_y": green flipped to Source's convention (files written before that are not reused)
        nname = (f"n_{(nrm_ref.key[-8:] if nrm_tex is not None else 'flat0000').lower()}"
                 f"_{sref.key[-4:].lower() if sref and not flat_mask else '0000'}_y")
        if opts.lossless_normals:
            nname += "_l"
        npath = out_dir / "tex" / f"{nname}.vtf"
        if not _fresh(npath, size=_top(nrm_tex, opts.max_size_normal) if nrm_tex is not None else 0):
            if nrm_tex is not None:
                img = sh.normal(nrm_tex, opts.max_size_normal, flip_y=True)
            else:
                img = np.empty(mask.shape + (4,), np.uint8)
                img[...] = (128, 128, 255, 255)
            if want_phong and not flat_mask:
                h, w = img.shape[:2]
                full = np.clip(mask * 255.0 + 0.5, 0, 255).astype(np.uint8)
                img = img.copy()
                img[..., 3] = tx.resize(full[..., None].repeat(3, -1), w, h)[..., 0]
            tx.encode([img], npath, alpha=want_phong and not flat_mask, flags=vtf.FLAG_NORMAL, lossless=opts.lossless_normals)
        vmt["$bumpmap"] = f"{cd}/tex/{nname}"

    if want_phong and float(mask.max()) > 0.02:
        if flat_surface:
            vmt["$phongexponent"] = str(int(round(float(roughness_to_exponent(np.float32(rough_mean))))))
        else:
            xname = f"x_{surf.ref.key[-8:].lower()}_{surf.ref.channels.strip('_').lower() or '3'}"
            xpath = out_dir / "tex" / f"{xname}.vtf"
            if not xpath.exists():
                img = np.zeros(rough.shape + (4,), np.uint8)
                img[..., 0] = np.clip(roughness_to_exponent(rough) / 150.0 * 255.0 + 0.5, 0, 255)
                img[..., 1] = np.clip(metal * 255.0 + 0.5, 0, 255)
                img[..., 3] = 255
                tx.encode([img], xpath, alpha=False, kind="linear", max_size=opts.max_size_secondary)
            vmt["$phongexponenttexture"] = f"{cd}/tex/{xname}"
        vmt["$phong"] = "1"
        if flat_mask:
            vmt["$phongboost"] = f"{max(0.1, 2.0 * float(mask.mean())):.2f}"
        else:
            vmt["$normalmapalphaphongmask"] = "1"
            vmt["$phongboost"] = "2"
        vmt["$phongfresnelranges"] = "[1 1 1]" if metal_mean > 0.5 else "[0.1 0.5 1]"
        if metal_mean > 0.05:
            vmt["$phongalbedotint"] = "1"
        if opts.envmap and metal_mean >= 0.15:
            vmt["$envmap"] = "env_cubemap"
            if not flat_mask:
                vmt["$normalmapalphaenvmapmask"] = "1"
            k = 0.6 * metal_mean * (1.0 - rough_mean) * (float(mask.mean()) if flat_mask else 1.0)
            vmt["$envmaptint"] = _vmt_vec((k, k, k))

    if lk.glass and translucent and opts.envmap and "$envmap" not in vmt:
        vmt["$envmap"] = "env_cubemap"
        vmt["$envmaptint"] = "[0.3 0.3 0.3]"

    # ---- emissive ---------------------------------------------------------------------------
    emi_ref = sh.first(mat, "emissive")
    if emi_ref is not None:
        ename = f"s_{emi_ref.key[-8:].lower()}"
        epath = out_dir / "tex" / f"{ename}.vtf"
        lit = epath.exists()
        if not lit:
            e = sh.emissive(mat, cache, opts.max_size_secondary)
            if e is not None:
                grey = np.repeat(e[..., :3].max(-1, keepdims=True), 4, -1).astype(np.uint8)
                grey[..., 3] = 255
                tx.encode([grey], epath, alpha=False, kind="linear")
                lit = True
        if lit:
            vmt["$selfillum"] = "1"
            vmt["$selfillummask"] = f"{cd}/tex/{ename}"

    # ---- blending ----------------------------------------------------------------------------
    if alpha_test:
        vmt["$alphatest"] = "1"
        vmt["$allowalphatocoverage"] = "1"
        vmt["$nocull"] = "1"
    elif translucent:
        vmt["$translucent"] = "1"
        res.translucent = True
    if "two_sided" in mat.flags:
        vmt["$nocull"] = "1"                # the game draws both faces (leaves, cloth, thin panels)
    if lk.overlay:
        vmt["$decal"] = "1"
    res.drop = lk.drop

    body = ['"VertexLitGeneric"', "{"]
    body += [f'\t"{k}" "{v}"' for k, v in vmt.items()]
    body.append("}")
    vmt_dir = mat_root / vmt_cd
    vmt_dir.mkdir(parents=True, exist_ok=True)
    (vmt_dir / f"{mat.name}.vmt").write_text("\n".join(body) + "\n", encoding="ascii")
    return res
