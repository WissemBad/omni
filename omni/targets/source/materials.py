"""Neutral Material -> Source VMT + VTFs (VertexLitGeneric).

Every VMT key written here is derived from the source material's data; nothing is added "just in case":

  $basetexture               base colour. BC1/BC3 copied byte-for-byte; BC7 re-encoded (Source cannot read BC7).
  $bumpmap                   normal map (BC5 -> RGB with Z rebuilt).
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

Colour rules taken from the source shaders (details in colour_model):
  * fabric / grey-pattern materials: the base map is a neutral grey (its RGB only carries a faint variation),
    the colour comes from BaseColor x the weave colours; their base alpha is a baked ambient occlusion
    (applied with the material's AmbientOcclusion strength), not an opacity.
  * colour constants are sRGB-encoded, combined in linear space; multipliers are neutral at 0.5.
  * outfits (game entity templates) override these constants per character: see sources/glacier/outfit.py.
  * hair (Glacier hair shader): the base alpha is the strand coverage -> alpha test, coverage-preserving mips.
  * eyes (eye shader): the base alpha is the iris mask of the cornea shader, not an opacity -> opaque.
  * no specular map but specular constants (fabric Specular_Color + Roughness, hair RoughnessMin/Max, eye
    cornea): one constant phong lobe built from those numbers.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.ir import Material, TextureData
from . import textures as tx
from . import vtf


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


class TextureCache:
    def __init__(self, source, limit: int = 24):
        self.source, self.limit, self._c = source, limit, {}

    def get(self, key: str) -> TextureData | None:
        if key in self._c:
            return self._c[key]
        t = self.source.load_texture(int(key, 16))
        if len(self._c) >= self.limit:
            self._c.pop(next(iter(self._c)))
        self._c[key] = t
        return t


def _first(mat: Material, *roles: str):
    for r in roles:
        for t in mat.textures:
            if t.role == r:
                return t
    return None


def _param(mat: Material, name: str, default=None):
    v = mat.params.get(name)
    return v[0] if v else default


def _rgb(a: np.ndarray) -> np.ndarray:
    return a[..., :3].astype(np.float32) / 255.0


def roughness_to_exponent(r: np.ndarray) -> np.ndarray:
    """PBR roughness -> Blinn-Phong exponent (alpha = r^2, n = 2/alpha^2 - 2), clamped to Source's 1..150."""
    a = np.clip(r, 0.05, 1.0) ** 2
    return np.clip(2.0 / (a * a) - 2.0, 1.0, 150.0)


def _vmt_vec(v) -> str:
    return "[" + " ".join(f"{x:.3f}" for x in v) + "]"


def _is_grey_pattern(tex) -> bool:
    """A base map with (almost) no colour of its own: the colour then comes from a material constant."""
    if tex is None or tex.width <= 4:
        return False
    small = [mp for mp in tex.mips if max(mp[0], mp[1]) <= 64] or [tex.mips[-1]]
    from ...sources.glacier.texture import Mip, to_rgba
    a = to_rgba(tex.fmt, Mip(*small[0]))[..., :3].astype(np.float32)
    return float((a.max(-1) - a.min(-1)).mean()) < 6.0 and float(a.std()) < 25.0


def _fresh(path: Path, alpha: bool = False, size: int = 0) -> bool:
    """An already converted texture can be reused, unless the material now needs an alpha channel that the
    file does not have, or it was written at another quality setting (``size``: largest side expected)."""
    if not path.exists():
        return False
    with open(path, "rb") as f:
        head = f.read(56)
    if len(head) < 56:
        return False
    if alpha and int.from_bytes(head[52:56], "little", signed=True) == vtf.DXT1:
        return False
    if size and max(int.from_bytes(head[16:18], "little"), int.from_bytes(head[18:20], "little")) != size:
        return False
    return True


def _top(tex, max_size: int) -> int:
    """Largest side of the biggest game mip that fits ``max_size`` (the size the converted file will have)."""
    m = tx._cap(tex.mips, max_size)[0]
    return max(m[0], m[1])


def _is_hair(mat: Material) -> bool:
    """Glacier hair shader (strand cards): recognised by its hair-specific texture slots."""
    return any(t.slot.lower().startswith("maphair") for t in mat.textures) or any(
        s.lower().startswith("maphair") for s in mat.unknown_slots)


def _is_eye(mat: Material) -> bool:
    return any(k.startswith("EyeShader_") for k in mat.params)


def _constant_spec(mat: Material, is_hair: bool, is_eye: bool):
    """(specular level, roughness, metallic) from material constants when there is no specular map."""
    if is_eye:
        return 0.5, 0.1, 0.0                         # wet cornea: smooth dielectric
    p = mat.params
    if is_hair and "RoughnessMin" in p and "RoughnessMax" in p:
        return 0.5, 0.5 * (p["RoughnessMin"][0] + p["RoughnessMax"][0]), 0.0
    if "Specular_Color" in p and "Roughness" in p:  # fabric shaders (sheen colour + roughness)
        return float(np.mean(p["Specular_Color"][:3])), float(p["Roughness"][0]), 0.0
    return None


def _lin(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _srgb(c):
    c = np.clip(np.asarray(c, np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


@dataclass
class ColourModel:
    """Albedo = base map (optionally normalised to an average of 1) x factor, in linear space,
    x baked AO from the base alpha. ``factor`` gathers the shader colour constants."""
    kind: str
    factor: np.ndarray                 # linear RGB multiplier
    normalise: bool                    # grey pattern map: keep only its variation
    ao: float | None = None            # strength of the AO stored in the base alpha
    ao_alpha: bool = False             # the base alpha is AO (never an opacity)

    def signature(self):
        return self.kind, tuple(round(float(x), 4) for x in self.factor), self.normalise, self.ao

    def apply(self, rgba: np.ndarray, keep_alpha: bool) -> np.ndarray:
        lin = _lin(rgba[..., :3].astype(np.float32) / 255.0)
        if self.normalise:
            small = lin[:: max(1, lin.shape[0] // 64), :: max(1, lin.shape[1] // 64)]
            lin = lin / max(float(small.mean()), 0.02)
        lin = lin * self.factor
        if self.ao is not None:
            lin = lin * (1.0 - float(self.ao) * (1.0 - rgba[..., 3:4].astype(np.float32) / 255.0))
        out = rgba.copy()
        out[..., :3] = np.clip(_srgb(lin) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if not keep_alpha:
            out[..., 3] = 255
        return out


def _rgb3(mat: Material, name: str):
    v = mat.params.get(name)
    return np.array(v[:3], np.float32) if v is not None and len(v) >= 3 else None


def colour_model(mat: Material, base, is_hair: bool, alpha_cut: bool) -> ColourModel | None:
    """How the game's shader colours the base map, from the material constants. Conventions (consistent over
    the outfits checked): colour constants are sRGB-encoded and multiplied in linear space; the *Mult /
    *_Multiplier constants are 0.5 when neutral (x2 inside the shader: gloves 0.8 = brighter, boots 4.0 = a
    near-black leather dyed tan).

      fabric / grey-pattern maps   BaseColor x mean(WeaveColor, WeftColor) x 2*BaseColorMult, map normalised,
                                   base alpha = baked AO (AmbientOcclusion strength), BaseColorSaturation
      basic shader                 BaseColor_Modulate x 2*BaseColor_Multiplier
      hair                         BaseColor x 2*BaseColorMult
      skin                         SkinColor (BaseColorMult belongs to the skin shading, not the albedo)
    Returns None when the constants leave the map unchanged (the map is then copied as is)."""
    bc = _rgb3(mat, "BaseColor")
    if is_hair:
        if bc is None:
            return None
        f = _lin(np.clip(bc, 0, None)) * 2.0 * _param(mat, "BaseColorMult", 0.5)
        return None if np.allclose(f, 1.0, atol=0.01) else ColourModel("hair", f, False)
    if "class:skin" in mat.flags:
        sc = _rgb3(mat, "SkinColor")
        return None if sc is None or np.allclose(sc, 1.0, atol=0.01) else ColourModel("skin", _lin(sc), False)
    if bc is not None and ("class:fabric" in mat.flags or _is_grey_pattern(base)):
        f = _lin(np.clip(bc, 0, None))
        w, wf = _rgb3(mat, "WeaveColor"), _rgb3(mat, "WeftColor")
        if w is not None and wf is not None:
            f = f * 0.5 * (_lin(np.clip(w, 0, None)) + _lin(np.clip(wf, 0, None)))
        f = f * 2.0 * _param(mat, "BaseColorMult", 0.5)
        sat = _param(mat, "BaseColorSaturation", 1.0)
        if abs(sat - 1.0) > 1e-3:
            luma = float(f @ np.array([0.2126, 0.7152, 0.0722], np.float32))
            f = luma + (f - luma) * max(0.0, sat)
        ao = None
        if _param(mat, "Alpha_Discard", 0.0) <= 0.0 and not alpha_cut:
            ao = _param(mat, "AmbientOcclusion")
        return ColourModel("fabric", f.astype(np.float32), True, ao, ao_alpha=True)
    mod = _rgb3(mat, "BaseColor_Modulate")
    if mod is not None:
        f = _lin(np.clip(mod, 0, None)) * 2.0 * _param(mat, "BaseColor_Multiplier", 0.5)
        return None if np.allclose(f, 1.0, atol=0.01) else ColourModel("modulate", f.astype(np.float32), False)
    return None


def convert_material(mat: Material, cache: TextureCache, mat_root: Path, cd: str, opts: Options,
                     vmt_cd: str | None = None) -> MatResult:
    """Write ``<mat_root>/<vmt_cd or cd>/<name>.vmt``; textures go to ``<mat_root>/<cd>/tex`` (shared by every
    model of the source). ``cd`` e.g. ``omni/007fl``."""
    vmt_cd = vmt_cd or cd
    res = MatResult(mat.name, f"{vmt_cd}/{mat.name}", unknown_slots=list(mat.unknown_slots))
    out_dir = mat_root / cd
    out_dir.mkdir(parents=True, exist_ok=True)
    vmt: dict[str, str] = {}

    hint = (mat.source_name or mat.name).lower()
    decal = "decal" in mat.flags or "decal" in hint            # dirt/rust/marking layers
    overlay = decal or "overlay" in mat.flags                   # anything drawn on top of other geometry
    alpha_test = "alpha_test" in mat.flags
    translucent = "translucent" in mat.flags
    is_glass = "glass" in hint or "class:glass" in mat.flags
    base_ref, nrm_ref = _first(mat, "base"), _first(mat, "normal")
    srm_ref, spec_ref = _first(mat, "srm"), _first(mat, "spec")
    emi_ref = _first(mat, "emissive")
    res.roles = {t.role: t.key for t in mat.textures}
    is_hair, is_eye = _is_hair(mat), _is_eye(mat)
    if is_hair:
        alpha_test = True                   # strand cards: the base alpha is the strand coverage
    if is_eye:
        alpha_test = translucent = False    # the base alpha is the iris mask of the cornea shader

    # ---- colour-mask tint (basicmaterial_colormask): base * c01 * mask(R,G,B) -> next 3 constants ----
    tint_key = ""
    mask_ref = None
    if "class:colormask" in mat.flags and base_ref is not None:
        cols = {int(k.split("_")[1]): v[:3] for k, v in mat.params.items() if k.startswith("ConstantColorRGB_") and len(v) >= 3}
        best = None
        for t in (t for t in mat.textures if t.role == "mask"):
            mt = cache.get(t.key)
            if mt is not None and mt.width > 4 and (best is None or mt.width * mt.height > best[1]):
                best = (t, mt.width * mt.height)
        identity = all(abs(x - 1.0) < 1e-3 for v in cols.values() for x in v)
        if best and cols and not identity:
            mask_ref = best[0]
            sig = repr((mask_ref.key, sorted((k, tuple(round(x, 4) for x in v)) for k, v in cols.items()))).encode()
            tint_key = "_t" + hashlib.sha1(sig).hexdigest()[:6]

    # ---- shader colour constants (BaseColor, weave, modulate, multipliers): see colour_model ----------
    colour = None
    if not tint_key and base_ref is not None:
        colour = colour_model(mat, cache.get(base_ref.key), is_hair, alpha_test or translucent)
        if colour is not None:
            tint_key = "_c" + hashlib.sha1(repr(colour.signature()).encode()).hexdigest()[:6]

    # ---- base colour ---------------------------------------------------------------
    base_tex = cache.get(base_ref.key) if base_ref else None
    if base_tex is not None and not (alpha_test or translucent or is_eye or (colour is not None and colour.ao_alpha)) \
            and tx.has_alpha(base_tex):
        if is_glass or decal:
            translucent = True
        else:
            alpha_test = True
        res.notes.append("alpha detected in base texture")
    if base_tex is not None:
        uses_alpha = alpha_test or translucent
        coverage = alpha_test and base_tex.fmt not in ("BC1", "BC3")   # re-encoded anyway: fix the alpha mips
        bname = f"b_{base_ref.key[-8:].lower()}{tint_key}{'_c' if coverage else ''}"
        bpath = out_dir / "tex" / f"{bname}.vtf"
        todo = not _fresh(bpath, uses_alpha, _top(base_tex, opts.max_size))
        if todo and tint_key.startswith("_c"):
            mips = [colour.apply(a, uses_alpha) for a in tx.decode_mips_rgba(base_tex, opts.max_size)]
            tx.encode(mips, bpath, alpha=uses_alpha, coverage=0.5 if coverage else 0.0)
        elif todo and tint_key.startswith("_t"):
            mips = tx.decode_mips_rgba(base_tex, opts.max_size)
            mk = tx.decode_mips_rgba(cache.get(mask_ref.key), opts.max_size)[0]
            cols = {int(k.split("_")[1]): v[:3] for k, v in mat.params.items() if k.startswith("ConstantColorRGB_") and len(v) >= 3}
            c01 = np.array(cols.pop(1, (1.0, 1.0, 1.0)), np.float32)
            chans = [np.array(cols[k], np.float32) for k in sorted(cols)][:3]
            for i, a in enumerate(mips):
                h, w = a.shape[:2]
                mm = tx.resize(mk, w, h).astype(np.float32) / 255.0
                tint = np.ones((h, w, 3), np.float32)
                for ch, col in enumerate(chans):
                    mc = mm[..., ch:ch + 1]
                    tint = tint * (1.0 - mc) + col * mc
                out = a.astype(np.float32)
                out[..., :3] = np.clip(out[..., :3] * tint * c01, 0, 255)
                mips[i] = out.astype(np.uint8)
            tx.encode(mips, bpath, alpha=uses_alpha)
        elif todo:
            if coverage:
                tx.encode(tx.decode_mips_rgba(base_tex, opts.max_size), bpath, alpha=True, coverage=0.5)
            elif not tx.passthrough(base_tex, bpath, opts.max_size, alpha=uses_alpha):
                tx.encode(tx.decode_mips_rgba(base_tex, opts.max_size), bpath, alpha=uses_alpha)
        vmt["$basetexture"] = f"{cd}/tex/{bname}"
    else:
        vmt["$basetexture"] = "models/debug/debugwhite"
        vmt["$color2"] = "[0.55 0.55 0.55]"
        res.notes.append("no base texture")

    # ---- specular data (SRM: R specular, G roughness, B metallic; or a plain spec map) ----------
    spec = rough = metal = None
    srm_tex = cache.get(srm_ref.key) if srm_ref else None
    if srm_tex is not None and srm_tex.width > 4:
        f = _rgb(tx.decode_mips_rgba(srm_tex, opts.max_size_spec)[0])
        spec, rough, metal = f[..., 0], f[..., 1], f[..., 2]
        rmin, rmax = _param(mat, "Roughness_Min", 0.0), _param(mat, "Roughness_Max", 1.0)
        rough = rmin + rough * (rmax - rmin)
    elif spec_ref is not None:
        st = cache.get(spec_ref.key)
        if st is not None and st.width > 4:
            # specular/gloss workflow: RGB = specular colour (sRGB), alpha = gloss
            a = tx.decode_mips_rgba(st, opts.max_size_spec)[0]
            f0 = (_rgb(a) ** 2.2).mean(-1)                   # linear reflectance at normal incidence
            spec = np.clip(f0 / 0.08, 0.0, 1.0)               # 4 % (plastic, wood, paint) -> 0.5 like an SRM
            metal = np.clip((f0 - 0.04) / 0.5, 0.0, 1.0)      # metals reflect 50-100 %
            gloss = a[..., 3].astype(np.float32) / 255.0
            if float(gloss.std()) < 0.01 and float(gloss.mean()) > 0.99:   # no gloss channel: material constant
                g = _param(mat, "ShaderLOD_Gloss")
                gloss = np.full_like(f0, g if g is not None else 1.0 - _param(mat, "ShaderLOD_Roughness", 0.5))
            rough = 1.0 - gloss
    if spec is None:
        const = _constant_spec(mat, is_hair, is_eye)
        if const is not None:
            spec, rough, metal = (np.full((4, 4), v, np.float32) for v in const)

    want_phong = opts.phong and spec is not None and not decal
    if want_phong:
        # highlight intensity: dielectrics scale with their specular level, metals are always strong;
        # rough surfaces spread the same energy over a wide lobe, so their peak is dimmer.
        gloss = 1.0 - np.clip(rough, 0.0, 1.0)
        intensity = (1.0 - metal) * spec * gloss ** 1.5 + metal * gloss ** 0.75
        mask = np.clip(intensity, 0.0, 1.0)
        metal_mean = float(metal.mean())
        rough_mean = float(rough.mean())
        # uniform surfaces need no per-pixel maps: a constant costs nothing (and halves the normal map: DXT1 not DXT5)
        flat_mask = float(mask.std()) < 0.04
        flat_surface = float(rough.std()) < 0.04 and float(metal.std()) < 0.04

    # ---- normal (+ specular mask in alpha) ---------------------------------------------------
    nrm_tex = cache.get(nrm_ref.key) if nrm_ref else None
    if nrm_tex is not None and nrm_tex.width <= 4:
        nrm_tex = None                      # 1x1/4x4 placeholder = flat normal, nothing to add
    if nrm_tex is not None or want_phong:
        sref = (srm_ref or spec_ref) if want_phong else None
        nname = f"n_{(nrm_ref.key[-8:] if nrm_tex is not None else 'flat0000').lower()}_{sref.key[-4:].lower() if sref and not flat_mask else '0000'}"
        if opts.lossless_normals:
            nname += "_l"
        npath = out_dir / "tex" / f"{nname}.vtf"
        if not _fresh(npath, size=_top(nrm_tex, opts.max_size_normal) if nrm_tex is not None else 0):
            if nrm_tex is not None:
                mips = tx.decode_mips_rgba(nrm_tex, opts.max_size_normal)
                if nrm_tex.fmt in ("BC5", "BC4", "RG8"):
                    mips = [tx.rebuild_normal(a) for a in mips]
            else:
                flat = np.empty(mask.shape + (4,), np.uint8)
                flat[...] = (128, 128, 255, 255)
                mips = [flat]
            if want_phong and not flat_mask:
                full = np.clip(mask * 255.0 + 0.5, 0, 255).astype(np.uint8)
                for i, a in enumerate(mips):
                    h, w = a.shape[:2]
                    a = a.copy()
                    a[..., 3] = tx.resize(full[..., None].repeat(3, -1), w, h)[..., 0]
                    mips[i] = a
            tx.encode(mips, npath, alpha=want_phong and not flat_mask, flags=vtf.FLAG_NORMAL, lossless=opts.lossless_normals)
        vmt["$bumpmap"] = f"{cd}/tex/{nname}"

    if want_phong and float(mask.max()) > 0.02:
        if flat_surface:
            vmt["$phongexponent"] = str(int(round(float(roughness_to_exponent(np.float32(rough_mean))))))
        else:
            xname = f"x_{(srm_ref or spec_ref).key[-8:].lower()}_3"
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

    if is_glass and translucent and opts.envmap and "$envmap" not in vmt:
        vmt["$envmap"] = "env_cubemap"
        vmt["$envmaptint"] = "[0.3 0.3 0.3]"

    # ---- emissive ---------------------------------------------------------------------------
    if emi_ref is not None:
        et = cache.get(emi_ref.key)
        if et is not None and et.width > 4:
            mips = tx.decode_mips_rgba(et, opts.max_size_secondary)
            if float(mips[0][..., :3].max(-1).mean()) > 6.0:
                ename = f"s_{emi_ref.key[-8:].lower()}"
                epath = out_dir / "tex" / f"{ename}.vtf"
                if not epath.exists():
                    grey = [np.repeat(a[..., :3].max(-1, keepdims=True), 4, -1).astype(np.uint8) for a in mips]
                    for g in grey:
                        g[..., 3] = 255
                    tx.encode(grey, epath, alpha=False, kind="linear")
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
    if overlay:
        vmt["$decal"] = "1"
    # a decal whose base colour is opaque (or missing) would paint a solid patch over the surface
    res.drop = decal and not (base_tex is not None and (alpha_test or translucent))

    body = ['"VertexLitGeneric"', "{"]
    body += [f'\t"{k}" "{v}"' for k, v in vmt.items()]
    body.append("}")
    vmt_dir = mat_root / vmt_cd
    vmt_dir.mkdir(parents=True, exist_ok=True)
    (vmt_dir / f"{mat.name}.vmt").write_text("\n".join(body) + "\n", encoding="ascii")
    return res
