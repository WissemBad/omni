"""Texture-slot -> render role resolution for 007 First Light materials.

Two slot generations exist in the MATI files:

* "named" slots (``mapTex_Basecolor`` / ``mapTex_SRM`` / ``mapTex_Normal`` ...) are explicit.
* "generic" slots (``mapTexture2D_01`` ...) depend on the material class and are better
  identified from the referenced texture's own name (``diffuse_b.tex``, ``specular_a.tex`` ...)
  or its type annotation (``(asnormalmap)``, ``(ascompoundnormal)``), then from the slot default.

Unresolved slots are returned as role ``other`` and listed in the report so this table can grow.
"""
from __future__ import annotations

import re

NAMED = {
    "maptex_basecolor": "base", "maprad_tex_basecolor": "base", "mapred_tex_basecolor": "base",
    "maptex_srm": "srm", "mapred_tex_srm": "srm",
    "maptex_normal": "normal", "mapred_tex_normal": "normal",
    "maptex_emissive": "emissive",
    "maptex_detail": "detail", "mapred_tex_detail": "detail",
    "maptex_micronormal": "detail_normal", "mapmicrobumptexture": "detail_normal",
    "maptex_translucency": "translucency",
    "mapbasecolortexture": "base",
    "maptexture2dheightmap_01": "height",
    # hair / cloth simulation inputs: BC5 like normal maps, but not surface normals (wind noise, strand flow)
    "mapwindheight": "other", "mapwindnormal": "other", "mapdirectiontexture": "other",
}

# Slots the converter deliberately leaves out (they are not unknown: the report must not count them).
IGNORED = frozenset({"mapwindheight", "mapwindnormal", "mapdirectiontexture"})

# Defaults for the generic scheme (slot, class family) -> role. ``*`` matches any class.
GENERIC_DEFAULTS = {
    ("maptexture2d_01", "*"): "base",
    ("maptexture2dnormal_01", "*"): "normal",
    ("maptexture2d_03", "*"): "spec",
    ("maptexture2d_04", "*"): "mask",
    ("maptexture2d_02", "*"): "mask",
    ("maptexture2dcompoundnormal_01", "*"): "detail_normal",
    ("maptexture2dcompoundnormal_02", "*"): "detail_normal",
    ("maptexture2dlinear_01", "*"): "spec",
    # colour-mask materials keep a grey albedo in slot 03 and the tint mask in 04
    ("maptexture2d_03", "colormask"): "base",
    ("maptexture2d_04", "colormask"): "mask",
}

_LEAF_ROLE = [
    (re.compile(r"^(diffuse|albedo|basecolor|color|colour)"), "base"),
    (re.compile(r"^(normal|nrm|nm)"), "normal"),
    (re.compile(r"^(srm)"), "srm"),
    (re.compile(r"^(specular|spec|gloss)"), "spec"),
    (re.compile(r"^(emissive|emission|glow|illum)"), "emissive"),
    (re.compile(r"^(mask|gradient)"), "mask"),
    (re.compile(r"^(detail)"), "detail"),
    (re.compile(r"^(height|disp|bump)"), "height"),
    (re.compile(r"^(opacity|alpha)"), "alpha"),
]


# what a packed map keeps in one channel, from the word the class uses for it
_CHANNEL_WORDS = {
    "specularlevel": "S", "specular": "S", "spec": "S", "roughness": "R", "gloss": "G", "glossiness": "G",
    "metal": "M", "metallic": "M", "metalness": "M", "ao": "O", "occlusion": "O", "ambientocclusion": "O",
    "emissive": "E", "colormask": "C",
}
_CHANNEL_RX = re.compile(r"(?:^map|_)([RGBA])_+([A-Za-z]+)")


def packed_channels(meaning: str) -> str:
    """Channel layout of a packed surface map, from the name its class gives the slot
    (``mapSpecular_R_SpecularLevel_G_Roughness_B_Metallic`` -> ``SRM_``, ``mapR_Specular_G_Gloss_B_Emissive`` ->
    ``SGE_``, ``mapSpecular_R_Metal_G_Roughness_B_AO`` -> ``MRO_``): one letter per RGBA channel among S specular
    level, R roughness, G gloss, M metallic, O occlusion, E emissive, C colour mask, ``_`` anything else.
    Empty when the name describes no specular, roughness/gloss or metal channel."""
    out = ["_"] * 4
    for ch, word in _CHANNEL_RX.findall(meaning):
        out["RGBA".index(ch)] = _CHANNEL_WORDS.get(word.lower(), "_")   # SpecularCavity, RoughnessGrease: modulators
    layout = "".join(out)
    return layout if sum(c in layout for c in "SRGM") >= 2 else ""


def semantic_role(slot: str, meaning: str) -> str:
    """Role of a texture from what its material class calls the slot (``mapSpecular``, ``mapEmissive``,
    ``mapSpecular_R_SpecularLevel_G_Roughness_B_Metallic``...): the class is the authority for the generic slot
    names, whose meaning changes from one class to the next."""
    s = meaning.lower()
    if packed_channels(meaning):
        return "srm"
    if "compoundnormal" in slot.lower() and "detail" in s:
        return "detail_normal"
    if s.startswith("mapdetail"):                     # tiled detail layers: DetailNormal, DetailR_BaseColorG_Dirt...
        return "detail_normal" if "normal" in s else "detail"
    if "normal" in s and "detail" not in s:
        return "normal"
    if "specularocclusion" in s:
        return "other"
    if "diffuseid" in s:
        return "mask"
    if any(k in s for k in ("basecolor", "diffuse", "albedo")):
        return "base"                                 # before the height and alpha a colour map may carry in A
    if "mask" in s or "lut" in s:
        return "mask"
    if "emissive" in s or "emmisve" in s:
        return "emissive"
    if "translucen" in s:
        return "translucency"
    if "height" in s and "wind" not in s:
        return "height"
    if "detail" in s:
        return "detail"
    if re.search(r"(^map|_)ao(_|$)", s) or ("occlusion" in s and "spec" not in s):
        return "ao"
    if "specular" in s:
        return "spec"
    if "alpha" in s or "opacity" in s:
        return "alpha"
    return "other"


def class_family(class_name: str) -> str:
    c = class_name.lower()
    for key in ("colormask", "hardalpha", "glass", "skin", "fabric", "decal", "foliage", "relief", "zmapped", "detail"):
        if key in c:
            return key
    return "basic"


def _leaf(texture_name: str) -> tuple[str, str]:
    """texture IOI name -> (leaf stem, annotation) e.g. ('diffuse_b', 'colormap')."""
    ann = re.search(r"\(as([a-z]+)\)", texture_name)
    m = re.search(r"\?/([^/\]]+?)\.tex", texture_name)
    return (m.group(1).lower() if m else ""), (ann.group(1) if ann else "")


def resolve(slot: str, cls_family: str, texture_name: str = "", fmt: str = "", meaning: str = "") -> str:
    """Explicit slot names first, then what the material class calls the slot (``meaning``, read from the class
    itself), then the texture's own file name (diffuse_a / specular_a / normal_a ...), then the slot position."""
    s = re.sub(r"^map[a-z0-9]+(?:_[a-z0-9]+)*?_tex_", "maptex_", slot.lower())   # mapRED_Tex_SRM, mapGREEN_DIRT_Tex_Basecolor ...
    if s in NAMED:
        return NAMED[s]
    if meaning:
        return semantic_role(slot, meaning)
    leaf, ann = _leaf(texture_name) if texture_name else ("", "")
    if ann == "normalmap":
        return "normal"
    if ann == "compoundnormal":
        return "detail_normal"
    if leaf and not leaf.startswith("default"):
        for rx, role in _LEAF_ROLE:
            if rx.match(leaf):
                return role
    r = GENERIC_DEFAULTS.get((s, cls_family)) or GENERIC_DEFAULTS.get((s, "*"))
    if r:
        return r
    if fmt == "BC5" or ("normal" in s and "micro" not in s):
        return "normal"
    return "other"
