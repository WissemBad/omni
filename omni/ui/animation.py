"""Animations of a compiled model for the viewer: which sequences it can play (its own and those of the models it
includes, such as Garry's Mod's ``m_anm``) and the bone transforms of one sequence, retargeted onto its skeleton.

The Source files are read by the Rust core (``native/src/source_anim.rs``); this module only chooses what to play and
moves the transforms onto the viewed model: bones are matched by name, rotations are applied on top of the model's own
rest pose and positions follow the model's proportions (only the pelvis moves, scaled by the height ratio).
"""
from __future__ import annotations

import base64
from pathlib import Path

import numpy as np

from ..native import N
from ..targets.source import gmodanim

UNITS_PER_METER = 39.37
PREFERRED_IDLES = ("idle_all_01", "idle_all", "idle", "idle1", "idle_01")
DELTA = 0x0004
HIDDEN = 0x0400


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def _conj(q: np.ndarray) -> np.ndarray:
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def _sources(mdl: bytes, ani: bytes | None, addon: Path | None, label: str, depth: int = 0) -> list[dict]:
    """The model's own animation file followed by those of its included models: [{id, label, mdl, ani, info}]."""
    info = N.mdl_info(mdl)
    out = [{"id": "self" if depth == 0 else label, "label": label, "mdl": mdl, "ani": ani, "info": info}]
    if depth < 2:
        for name in info["includes"]:
            found = gmodanim.load(name, addon)
            if found:
                out += _sources(found[0], found[1], addon, name.replace("\\", "/").rsplit("/", 1)[-1], depth + 1)
    return out


def _playable(s: dict) -> bool:
    return s["anim"] >= 0 and s["frames"] >= 1 and not (s["flags"] & (DELTA | HIDDEN)) and not (s["anim_flags"] & DELTA)


def catalog(mdl: bytes, ani: bytes | None = None, addon: Path | None = None, own_label: str = "Modèle") -> dict:
    """What the viewer lists: per source the playable sequences, and the one to start with."""
    sources, default = [], None
    for src in _sources(mdl, ani, addon, own_label):
        info = src["info"]
        usable = not info["needs_ani"] or src["ani"] is not None
        seqs = [{"index": i, "name": s["name"], "activity": s["activity"], "frames": s["frames"], "fps": round(s["fps"], 2),
                 "loop": bool(s["flags"] & 1), "seconds": round(s["frames"] / s["fps"], 2) if s["fps"] and s["blends"] <= 1 else 0}
                for i, s in enumerate(info["sequences"]) if _playable(s)] if usable else []
        sources.append({"id": src["id"], "label": src["label"], "available": usable, "sequences": seqs})
        if default is None:
            for pref in PREFERRED_IDLES:
                hit = next((s for s in seqs if s["name"] == pref), None)
                if hit:
                    default = {"source": src["id"], "index": hit["index"]}
                    break
    return {"sources": sources, "default": default, "bones": len(N.mdl_info(mdl)["bones"])}


def _view(pos: np.ndarray, quat: np.ndarray) -> np.ndarray:
    """Source (Z up, inches) -> viewer (Y up, metres): the rotation (x, y, z) -> (x, z, -y) applied to both."""
    p = np.stack([pos[..., 0], pos[..., 2], -pos[..., 1]], -1) / UNITS_PER_METER
    q = np.stack([quat[..., 0], quat[..., 2], -quat[..., 1], quat[..., 3]], -1)
    return np.concatenate([p, q], -1)


def _best_blend(src: dict, seq: dict):
    """A movement sequence blends several animations (a walk's centre blend is the character standing still): play the one
    that moves the most."""
    anims = [a for a in dict.fromkeys(seq.get("blend_anims") or [seq["anim"]]) if 0 <= a < src["info"]["animations"]] or [seq["anim"]]
    best, motion = None, -1.0
    for a in anims[:64]:
        try:
            fps, flags, raw = N.mdl_sample(src["mdl"], src["ani"], a)
        except ValueError:
            continue
        m = float(np.abs(np.diff(raw[..., 3:], axis=0)).sum()) if len(raw) > 1 else 0.0
        if m > motion:
            best, motion = (fps, flags, raw), m
    if best is None:
        raise ValueError("animation illisible")
    return best


def sample(mdl: bytes, ani: bytes | None, addon: Path | None, source: str, index: int, own_label: str = "Modèle") -> dict:
    """The transforms of every bone of the viewed model for one sequence, one entry per frame (viewer frame)."""
    srcs = _sources(mdl, ani, addon, own_label)
    src = next((s for s in srcs if s["id"] == source), None)
    if src is None:
        raise KeyError(f"source d’animation inconnue : {source}")
    seqs = src["info"]["sequences"]
    if not 0 <= index < len(seqs) or not _playable(seqs[index]):
        raise KeyError("séquence inconnue ou non jouable")
    seq = seqs[index]
    fps, _flags, raw = _best_blend(src, seq)
    raw = np.asarray(raw, np.float64)                                    # (frames, source bones, 7)
    target = srcs[0]["info"]["bones"]
    source_bones = src["info"]["bones"]
    frames = raw.shape[0]
    t_pos = np.array([b["pos"] for b in target], np.float64)
    t_quat = np.array([b["quat"] for b in target], np.float64)
    out = np.empty((frames, len(target), 7))
    out[..., :3] = t_pos
    out[..., 3:] = t_quat

    if src is srcs[0]:                                                  # the model's own sequence: as it is
        out = raw
    else:
        index_of = {b["name"].lower(): i for i, b in enumerate(source_bones)}
        s_pos = np.array([b["pos"] for b in source_bones], np.float64)
        s_quat = np.array([b["quat"] for b in source_bones], np.float64)
        same = (len(target) == len(source_bones) and all(a["name"].lower() == b["name"].lower() for a, b in zip(target, source_bones))
                and np.allclose(t_pos, s_pos, atol=1e-3))
        scale = 1.0
        if not same:
            roots = [i for i, b in enumerate(target) if b["parent"] < 0]
            if roots and (b := index_of.get(target[roots[0]]["name"].lower())) is not None and abs(s_pos[b][2]) > 1e-3:
                scale = t_pos[roots[0]][2] / s_pos[b][2]
        for i, tb in enumerate(target):
            j = index_of.get(tb["name"].lower())
            if j is None:
                continue
            if same:
                out[:, i] = raw[:, j]
                continue
            out[:, i, 3:] = _qmul(t_quat[i], _qmul(_conj(s_quat[j]), raw[:, j, 3:]))
            if tb["parent"] < 0:
                out[:, i, :3] = t_pos[i] + (raw[:, j, :3] - s_pos[j]) * scale
    out[..., 3:] /= np.linalg.norm(out[..., 3:], axis=-1, keepdims=True).clip(1e-9)
    view = _view(out[..., :3], out[..., 3:]).astype("<f4")
    return {"frames": frames, "fps": float(seq["fps"] or fps or 30.0), "loop": bool(seq["flags"] & 1), "bones": len(target),
            "names": [b["name"] for b in target], "name": seq["name"], "data": base64.b64encode(view.tobytes()).decode("ascii")}
