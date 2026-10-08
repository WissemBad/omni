"""What a neutral Material looks like, as images any target can encode (Source VMT/VTF, glTF, Blender).

Every target reads the game's material the same way: the albedo after the shader's colour constants, the alpha
meaning (cut-out, blended, or other data), the tangent-space normal, and the surface terms (specular level,
roughness, metallic, occlusion) from whatever maps the game has. Game-independent: it only reads the IR.

Colour rules taken from the source shaders (details in colour_model):
  * fabric / grey-pattern materials: the base map is a neutral grey (its RGB only carries a faint variation),
    the colour comes from BaseColor x the weave colours; their base alpha is a baked ambient occlusion
    (applied with the material's AmbientOcclusion strength), not an opacity.
  * colour constants are sRGB-encoded, combined in linear space; multipliers are neutral at 0.5.
  * colour-mask materials: base x ConstantColorRGB_01, then each mask channel blends to the next constant.
  * hair (Glacier hair shader): the base alpha is the strand coverage -> alpha test.
  * eyes (eye shader): the base alpha is the iris mask of the cornea shader, not an opacity -> opaque.
Surface rules:
  * packed maps follow the layout their class declares (``TextureRef.channels``); an SRM is R specular level,
    G roughness, B metallic, remapped by Roughness_Min/Max; gloss channels are inverted into roughness.
  * specular/gloss maps: RGB = specular colour (sRGB; ~4 % reflectance for dielectrics), alpha = gloss.
  * Unreal: packed occlusion-roughness-metal (channel order from the slot) or separate roughness/metal maps.
  * no map but specular constants (fabric Specular_Color + Roughness, hair RoughnessMin/Max, eye cornea): constants.
Normals: the game's maps point green up (OpenGL), like glTF/Blender; Source 1 needs green down (``flip_y``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from ..core.ir import Material, TextureData
from ..native import N
from ..sources.glacier.texture import Mip, to_rgba
from .source import textures as tx


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


def first(mat: Material, *roles: str):
    for r in roles:
        for t in mat.textures:
            if t.role == r:
                return t
    return None


def param(mat: Material, name: str, default=None):
    v = mat.params.get(name)
    return v[0] if v else default


def rgb(a: np.ndarray) -> np.ndarray:
    return a[..., :3].astype(np.float32) / 255.0


def lin(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


_LIN8 = lin(np.arange(256, dtype=np.float32) / 255.0).astype(np.float32)


def srgb(c):
    c = np.clip(np.asarray(c, np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def usable(tex) -> bool:
    """A real map, not the 1x1/4x4 placeholder a class uses for a slot the material leaves empty."""
    return tex is not None and tex.width > 4


def is_hair(mat: Material) -> bool:
    """Glacier hair shader (strand cards): recognised by its hair-specific texture slots."""
    return any(t.slot.lower().startswith("maphair") for t in mat.textures) or any(
        s.lower().startswith("maphair") for s in mat.unknown_slots)


def is_eye(mat: Material) -> bool:
    return any(k.startswith("EyeShader_") for k in mat.params)


def _is_grey_pattern(tex) -> bool:
    """A base map with (almost) no colour of its own: the colour then comes from a material constant."""
    if not usable(tex):
        return False
    small = [mp for mp in tex.mips if max(mp[0], mp[1]) <= 64] or [tex.mips[-1]]
    a = to_rgba(tex.fmt, Mip(*small[0]))[..., :3].astype(np.float32)
    return float((a.max(-1) - a.min(-1)).mean()) < 6.0 and float(a.std()) < 25.0


# ---- colour ------------------------------------------------------------------------------------------------
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
        lin8 = _LIN8[rgba[..., :3]]                # sRGB -> linear of 8-bit values: a table, not a power per pixel
        if self.normalise:
            small = lin8[:: max(1, lin8.shape[0] // 64), :: max(1, lin8.shape[1] // 64)]
            lin8 = lin8 / max(float(small.mean()), 0.02)
        lin8 = lin8 * self.factor
        if self.ao is not None:
            lin8 = lin8 * (1.0 - float(self.ao) * (1.0 - rgba[..., 3:4].astype(np.float32) / 255.0))
        out = rgba.copy()
        out[..., :3] = np.clip(srgb(lin8) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if not keep_alpha:
            out[..., 3] = 255
        return out


def _rgb3(mat: Material, name: str):
    v = mat.params.get(name)
    return np.array(v[:3], np.float32) if v is not None and len(v) >= 3 else None


def colour_model(mat: Material, base, hair: bool, alpha_cut: bool) -> ColourModel | None:
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
    if hair:
        if bc is None:
            return None
        f = lin(np.clip(bc, 0, None)) * 2.0 * param(mat, "BaseColorMult", 0.5)
        return None if np.allclose(f, 1.0, atol=0.01) else ColourModel("hair", f, False)
    if "class:skin" in mat.flags:
        sc = _rgb3(mat, "SkinColor")
        return None if sc is None or np.allclose(sc, 1.0, atol=0.01) else ColourModel("skin", lin(sc), False)
    if bc is not None and ("class:fabric" in mat.flags or _is_grey_pattern(base)):
        f = lin(np.clip(bc, 0, None))
        w, wf = _rgb3(mat, "WeaveColor"), _rgb3(mat, "WeftColor")
        if w is not None and wf is not None:
            f = f * 0.5 * (lin(np.clip(w, 0, None)) + lin(np.clip(wf, 0, None)))
        f = f * 2.0 * param(mat, "BaseColorMult", 0.5)
        sat = param(mat, "BaseColorSaturation", 1.0)
        if abs(sat - 1.0) > 1e-3:
            luma = float(f @ np.array([0.2126, 0.7152, 0.0722], np.float32))
            f = luma + (f - luma) * max(0.0, sat)
        ao = None
        if param(mat, "Alpha_Discard", 0.0) <= 0.0 and not alpha_cut:
            ao = param(mat, "AmbientOcclusion")
        return ColourModel("fabric", f.astype(np.float32), True, ao, ao_alpha=True)
    mod = _rgb3(mat, "BaseColor_Modulate")
    if mod is not None:
        f = lin(np.clip(mod, 0, None)) * 2.0 * param(mat, "BaseColor_Multiplier", 0.5)
        return None if np.allclose(f, 1.0, atol=0.01) else ColourModel("modulate", f.astype(np.float32), False)
    return None


@dataclass
class Look:
    """The decisions about a material's base colour: which map, how the shader colours it, what its alpha means.
    ``key`` names the coloured variant (two materials with the same map and constants share one texture)."""
    base_ref: object = None
    base_tex: TextureData | None = None
    colour: ColourModel | None = None
    mask_ref: object = None
    tints: tuple = ()                  # (ConstantColorRGB_01, [mask channel colours]) of a colour-mask material
    key: str = ""
    alpha_test: bool = False
    translucent: bool = False
    decal: bool = False
    overlay: bool = False
    glass: bool = False
    hair: bool = False
    eye: bool = False
    constant: tuple = (0.55, 0.55, 0.55)   # sRGB colour of a material without a base map (procedural shaders)
    notes: list = field(default_factory=list)

    @property
    def uses_alpha(self) -> bool:
        return self.alpha_test or self.translucent

    @property
    def drop(self) -> bool:
        """A decal whose base colour is opaque (or missing) would paint a solid patch over the surface."""
        return self.decal and not (self.base_tex is not None and self.uses_alpha)

    def albedo(self, cache: TextureCache, max_size: int) -> np.ndarray | None:
        """RGBA of the base colour at the largest game size that fits ``max_size``; the alpha is kept only when
        it is an opacity. Without a base map: a small image of the material's constant colour."""
        if self.base_tex is None:
            c = np.empty((4, 4, 4), np.uint8)
            c[..., :3] = np.clip(np.asarray(self.constant) * 255.0 + 0.5, 0, 255).astype(np.uint8)
            c[..., 3] = 255
            return c
        a = tx.decode_top(self.base_tex, max_size)
        if self.colour is not None:
            return self.colour.apply(a, self.uses_alpha)
        if self.mask_ref is not None:
            c01, chans = self.tints
            h, w = a.shape[:2]
            mm = tx.resize(tx.decode_top(cache.get(self.mask_ref.key), max_size), w, h).astype(np.float32) / 255.0
            tint = np.ones((h, w, 3), np.float32)
            for ch, col in enumerate(chans):
                mc = mm[..., ch:ch + 1]
                tint = tint * (1.0 - mc) + col * mc
            out = a.astype(np.float32)
            out[..., :3] = np.clip(out[..., :3] * tint * c01, 0, 255)
            a = out.astype(np.uint8)
        if not self.uses_alpha:
            a = a.copy()
            a[..., 3] = 255
        return a


def look(mat: Material, cache: TextureCache) -> Look:
    hint = (mat.source_name or mat.name).lower()
    lk = Look(decal="decal" in mat.flags or "decal" in hint, alpha_test="alpha_test" in mat.flags,
              translucent="translucent" in mat.flags, glass="glass" in hint or "class:glass" in mat.flags,
              hair=is_hair(mat), eye=is_eye(mat))
    lk.overlay = lk.decal or "overlay" in mat.flags
    if lk.hair:
        lk.alpha_test = True                   # strand cards: the base alpha is the strand coverage
    if lk.eye:
        lk.alpha_test = lk.translucent = False  # the base alpha is the iris mask of the cornea shader
    lk.base_ref = first(mat, "base")
    lk.base_tex = cache.get(lk.base_ref.key) if lk.base_ref else None
    if lk.base_tex is None:
        bc = mat.params.get("BaseColor")
        if bc:
            lk.constant = tuple(float(np.clip(x, 0.0, 1.0)) for x in (bc[:3] if len(bc) >= 3 else bc[:1] * 3))

    # colour-mask tint (basicmaterial_colormask): base * c01 * mask(R,G,B) -> next 3 constants
    if "class:colormask" in mat.flags and lk.base_tex is not None:
        cols = {int(k.split("_")[1]): v[:3] for k, v in mat.params.items() if k.startswith("ConstantColorRGB_") and len(v) >= 3}
        best = None
        for t in (t for t in mat.textures if t.role == "mask"):
            mt = cache.get(t.key)
            if usable(mt) and (best is None or mt.width * mt.height > best[1]):
                best = (t, mt.width * mt.height)
        identity = all(abs(x - 1.0) < 1e-3 for v in cols.values() for x in v)
        if best and cols and not identity:
            lk.mask_ref = best[0]
            sig = repr((lk.mask_ref.key, sorted((k, tuple(round(x, 4) for x in v)) for k, v in cols.items()))).encode()
            lk.key = "_t" + hashlib.sha1(sig).hexdigest()[:6]
            c01 = np.array(cols.pop(1, (1.0, 1.0, 1.0)), np.float32)
            lk.tints = (c01, [np.array(cols[k], np.float32) for k in sorted(cols)][:3])

    # shader colour constants (BaseColor, weave, modulate, multipliers)
    if not lk.key and lk.base_tex is not None:
        lk.colour = colour_model(mat, lk.base_tex, lk.hair, lk.uses_alpha)
        if lk.colour is not None:
            lk.key = "_c" + hashlib.sha1(repr(lk.colour.signature()).encode()).hexdigest()[:6]

    if lk.base_tex is not None and not (lk.uses_alpha or lk.eye or (lk.colour is not None and lk.colour.ao_alpha)) \
            and tx.has_alpha(lk.base_tex):
        if lk.glass or lk.decal:
            lk.translucent = True
        else:
            lk.alpha_test = True
        lk.notes.append("alpha detected in base texture")
    return lk


# ---- normal ------------------------------------------------------------------------------------------------
def normal(tex: TextureData | None, max_size: int, flip_y: bool = False) -> np.ndarray | None:
    """Tangent-space normal map RGBA (Z rebuilt for X/Y-only formats), green up unless ``flip_y``."""
    if not usable(tex):
        return None                            # 1x1/4x4 placeholder = flat normal, nothing to add
    return N.normal_map(tx.decode_top(tex, max_size), tex.fmt in ("BC5", "BC4", "RG8"), flip_y)


# ---- surface -----------------------------------------------------------------------------------------------
@dataclass
class Surface:
    """Per-pixel specular level, roughness, metallic (float32 0..1, same shape) and occlusion (or None);
    ``ref`` is the texture they come from (None for constants)."""
    spec: np.ndarray
    rough: np.ndarray
    metal: np.ndarray
    ao: np.ndarray | None = None
    ref: object = None


def _fit(a: np.ndarray, shape) -> np.ndarray:
    if a.shape[:2] == shape:
        return a
    ys = np.linspace(0, a.shape[0] - 1, shape[0]).astype(int)
    xs = np.linspace(0, a.shape[1] - 1, shape[1]).astype(int)
    return a[ys][:, xs]


def _constant_spec(mat: Material, hair: bool, eye: bool):
    """(specular level, roughness, metallic) from material constants when there is no specular map."""
    if eye:
        return 0.5, 0.1, 0.0                         # wet cornea: smooth dielectric
    p = mat.params
    if hair and "RoughnessMin" in p and "RoughnessMax" in p:
        return 0.5, 0.5 * (p["RoughnessMin"][0] + p["RoughnessMax"][0]), 0.0
    if "Specular_Color" in p and "Roughness" in p:  # fabric shaders (sheen colour + roughness)
        return float(np.mean(p["Specular_Color"][:3])), float(p["Roughness"][0]), 0.0
    return None


def _packed(mat: Material, ref, tex, max_size: int) -> Surface | None:
    """A packed surface map read with the channel layout its class declares (SRM by default)."""
    layout = ref.channels or "SRM_"
    a = tx.decode_top(tex, max_size).astype(np.float32) / 255.0
    chan = {c: a[..., i] for i, c in enumerate(layout) if c != "_"}
    if "R" in chan:
        rough = chan["R"]
    elif "G" in chan:
        rough = 1.0 - chan["G"]
    else:
        return None
    if ref.role == "srm":                        # the SRM shaders remap the stored roughness
        rmin, rmax = param(mat, "Roughness_Min", 0.0), param(mat, "Roughness_Max", 1.0)
        rough = rmin + rough * (rmax - rmin)
    spec = chan.get("S", np.full_like(rough, 0.5))
    metal = chan.get("M", np.zeros_like(rough))
    return Surface(spec, rough, metal, chan.get("O"), ref)


def surface(mat: Material, cache: TextureCache, max_size: int, lk: Look | None = None) -> Surface | None:
    """The surface terms from whatever the material has, or None when it says nothing about them."""
    srm_ref, spec_ref = first(mat, "srm"), first(mat, "spec")
    srm_tex = cache.get(srm_ref.key) if srm_ref else None
    if usable(srm_tex):
        s = _packed(mat, srm_ref, srm_tex, max_size)
        if s is not None:
            return s
    if spec_ref is not None:
        st = cache.get(spec_ref.key)
        if usable(st):
            # specular/gloss workflow: RGB = specular colour (sRGB), alpha = gloss
            a = tx.decode_top(st, max_size)
            f0 = (rgb(a) ** 2.2).mean(-1)                    # linear reflectance at normal incidence
            spec = np.clip(f0 / 0.08, 0.0, 1.0)               # 4 % (plastic, wood, paint) -> 0.5 like an SRM
            metal = np.clip((f0 - 0.04) / 0.5, 0.0, 1.0)      # metals reflect 50-100 %
            gloss = a[..., 3].astype(np.float32) / 255.0
            if float(gloss.std()) < 0.01 and float(gloss.mean()) > 0.99:   # no gloss channel: material constant
                g = param(mat, "ShaderLOD_Gloss")
                gloss = np.full_like(f0, g if g is not None else 1.0 - param(mat, "ShaderLOD_Roughness", 0.5))
            return Surface(spec, 1.0 - gloss, metal, None, spec_ref)
    # metal/roughness workflow (Unreal): packed occlusion-roughness-metal (channel order from the slot, ORM by
    # default) or separate roughness / metallic maps; dielectric F0 4 % = specular level 0.5
    orm_ref, rough_ref, metal_ref, ao_ref = first(mat, "orm"), first(mat, "rough"), first(mat, "metal"), first(mat, "ao")
    out = None
    if orm_ref is not None:
        ot = cache.get(orm_ref.key)
        if usable(ot):
            f = rgb(tx.decode_top(ot, max_size))
            order = (orm_ref.slot or "ORM").upper()
            order = order if len(order) == 3 and "R" in order else "ORM"
            rough = f[..., order.find("R")]
            metal = f[..., order.find("M")] if "M" in order else np.zeros_like(rough)
            ao = f[..., order.find("O")] if "O" in order else None
            out = Surface(np.full_like(rough, 0.5), rough, metal, ao, orm_ref)
    elif rough_ref is not None:
        rt = cache.get(rough_ref.key)
        if usable(rt):
            rough = rgb(tx.decode_top(rt, max_size))[..., 0]
            metal = np.zeros_like(rough)
            mt = cache.get(metal_ref.key) if metal_ref is not None else None
            if usable(mt):
                metal = _fit(rgb(tx.decode_top(mt, max_size))[..., 0], rough.shape)
            out = Surface(np.full_like(rough, 0.5), rough, metal, None, rough_ref)
    if out is not None:
        if out.ao is None and ao_ref is not None and usable(cache.get(ao_ref.key)):
            out.ao = _fit(rgb(tx.decode_top(cache.get(ao_ref.key), max_size))[..., 0], out.rough.shape)
        return out
    lk = lk or Look(hair=is_hair(mat), eye=is_eye(mat))
    const = _constant_spec(mat, lk.hair, lk.eye)
    if const is not None:
        spec, rough, metal = (np.full((4, 4), v, np.float32) for v in const)
        return Surface(spec, rough, metal)
    return None


# ---- emissive ----------------------------------------------------------------------------------------------
def emissive(mat: Material, cache: TextureCache, max_size: int) -> np.ndarray | None:
    """RGBA of the emissive map when it is not black (the class placeholder of a material that does not glow)."""
    ref = first(mat, "emissive")
    et = cache.get(ref.key) if ref is not None else None
    if not usable(et):
        return None
    a = tx.decode_top(et, max_size)
    return a if float(a[..., :3].max(-1).mean()) > 6.0 else None
