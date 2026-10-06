"""Unreal Engine 5 games as an omni source (any game: identified from its folder by ``omni.games``).

Keys are the 64-bit package ids (16 hex digits) the IoStore containers index packages by, so the catalog, the URLs
and the output names work exactly as for Glacier. Everything is read in place from the game's containers by the
Rust core (``N.UnrealGame``); this module only maps Unreal's assets onto omni's neutral IR:

  StaticMesh / SkeletalMesh -> Model (one SubMesh per LOD section), skeletal meshes carry a rig (UERig) so they
                               convert as statues among the props and as playermodels among the characters
  Material / MaterialInstanceConstant -> Material: texture parameters (instance chain, then the base material's
                               own textures), roles from parameter and texture names, blend mode and two-sidedness
  Texture2D                 -> TextureData (BC1/3/4/5/7 kept, anything else converted to RGBA8 by the core)
  SoundWave                 -> SoundRef read through the core (Bink Audio decoded natively)

Frames: Unreal is left-handed, centimetres, Z up; omni's IR is right-handed metres Z up. Props mirror Y;
characters are additionally turned so that they face +Y (what the playermodel builder expects).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.config import CONFIG, Config
from ...core.ir import Material, Model, SoundRef, SubMesh, TextureData, TextureRef
from ...core.naming import slug
from ..base import Source
from . import game as ue

log = logging.getLogger("omni.unreal")

MESH_CLASSES = ("StaticMesh", "SkeletalMesh")
MATERIAL_CLASSES = ("MaterialInstanceConstant", "Material")

# texture role from a parameter or texture name (lower case, separators removed); first match wins
_ROLE_WORDS = [
    ("normal", r"normal|nrm|nmap|bump"),
    ("orm", r"occ.*rough|rough.*metal|metal.*rough|^orm|orm$|^arm$|arm$|aorm|^rma|rma$|^mra|mra$|packed|maskrgb"),
    ("emissive", r"emiss|emit|glow|illum"),
    ("rough", r"rough|gloss"),
    ("metal", r"metal"),
    ("ao", r"ambientocclusion|occlusion|^ao$|cavity"),
    ("alpha", r"opacity|alpha|transparen"),
    ("height", r"height|displace|parallax"),
    ("spec", r"specular|spec$"),
    ("mask", r"mask|tint|colorid|id$"),
    ("base", r"basecolor|base|albedo|diffuse|color|colour|tex|texture|main|skin|cloth"),
]
_SUFFIX_ROLE = {"d": "base", "bc": "base", "b": "base", "c": "base", "col": "base", "diff": "base", "a": "base",
                "albedo": "base", "basecolor": "base", "diffuse": "base", "color": "base",
                "n": "normal", "nm": "normal", "nrm": "normal", "normal": "normal", "norm": "normal",
                "orm": "orm", "arm": "orm", "rma": "orm", "mra": "orm", "mro": "orm", "ao_r_m": "orm",
                "r": "rough", "rough": "rough", "roughness": "rough", "m": "metal", "metal": "metal",
                "metallic": "metal", "e": "emissive", "emissive": "emissive", "em": "emissive",
                "ao": "ao", "o": "ao", "occlusion": "ao", "mask": "mask", "msk": "mask", "op": "alpha",
                "opacity": "alpha", "alpha": "alpha", "h": "height", "height": "height", "s": "spec",
                "spec": "spec", "specular": "spec"}


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def role_of(param: str, texture_path: str, fmt: str = "") -> str:
    """Role of a texture from its suffix (``T_Rock_N``), else the parameter name, else the last word of the
    texture name. ``other`` when nothing tells (the caller may still promote it to the base colour)."""
    leaf = texture_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    m = re.search(r"_([A-Za-z]+)$", leaf)
    if m and m.group(1).lower() in _SUFFIX_ROLE:
        r = _SUFFIX_ROLE[m.group(1).lower()]
        return "normal" if r == "base" and fmt == "BC5" else r
    last = re.split(r"[_\s-]", leaf)[-1] if leaf else ""
    for text in (param, last):
        f = _flat(text)
        if not f:
            continue
        for role, pat in _ROLE_WORDS:
            if re.search(pat, f):
                return "normal" if role == "base" and fmt == "BC5" else role
    return "normal" if fmt == "BC5" else "other"


# ------------------------------------------------------------------------------------------------ frames
MIRROR = np.diag([1.0, -1.0, 1.0])                 # Unreal (left-handed) -> right-handed


def _facing_turn(names: list[str], positions: np.ndarray) -> np.ndarray:
    """Rotation about Z turning a (mirrored) character to face +Y, from where its left and right limbs are."""
    from ...targets.source.humanoid import canonical_names
    canon = canonical_names(names, [-1] * len(names))
    left = [positions[i] for i, c in enumerate(canon) if c.startswith("L_")]
    right = [positions[i] for i, c in enumerate(canon) if c.startswith("R_")]
    if not left or not right:
        return np.eye(3)
    lv = np.mean(left, 0) - np.mean(right, 0)
    lv[2] = 0.0
    if np.linalg.norm(lv) < 1e-6:
        return np.eye(3)
    f = np.cross(lv, [0.0, 0.0, 1.0])               # facing = left x up
    ang = np.arctan2(f[1], f[0])                    # rotate so that f points along +Y (90 degrees)
    a = np.pi / 2 - ang
    # snap to quarter turns: rigs are authored facing an axis
    a = round(a / (np.pi / 2)) * (np.pi / 2)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _qmat(q) -> np.ndarray:
    x, y, z, w = [float(v) for v in q]
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([[1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
                     [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
                     [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)]])


@dataclass
class UERig:
    """A skeletal mesh's reference skeleton in omni's frame: ``names`` are canonical humanoid names where a bone
    plays that role (see targets/source/humanoid.py), ``world()`` the bind pose (metres, Z up, facing +Y)."""
    names: list[str]
    parents: list[int]
    world_m: np.ndarray
    source_names: list[str] = field(default_factory=list)

    def world(self) -> np.ndarray:
        return self.world_m.copy()


def _rig_from_bones(bones: list, A: np.ndarray) -> tuple[UERig, np.ndarray]:
    """(rig, raw world positions in Unreal units) from [(name, parent, quat, translation, scale)]."""
    n = len(bones)
    W = np.zeros((n, 4, 4))
    for i, (_nm, parent, q, t, s) in enumerate(bones):
        m = np.eye(4)
        m[:3, :3] = _qmat(q) * np.asarray(s, float)
        m[:3, 3] = t
        W[i] = W[parent] @ m if 0 <= parent < i else m
    raw_pos = W[:, :3, 3].copy()
    Ainv = np.linalg.inv(A)
    out = np.zeros_like(W)
    for i in range(n):
        R = W[i, :3, :3]
        # orthonormalise (scaled bones) before conjugating into the mirrored frame
        u, _s, vt = np.linalg.svd(R)
        R = u @ vt
        out[i, :3, :3] = A @ R @ Ainv
        out[i, :3, 3] = A @ W[i, :3, 3] * 0.01
        out[i, 3, 3] = 1.0
    names = [b[0] for b in bones]
    parents = [int(b[1]) for b in bones]
    from ...targets.source.humanoid import canonical_names
    canon = canonical_names(names, parents, out[:, :3, 3])
    return UERig(canon, parents, out, names), raw_pos


# ------------------------------------------------------------------------------------------------ sounds
@dataclass
class UnrealSoundRef(SoundRef):
    """A SoundWave of an Unreal game: ``file`` is its package path, read through the core in any process."""
    game: dict = field(default_factory=dict)

    def read(self) -> bytes:
        return ue.open_game(self.game).sound(self.file)[1]


# ------------------------------------------------------------------------------------------------ source
class UnrealSource(Source):
    capabilities = ("props", "characters", "textures", "sounds")
    description = "Unreal Engine 5"

    def __init__(self, info: dict, config: Config = CONFIG):
        self.info = dict(info)
        self.id = info["id"]
        self.title = info.get("title") or info["id"]
        self.description = f"Unreal Engine {info.get('version') or '5'}"
        self.config = config
        self._lock = threading.RLock()
        self._index: dict[str, list] = {}
        self._kind: dict[int, str] = {}
        self._mat_memo: dict = {}
        self._rels: dict[int, str] | None = None
        self._characters = None

    # ---- plumbing -------------------------------------------------------------------------------------
    @property
    def g(self):
        return ue.open_game(self.info)

    def close(self) -> None:
        pass

    def key_of(self, path: str) -> str | None:
        pid = self.g.package_id(path)
        return None if pid is None else "%016X" % pid

    def path_of(self, h: int | str) -> str:
        pid = int(h, 16) if isinstance(h, str) else int(h)
        p = self.g.package_path(pid)
        if p is None:
            raise FileNotFoundError(f"package {pid:016X} introuvable")
        return p

    def _signature(self) -> list:
        sig = []
        for p in sorted(Path(self.info["paks"]).glob("*.utoc")):
            st = p.stat()
            sig.append([p.name, st.st_size, st.st_mtime_ns])
        return sig + [2]

    def index(self, cls: str) -> list:
        """[(path, export name, class, size)] of a class, cached on disk until the containers change."""
        with self._lock:
            if cls in self._index:
                return self._index[cls]
            cache = ue.game_dir(self.id) / f"index_{cls}.json"
            sig = self._signature()
            rows = None
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data["sig"] == sig:
                    rows = [tuple(r) for r in data["rows"]]
            except (OSError, ValueError, KeyError):
                pass
            if rows is None:
                rows = [tuple(r) for r in self.g.index([cls])]
                try:
                    cache.write_text(json.dumps({"sig": sig, "rows": rows}), encoding="utf-8")
                except OSError:
                    pass
            self._index[cls] = rows
            return rows

    def _kind_of(self, h: int) -> str:
        if not self._kind:
            for cls in MESH_CLASSES:
                for path, *_r in self.index(cls):
                    pid = self.g.package_id(path)
                    if pid is not None:
                        self._kind[pid] = cls
        return self._kind.get(h, "")

    # ---- names ------------------------------------------------------------------------------------------
    @staticmethod
    def _rel(path: str) -> str:
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].lower() == "game":
            parts = parts[1:]
        else:
            parts = ["engine"] + parts
        dirs = [slug(p, 32) for p in parts[:-1]][-3:]
        return "/".join(dirs + [slug(parts[-1], 48)]) if parts else "unnamed"

    def rel_path(self, h: int) -> str:
        if self._rels is None:
            from collections import Counter
            rows = [(self.g.package_id(p), p) for cls in MESH_CLASSES for p, *_r in self.index(cls)]
            counts = Counter(self._rel(p) for _pid, p in rows)
            self._rels = {pid: (self._rel(p) if counts[self._rel(p)] == 1 else f"{self._rel(p)}_{pid & 0xFFFFFF:06x}")
                          for pid, p in rows if pid is not None}
        return self._rels.get(h) or self._rel(self.path_of(h))

    @staticmethod
    def _category(path: str) -> str:
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].lower() == "game":
            return "/".join(parts[1:3]) if len(parts) > 3 else (parts[1] if len(parts) > 2 else "game")
        return "~moteur/" + (parts[0] if parts else "?")

    def material_name(self, path: str, key: str) -> str:
        return f"{slug(path.rsplit('/', 1)[-1], 36)}_{key[-6:].lower()}"

    # ---- props ------------------------------------------------------------------------------------------
    def catalog_rows(self):
        for cls in MESH_CLASSES:
            for path, _name, _c, size in self.index(cls):
                pid = self.g.package_id(path)
                if pid is None:
                    continue
                yield {"key": "%016X" % pid, "name": path, "rel": self.rel_path(pid), "size": int(size),
                       "cat": self._category(path), "skinned": cls == "SkeletalMesh", "linked": False}

    def _section_mesh(self, lod: dict, sec: dict, A: np.ndarray, skel: bool, rig_n: int) -> SubMesh | None:
        idx = lod["indices"]
        f, n = int(sec["first_index"]), int(sec["num_triangles"]) * 3
        tri = np.asarray(idx[f:f + n], np.int64)
        if len(tri) < 3:
            return None
        used, inv = np.unique(tri, return_inverse=True)
        pos = np.asarray(lod["positions"], np.float32).reshape(-1, 3)
        if len(pos) == 0 or used[-1] >= len(pos):
            return None
        p = (pos[used].astype(np.float64) @ A.T) * 0.01
        nrm_all = np.asarray(lod["normals"], np.float32).reshape(-1, 3)
        nrm = nrm_all[used].astype(np.float64) @ A.T if len(nrm_all) == len(pos) else np.zeros_like(p)
        tan_all = np.asarray(lod["tangents"], np.float32).reshape(-1, 4)
        tan = None
        if len(tan_all) == len(pos):
            t = tan_all[used].astype(np.float64)
            tan = np.concatenate([t[:, :3] @ A.T, -t[:, 3:4]], 1).astype(np.float32)
        uvs_all = lod["uvs"]
        uv = (np.asarray(uvs_all[0], np.float32).reshape(-1, 2)[used] if uvs_all and len(uvs_all[0]) >= 2 * len(pos)
              else np.zeros((len(used), 2), np.float32))
        col_all = np.asarray(lod["colors"], np.uint8)
        col = col_all.reshape(-1, 4)[used] if len(col_all) == 4 * len(pos) else None
        joints = weights = None
        if skel and lod["influences"]:
            k = int(lod["influences"])
            bi = np.asarray(lod["bone_indices"], np.int64).reshape(-1, k)[used]
            bw = np.asarray(lod["bone_weights"], np.float32).reshape(-1, k)[used]
            bmap = np.asarray(sec["bone_map"] or [0], np.int64)
            bones = bmap[np.clip(bi, 0, len(bmap) - 1)]
            order = np.argsort(-bw, axis=1)[:, :4]
            bones = np.take_along_axis(bones, order, 1)
            bw = np.take_along_axis(bw, order, 1)
            if bw.shape[1] < 4:
                pad = 4 - bw.shape[1]
                bones = np.pad(bones, ((0, 0), (0, pad)))
                bw = np.pad(bw, ((0, 0), (0, pad)))
            tot = bw.sum(1, keepdims=True)
            bw = np.where(tot > 0, bw / np.maximum(tot, 1e-6), 0.0)
            joints = np.clip(bones, 0, max(rig_n - 1, 0)).astype(np.uint16)
            weights = bw.astype(np.float32)
        return SubMesh(positions=p.astype(np.float32), normals=nrm.astype(np.float32), uvs=uv,
                       indices=inv.astype(np.uint32).reshape(-1), colors=col, tangents=tan, joints=joints,
                       weights=weights)

    def load_model(self, h: int, lod: int = 0) -> tuple[Model, dict[str, Material]]:
        path = self.path_of(h)
        kind = self._kind_of(h) or "StaticMesh"
        skel = kind == "SkeletalMesh"
        data = (self.g.skeletal_mesh if skel else self.g.static_mesh)(path, max(1, lod + 1))
        lods = [i for i, l in enumerate(data["lods"]) if len(l["positions"])]
        if not lods:
            raise ValueError("no geometry in any level of detail")
        level = max([i for i in lods if i <= lod] or [lods[0]])
        L = data["lods"][level]
        if skel:
            slots = data.get("materials") or []
        else:
            slots = [m.get("MaterialInterface") if isinstance(m, dict) else None
                     for m in data["props"].get("StaticMaterials", [])]
        rig, A, warnings = None, MIRROR, []
        if skel and data["bones"]:
            names = [b[0] for b in data["bones"]]
            _rig0, raw = _rig_from_bones(data["bones"], MIRROR)
            A = _facing_turn(names, (raw @ MIRROR.T) * 0.01) @ MIRROR
            rig, _raw = _rig_from_bones(data["bones"], A)
        materials: dict[str, Material] = {}
        subs = []
        for si, sec in enumerate(L["sections"]):
            sm = self._section_mesh(L, sec, A, skel, len(rig.names) if rig else 0)
            if sm is None:
                continue
            mi = int(sec["material"])
            mpath = slots[mi] if 0 <= mi < len(slots) else None
            mk = self.key_of(mpath.split(".")[0]) if mpath else None
            if mk:
                if mk not in materials:
                    materials[mk] = self.load_material(int(mk, 16))
                sm.material_key = mk
            else:
                warnings.append(f"section {si}: material slot {mi} has no material")
            subs.append(sm)
        model = Model(key="%016X" % h, name=self.rel_path(h), submeshes=subs, skinned=skel, warnings=warnings,
                      lod=level, lods=lods)
        model.rig = rig
        return model, materials

    # ---- materials -------------------------------------------------------------------------------------
    def _texture_paths(self) -> set[str]:
        if getattr(self, "_texset", None) is None:
            self._texset = {p.lower() for p, *_r in self.index("Texture2D")}
        return self._texset

    def material_textures(self, path: str) -> tuple[list[tuple[str, str]], dict, dict, str]:
        """([(slot, texture path)], scalars, vectors, base class info) through the instance chain."""
        textures: list[tuple[str, str]] = []
        seen_slots: set[str] = set()
        scalars: dict[str, list[float]] = {}
        flags: dict = {}
        cur, depth = path, 0
        texset = self._texture_paths()
        while cur and depth < 10:
            depth += 1
            try:
                cls, props = self.g.properties(cur)
            except (ValueError, OSError):
                break
            if cls == "MaterialInstanceConstant" or "TextureParameterValues" in props or "Parent" in props:
                for tp in props.get("TextureParameterValues") or []:
                    name = ((tp.get("ParameterInfo") or {}).get("Name")) or "texture"
                    val = tp.get("ParameterValue")
                    if val and name not in seen_slots:
                        seen_slots.add(name)
                        textures.append((name, val.split(".")[0]))
                for sp in props.get("ScalarParameterValues") or []:
                    name = (sp.get("ParameterInfo") or {}).get("Name")
                    if name and name not in scalars and sp.get("ParameterValue") is not None:
                        scalars[name] = [float(sp["ParameterValue"])]
                for vp in props.get("VectorParameterValues") or []:
                    name = (vp.get("ParameterInfo") or {}).get("Name")
                    v = vp.get("ParameterValue")
                    if name and name not in scalars and isinstance(v, list):
                        scalars[name] = [float(x) for x in v]
                ov = props.get("BasePropertyOverrides") or {}
                if ov.get("bOverride_BlendMode") and "blend" not in flags:
                    flags["blend"] = ov.get("BlendMode", "")
                if ov.get("bOverride_TwoSided") and "two_sided" not in flags:
                    flags["two_sided"] = bool(ov.get("TwoSided"))
                parent = props.get("Parent")
                cur = parent.split(".")[0] if parent else None
                continue
            # base material: its blend mode and the textures its package imports (sampler defaults)
            flags.setdefault("blend", props.get("BlendMode", "BLEND_Opaque"))
            flags.setdefault("two_sided", bool(props.get("TwoSided")))
            flags["domain"] = props.get("MaterialDomain", "")
            try:
                for imp in self.g.imports(cur):
                    if imp.lower() in texset:
                        leaf = imp.rsplit("/", 1)[-1]
                        if leaf not in seen_slots:
                            seen_slots.add(leaf)
                            textures.append(("default:" + leaf, imp))
            except (ValueError, OSError):
                pass
            break
        return textures, scalars, flags, cur or ""

    def load_material(self, h: int) -> Material:
        hit = self._mat_memo.get(h)
        if hit is None:
            hit = self._load_material(h)
            if len(self._mat_memo) > 20000:
                self._mat_memo.clear()
            self._mat_memo[h] = hit
        return copy.deepcopy(hit)

    def _load_material(self, h: int) -> Material:
        key = "%016X" % h
        try:
            path = self.path_of(h)
        except FileNotFoundError:
            m = Material(key=key, name=f"mat_{key[-6:].lower()}")
            m.flags.add("missing")
            return m
        mat = Material(key=key, name=self.material_name(path, key), source_name=path)
        textures, params, flags, base = self.material_textures(path)
        fmts = {}
        infos = self.g.texture_infos([p for _s, p in textures]) if textures else []
        for (slot, tpath), info in zip(textures, infos):
            fmts[tpath] = (info[0] if info else "")
        taken: set[str] = set()
        for slot, tpath in textures:
            tk = self.key_of(tpath)
            if tk is None:
                continue
            pf = fmts.get(tpath, "")
            role = role_of("" if slot.startswith("default:") else slot, tpath, {"PF_BC5": "BC5"}.get(pf, ""))
            default = slot.startswith("default:")
            if default and role in taken:
                continue                    # the instance already sets this role
            if role in taken and role in ("base", "normal", "orm"):
                role = "detail_normal" if role == "normal" else "other"
            if role == "other":
                mat.unknown_slots.append(slot)
            taken.add(role)
            orm_slot = slot
            if role == "orm":
                m = re.search(r"_([A-Za-z]{3})$", tpath)
                orm_slot = m.group(1).upper() if m and m.group(1).lower() in ("orm", "arm", "rma", "mra", "mro") else "ORM"
            mat.textures.append(TextureRef(role, orm_slot if role == "orm" else slot, tk))
        if not any(t.role == "base" for t in mat.textures):
            # a texture nothing names (painting, trim sheet...) is the colour when the material has none
            for t in mat.textures:
                if t.role == "other":
                    t.role = "base"
                    if t.slot in mat.unknown_slots:
                        mat.unknown_slots.remove(t.slot)
                    break
        mat.params = params
        blend = str(flags.get("blend", ""))
        if blend.endswith("Masked"):
            mat.flags.add("alpha_test")
        elif any(blend.endswith(x) for x in ("Translucent", "Additive", "Modulate", "AlphaComposite")):
            mat.flags.add("translucent")
        if flags.get("two_sided"):
            mat.flags.add("two_sided")
        if str(flags.get("domain", "")).endswith("DeferredDecal"):
            mat.flags.add("decal")
        if base:
            mat.flags.add("class:" + slug(base.rsplit("/", 1)[-1], 40))
        return mat

    def load_material_variant(self, h: int, params: dict | None = None) -> Material:
        return self.load_material(h)

    # ---- textures ---------------------------------------------------------------------------------------
    def load_texture(self, h: int) -> TextureData | None:
        try:
            t = self.g.texture(self.path_of(h))
        except (ValueError, FileNotFoundError):
            return None
        if not t["mips"]:
            return None
        return TextureData("%016X" % h, t["format"], t["mips"][0][0], t["mips"][0][1], [tuple(m) for m in t["mips"]])

    def texture_png(self, key: str, channel: str = "rgb", max_dim: int = 1024, normal: bool = False) -> bytes:
        from ...native import N
        t = self.g.texture(self.path_of(key), max_dim)
        if not t["mips"]:
            raise FileNotFoundError(f"texture {key}: no pixel data")
        w, hh, data = t["mips"][0]
        rgba = N.to_rgba(t["format"], w, hh, data)
        if normal and t["format"] in ("BC5", "BC4"):
            from ...targets.source import textures as tx
            rgba = tx.rebuild_normal(rgba)
        return N.png_rgba(np.ascontiguousarray(rgba), channel, max_dim)

    def texture_index(self, progress=print) -> dict:
        from concurrent.futures import ThreadPoolExecutor
        tex_rows = self.index("Texture2D")
        paths = [p for p, *_r in tex_rows]
        infos = self.g.texture_infos(paths)
        progress(f"{len(paths)} textures read")
        materials, tex_mat, first_use = [], [], {}
        mat_paths = [p for cls in MATERIAL_CLASSES for p, *_r in self.index(cls)]

        def one(path):
            try:
                return path, self.load_material(int(self.key_of(path), 16))
            except Exception:  # noqa: BLE001
                return path, None
        with ThreadPoolExecutor(8) as ex:
            loaded = list(ex.map(one, mat_paths))
        for path, m in loaded:
            if m is None:
                continue
            materials.append({"key": m.key, "name": m.name, "cls": next((f[6:] for f in m.flags if f.startswith("class:")), ""),
                              "source": path})
            for t in m.textures:
                tex_mat.append((t.key, m.key, t.slot, t.role))
                first_use.setdefault(t.key, t.role)
        progress(f"{len(materials)} materials, {len(tex_mat)} texture slots")

        def mesh_mats(row):
            path, cls = row
            try:
                if cls == "SkeletalMesh":
                    slots = self.g.skeletal_mesh(path, 0).get("materials") or []
                else:
                    _c, props = self.g.properties(path, "StaticMesh")
                    slots = [m.get("MaterialInterface") for m in props.get("StaticMaterials", []) if isinstance(m, dict)]
            except Exception:  # noqa: BLE001
                return []
            out = []
            for s in slots:
                k = self.key_of(s.split(".")[0]) if s else None
                if k:
                    out.append((k, self.key_of(path)))
            return out
        rows = [(p, cls) for cls in MESH_CLASSES for p, *_r in self.index(cls)]
        with ThreadPoolExecutor(8) as ex:
            mat_model = [x for r in ex.map(mesh_mats, rows) for x in r]
        progress(f"{len(mat_model)} mesh/material links")
        out = []
        for path, info in zip(paths, infos):
            key = self.key_of(path)
            if key is None:
                continue
            fmt, w, hh, mips, size = info if info else ("?", 0, 0, 0, 0)
            from ...native import N as _N  # noqa: F401  (keeps the import local)
            omni_fmt = {"PF_DXT1": "BC1", "PF_DXT3": "BC2", "PF_DXT5": "BC3", "PF_BC4": "BC4", "PF_BC5": "BC5",
                        "PF_BC7": "BC7"}.get(fmt, fmt.replace("PF_", ""))
            leaf = path.rsplit("/", 1)[-1]
            folder = "/".join([p for p in path.split("/") if p][:-1][-3:]) or "misc"
            role = first_use.get(key) or role_of("", path, omni_fmt)
            out.append({"key": key, "name": leaf, "folder": folder, "fmt": omni_fmt, "width": w, "height": hh,
                        "mips": mips, "bytes": size, "role": role, "named": True})
        return {"textures": out, "materials": materials, "tex_mat": tex_mat, "mat_model": mat_model}

    # ---- sounds -----------------------------------------------------------------------------------------
    def list_sounds(self, progress=print, fresh: bool = False):
        refs = []
        for path, _name, _c, _size in self.index("SoundWave"):
            parts = [p for p in path.split("/") if p]
            rel = "/".join(slug(p, 60) for p in (parts[1:] if parts and parts[0].lower() == "game" else ["engine"] + parts))
            refs.append(UnrealSoundRef(path=rel or "sound", file=path, source_id=f"UE:{path}", priority=1,
                                       meta={"title": parts[-1] if parts else "", "album": parts[-2] if len(parts) > 1 else "",
                                             "genre": "sound"}, game=self.info))
        progress(f"{len(refs)} sound waves")
        return refs

    # ---- characters -------------------------------------------------------------------------------------
    def characters(self) -> list[dict]:
        if self._characters is not None:
            return list(self._characters.values())
        cache = ue.game_dir(self.id) / "characters.json"
        sig = self._signature()
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data["sig"] == sig and data.get("version") == 1:
                self._characters = {c["id"]: c for c in data["items"]}
                return list(self._characters.values())
        except (OSError, ValueError, KeyError):
            pass
        from ...targets.source.humanoid import canonical_names, is_humanoid
        out = {}
        for path, _name, _c, _size in self.index("SkeletalMesh"):
            try:
                m = self.g.skeletal_mesh(path, 0)
            except Exception:  # noqa: BLE001
                continue
            bones = m["bones"]
            canon = canonical_names([b[0] for b in bones], [b[1] for b in bones])
            if not is_humanoid(canon):
                continue
            key = self.key_of(path)
            parts = [p for p in path.split("/") if p]
            leaf = parts[-1]
            title = re.sub(r"^(skm?|sk|mesh)_", "", leaf, flags=re.I).replace("_", " ").strip() or leaf
            folder = parts[-2] if len(parts) > 1 else ""
            out[key] = {"id": key, "title": title, "kind": "character", "mission": folder, "role": "",
                        "body": "male", "variants": [{"v": 0, "key": key, "name": leaf}], "path": path}
        self._characters = out
        try:
            cache.write_text(json.dumps({"sig": sig, "version": 1, "items": list(out.values())}), encoding="utf-8")
        except OSError:
            pass
        return list(out.values())

    def character(self, cid: str) -> dict | None:
        self.characters()
        return self._characters.get(cid)

    def build_character(self, cid: str, opts, cfg: Config = CONFIG, title: str = ""):
        """Playermodel of one skeletal mesh (generic builder: the mesh's own skeleton on ValveBiped)."""
        from ...targets.source import pm_build
        c = self.character(cid)
        o = copy.copy(opts)
        o.lod = 0
        o.name = slug(c["variants"][0]["name"] if c else cid, 40)
        o.title = title or (c["title"] if c else cid)
        return pm_build.build_playermodel(self, [cid], o, cfg)

    def preview_character(self, cid: str, out_glb: Path, opts, cfg: Config, tex_size: int = 512) -> dict:
        from ...targets.source import pm_build
        c = self.character(cid) or {"title": cid, "variants": [{"v": 0, "key": cid, "name": cid}]}
        o = copy.copy(opts)
        o.lod = 0
        o.name = slug(c["variants"][0]["name"], 40)
        return pm_build.export_character_preview(self, [cid], o, cfg, out_glb, tex_size)
