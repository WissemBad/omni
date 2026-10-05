"""Minimal reader for compiled Source models (.mdl v44-49): skeleton, hitboxes, include-models, attachments.

Used to take the reference skeleton / hitboxes of an existing GMod player model as the template
for converted characters, so that GMod's stock animations (m_anm.mdl ...) drive them.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np


@dataclass
class MdlBone:
    name: str
    parent: int
    pos: np.ndarray            # local, Source units
    quat: np.ndarray           # local (x, y, z, w)
    pose_to_bone: np.ndarray   # 3x4 world->bone (inverse bind), Source units
    flags: int
    physics_bone: int


@dataclass
class MdlHitbox:
    bone: int
    group: int
    bbmin: np.ndarray
    bbmax: np.ndarray


@dataclass
class MdlAttachment:
    name: str
    bone: int
    local: np.ndarray          # 3x4


@dataclass
class MdlInfo:
    name: str
    bones: list[MdlBone] = field(default_factory=list)
    hitboxes: list[MdlHitbox] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    attachments: list[MdlAttachment] = field(default_factory=list)
    surfaceprop: str = ""
    eye_position: tuple = (0, 0, 0)
    illum_position: tuple = (0, 0, 0)
    hull_min: tuple = (0, 0, 0)
    hull_max: tuple = (0, 0, 0)
    view_bbmin: tuple = (0, 0, 0)
    view_bbmax: tuple = (0, 0, 0)
    ikchains: list = field(default_factory=list)      # [(name, [bone...], knee_dir)]
    iklocks: list = field(default_factory=list)       # [(chain index, pos weight, local q weight)]


def _cstr(d: bytes, off: int) -> str:
    end = d.index(b"\0", off)
    return d[off:end].decode("latin-1")


def read_mdl(d: bytes) -> MdlInfo:
    ident, version = struct.unpack_from("<4si", d, 0)
    if ident != b"IDST":
        raise ValueError("not a Source model")
    info = MdlInfo(name=_cstr(d, 12))
    f3 = lambda o: struct.unpack_from("<3f", d, o)
    info.eye_position, info.illum_position = f3(80), f3(92)
    info.hull_min, info.hull_max = f3(104), f3(116)
    info.view_bbmin, info.view_bbmax = f3(128), f3(140)
    numbones, boneindex = struct.unpack_from("<2i", d, 156)
    nhb, hbidx = struct.unpack_from("<2i", d, 172)
    natt, attidx = struct.unpack_from("<2i", d, 240)
    nik, ikidx = struct.unpack_from("<2i", d, 284)
    (spidx,) = struct.unpack_from("<i", d, 308)
    nlock, lockidx = struct.unpack_from("<2i", d, 320)
    ninc, incidx = struct.unpack_from("<2i", d, 336)

    for i in range(numbones):
        o = boneindex + i * 216
        nameidx, parent = struct.unpack_from("<2i", d, o)
        pos = np.array(struct.unpack_from("<3f", d, o + 32), np.float32)
        quat = np.array(struct.unpack_from("<4f", d, o + 44), np.float32)
        p2b = np.array(struct.unpack_from("<12f", d, o + 96), np.float32).reshape(3, 4)
        flags, _pt, _pi, phys = struct.unpack_from("<4i", d, o + 160)
        info.bones.append(MdlBone(_cstr(d, o + nameidx), parent, pos, quat, p2b, flags, phys))

    if nhb:
        # only the first hitbox set (GMod player models have exactly one)
        so = hbidx
        _name, nbox, boxidx = struct.unpack_from("<3i", d, so)
        for j in range(nbox):
            o = so + boxidx + j * 68
            bone, group = struct.unpack_from("<2i", d, o)
            info.hitboxes.append(MdlHitbox(bone, group,
                                           np.array(struct.unpack_from("<3f", d, o + 8), np.float32),
                                           np.array(struct.unpack_from("<3f", d, o + 20), np.float32)))
    for i in range(natt):
        o = attidx + i * 92
        nameidx, _flags, bone = struct.unpack_from("<3i", d, o)
        local = np.array(struct.unpack_from("<12f", d, o + 12), np.float32).reshape(3, 4)
        info.attachments.append(MdlAttachment(_cstr(d, o + nameidx), bone, local))
    for i in range(nik):
        o = ikidx + i * 16
        nameidx, _ltype, nlinks, linkidx = struct.unpack_from("<4i", d, o)
        links = [struct.unpack_from("<i3f", d, o + linkidx + k * 28) for k in range(nlinks)]
        info.ikchains.append((_cstr(d, o + nameidx), [l[0] for l in links], tuple(links[0][1:4])))
    for i in range(nlock):
        chain, pw, qw = struct.unpack_from("<i2f", d, lockidx + i * 32)
        info.iklocks.append((chain, pw, qw))
    for i in range(ninc):
        o = incidx + i * 8
        _lab, nameidx = struct.unpack_from("<2i", d, o)
        info.includes.append(_cstr(d, o + nameidx))
    if spidx:
        info.surfaceprop = _cstr(d, spidx)
    return info


def quat_to_mat(q: np.ndarray) -> np.ndarray:
    x, y, z, w = [float(v) for v in q]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def world_transforms(bones: list[MdlBone]) -> list[np.ndarray]:
    """4x4 bone-to-world matrices of the bind pose."""
    out: list[np.ndarray] = []
    for b in bones:
        m = np.eye(4)
        m[:3, :3] = quat_to_mat(b.quat)
        m[:3, 3] = b.pos
        out.append(out[b.parent] @ m if b.parent >= 0 else m)
    return out
