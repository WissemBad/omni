"""Neutral intermediate representation shared by every source and target."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SubMesh:
    """One draw call: geometry plus the key of the material it uses."""
    positions: np.ndarray             # (N,3) float32, metres, source frame
    normals: np.ndarray               # (N,3) float32
    uvs: np.ndarray                   # (N,2) float32, origin top-left (D3D convention)
    indices: np.ndarray               # (M,) uint32, triangle list
    colors: np.ndarray | None = None  # (N,4) uint8
    tangents: np.ndarray | None = None  # (N,4) float32, w = handedness
    joints: np.ndarray | None = None    # (N,4) uint16
    weights: np.ndarray | None = None   # (N,4) float32
    material_key: str = ""
    lod_mask: int = 0xFF
    zbias: int = 0                    # >0: overlay drawn on top of other geometry (decal layer)


@dataclass
class TextureRef:
    role: str                         # base | normal | srm | alpha | emissive | ao | detail_normal | other
    slot: str                         # original slot name in the source material
    key: str                          # source-specific texture id (hash for Glacier)


@dataclass
class Material:
    key: str                          # source-specific id (hash)
    name: str                         # human readable, already game-safe
    source_name: str = ""             # original path/name in the source game (used for hints)
    textures: list[TextureRef] = field(default_factory=list)
    params: dict[str, list[float]] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)   # alpha_test, translucent, two_sided, ...
    unknown_slots: list[str] = field(default_factory=list)


@dataclass
class TextureData:
    """Raw texture as stored by the source game: per-mip block-compressed bytes."""
    key: str
    fmt: str                          # BC1 BC2 BC3 BC4 BC5 BC7 RGBA8 RG8 A8
    width: int
    height: int
    mips: list = field(default_factory=list)    # [(w, h, bytes)] largest first


@dataclass
class Bone:
    name: str
    parent: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]   # x y z w, local


@dataclass
class Model:
    key: str
    name: str
    submeshes: list[SubMesh]
    bones: list[Bone] = field(default_factory=list)
    skinned: bool = False
    warnings: list[str] = field(default_factory=list)
    rig: object = None                # source-specific skeleton (joints of skinned submeshes index into it)
    lod: int = 0                      # level of detail this model holds
    lods: list[int] = field(default_factory=list)   # levels available in the source mesh


@dataclass
class SoundRef:
    """One encoded sound as stored by a game: a byte range of a file (whole file when size < 0).
    ``path`` is the readable output path proposed by the source (folders/name, no extension); ``priority``
    ranks names when the same sound is reached several ways (lower = better: real game path first)."""
    path: str
    file: str
    offset: int = 0
    size: int = -1
    source_id: str = ""               # e.g. "WWES:01A4DD12C09497B9" or "WWEV:...#12"
    priority: int = 5
    meta: dict = field(default_factory=dict)   # tags: title, album (event / conversation / bank), artist
                                               # (speaker), language, genre (dialogue / event / bank)

    def read(self) -> bytes:
        with open(self.file, "rb") as f:
            f.seek(self.offset)
            return f.read() if self.size < 0 else f.read(self.size)
