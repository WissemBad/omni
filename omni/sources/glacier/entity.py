"""Glacier 2 entity templates, 007 First Light: TEMP (factory) + TBLU (blueprint), "BIN1" resources.

Layouts (verified on the game files; offsets inside the BIN1 data section):

  BIN1 file      'BIN1', u8 flags.., u32 BE data size @8, data @16, then segments {u32 id, u32 size, payload}
                 segment 0x3989BF9F = type table: u32 n, n x u32 offsets, u32 count, count x
                 {u32 index, i32 -1, u32 len, name\\0, pad to 4}
  ZString        {u32 length | 0x40000000, u32 pad, u64 ptr}
  TArray<T>      {u64 begin, u64 end, u64 capacity} (-1 when empty)
  SEntityRef     {u64 index, u64 entityID, ZString exposedEntity, i32 externalScene, pad}      40 bytes
  property       {u32 CRC32(name), u32 pad, u64 typeIndex, u64 dataPtr}                         24 bytes

  TEMP           i32 subType, i32 blueprintIndex (meta ref), i32 rootIndex, pad, ZString name,
                 TArray<sub> @0x20 (0x78 each: SEntityRef parent, i32 typeIndex (meta ref), pad,
                 TArray props @0x30, TArray postInitProps @0x48, TArray platformProps @0x60),
                 TArray<override> @0x38 (SEntityRef owner + property, 64 each)
  TBLU           i32 subType, i32 rootIndex, ZString name, TArray<sub> @0x18 (0xB0 each: SEntityRef
                 parent, i32 typeIndex, u64 entityID @0x30, ZString name @0x58,
                 TArray<alias> @0x68 {ZString alias, u64 entityIndex, ZString property} 40 each)

Property names are CRC32 of the name; they are resolved with every name the converter knows (material
parameters, texture slots, a few engine properties). Unresolved ones keep their hex id.
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass, field
from functools import lru_cache

from ...native import N

TYPE_TABLE = 0x3989BF9F
ENGINE_PROPERTIES = (
    "m_mTransform", "m_bVisible", "m_bEnabled", "m_eRoomBehaviour", "m_ResourceID", "m_aMaterials",
    "m_rMaterial", "m_pMaterial", "m_aMaterialOverrides", "m_eidParent", "m_bEnableTransformation",
)


def parse_bin1(d: bytes) -> tuple[bytes, list[str]]:
    if d[:4] != b"BIN1":
        raise ValueError("not a BIN1 resource")
    size = struct.unpack_from(">I", d, 8)[0]
    data = d[16:16 + size]
    types: list[str] = []
    o = 16 + size
    while o + 8 <= len(d):
        sid, ssz = struct.unpack_from("<II", d, o)
        seg = d[o + 8:o + 8 + ssz]
        if sid == TYPE_TABLE:
            n = struct.unpack_from("<I", seg, 0)[0]
            q = 4 + 4 * n
            cnt = struct.unpack_from("<I", seg, q)[0]
            q += 4
            for _ in range(cnt):
                _idx, _m1, ln = struct.unpack_from("<iiI", seg, q)
                q += 12
                types.append(seg[q:q + ln - 1].decode("latin-1"))
                q = (q + ln + 3) & ~3
        o += 8 + ssz
    return data, types


def _zstring(data: bytes, o: int) -> str:
    ln, _pad, ptr = struct.unpack_from("<IIq", data, o)
    ln &= 0x3FFFFFFF
    if ptr < 0 or ln == 0:
        return ""
    return data[ptr:ptr + ln].decode("utf-8", "replace")


def _array(data: bytes, o: int) -> tuple[int, int]:
    b, e = struct.unpack_from("<qq", data, o)
    return (b, e) if 0 <= b <= e else (0, 0)


@dataclass
class EntityRef:
    index: int                  # index of the entity in the same template (-1: none)
    entity_id: int
    exposed: str = ""
    external_scene: int = -1


def _ref(data: bytes, o: int) -> EntityRef:
    idx, eid = struct.unpack_from("<qq", data, o)
    ext = struct.unpack_from("<i", data, o + 32)[0]
    if idx >= 0x80000000:                  # stored as u32 -1
        idx = -1
    return EntityRef(int(idx), eid, _zstring(data, o + 16), ext)


@dataclass
class Prop:
    pid: int
    name: str
    type: str
    value: object


def _value(data: bytes, types: list[str], tidx: int, ptr: int, refs: list[int]):
    t = types[tidx] if 0 <= tidx < len(types) else f"?{tidx}"
    try:
        if t == "SColorRGB" or t == "SVector3":
            return t, struct.unpack_from("<3f", data, ptr)
        if t == "SColorRGBA" or t == "SVector4":
            return t, struct.unpack_from("<4f", data, ptr)
        if t == "SVector2":
            return t, struct.unpack_from("<2f", data, ptr)
        if t == "float32":
            return t, struct.unpack_from("<f", data, ptr)[0]
        if t == "float64":
            return t, struct.unpack_from("<d", data, ptr)[0]
        if t == "bool":
            return t, bool(data[ptr])
        if t in ("int8", "uint8"):
            return t, struct.unpack_from("<b" if t == "int8" else "<B", data, ptr)[0]
        if t in ("int16", "uint16"):
            return t, struct.unpack_from("<h" if t == "int16" else "<H", data, ptr)[0]
        if t in ("int32", "uint32"):
            return t, struct.unpack_from("<i" if t == "int32" else "<I", data, ptr)[0]
        if t in ("int64", "uint64"):
            return t, struct.unpack_from("<q" if t == "int64" else "<Q", data, ptr)[0]
        if t == "ZString":
            return t, _zstring(data, ptr)
        if t == "SMatrix43":
            return t, struct.unpack_from("<12f", data, ptr)
        if t == "ZRuntimeResourceID":
            hi, lo = struct.unpack_from("<II", data, ptr)
            if hi == 0xFFFFFFFF and lo == 0xFFFFFFFF:
                return t, None
            if lo < len(refs):              # serialised as an index into the resource's references
                return t, refs[lo]
            return t, (hi << 32) | lo
        if t == "TArray<SEntityTemplateReference>":
            b, e = _array(data, ptr)
            return t, [_ref(data, x) for x in range(b, e, 40)]
        if t.startswith("TArray<"):
            return t, None
        if t[:1] == "E" and t[1:2].isupper():  # enums are serialised as int32
            return t, struct.unpack_from("<i", data, ptr)[0]
    except struct.error:
        pass
    return t, None


def _props(data, types, o, refs, names) -> list[Prop]:
    out = []
    b, e = _array(data, o)
    for x in range(b, e, 24):
        pid, _pad, tidx, ptr = struct.unpack_from("<IIqq", data, x)
        t, v = _value(data, types, tidx, ptr, refs)
        out.append(Prop(pid, names.get(pid, "0x%08x" % pid), t, v))
    return out


def _py_value(v):
    """Native values -> the objects the Python parser produces (entity refs become EntityRef)."""
    if isinstance(v, list):
        return [EntityRef(*x) if isinstance(x, tuple) else x for x in v]
    return v


@dataclass
class SubEntity:
    parent: EntityRef
    type_ref: int                  # resource hash of the entity type (template, class, material, prim...)
    props: list[Prop] = field(default_factory=list)
    post_props: list[Prop] = field(default_factory=list)
    entity_id: int = 0             # from the blueprint
    name: str = ""
    aliases: list = field(default_factory=list)   # (alias, entity index, property)


@dataclass
class Override:
    owner: EntityRef
    prop: Prop


@dataclass
class Template:
    key: int
    name: str
    root: int
    subs: list[SubEntity]
    overrides: list[Override]
    refs: list[int]

    def by_id(self, entity_id: int) -> int:
        for i, s in enumerate(self.subs):
            if s.entity_id == entity_id:
                return i
        return -1


class EntityReader:
    """Decodes TEMP/TBLU pairs of a Glacier archive (cached)."""

    def __init__(self, source, extra_names=()):
        self.source = source
        self.archive = source.archive
        self._names = None
        self._extra = tuple(extra_names)

    @property
    def names(self) -> dict[int, str]:
        if self._names is None:
            import json
            from ...core.config import CONFIG
            cache = CONFIG.cache / f"property_names_{self.source.id}.json"
            if cache.exists():
                try:
                    known = set(json.loads(cache.read_text(encoding="utf-8"))) | set(ENGINE_PROPERTIES) | set(self._extra)
                    self._names = {zlib.crc32(n.encode()): n for n in known}
                    return self._names
                except (OSError, ValueError):
                    pass
            from .mati import parse_mati
            known = set(ENGINE_PROPERTIES) | set(self._extra)
            for i, (_h, p) in enumerate(self.archive.index("MATI").items()):
                if i > 8000:
                    break
                try:
                    mt = parse_mati(p.read_bytes())
                except Exception:  # noqa: BLE001
                    continue
                known |= {q.name for q in mt.params} | {t.name for t in mt.textures}
            self._names = {zlib.crc32(n.encode()): n for n in known}
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(sorted(known)), encoding="utf-8")
                tmp.replace(cache)
            except OSError:
                pass
        return self._names

    def _refs(self, path) -> list[int]:
        meta = self.archive.meta(path)
        return [r[0] for r in meta.refs] if meta else []

    @lru_cache(maxsize=4096)
    def template(self, key: int) -> Template | None:
        tp = self.archive.find("TEMP", key)
        if tp is None:
            return None
        refs = self._refs(tp)
        try:
            return self._template_py(key, tp, refs)
        except (struct.error, IndexError, ValueError, OverflowError):
            if N is None:
                raise
            # malformed/out-of-range data: the Rust parser reads defensively (and is the only one that
            # copes with the multi-MB level-scene templates)
            return self._template_native(key, tp.read_bytes(), refs)

    def _template_native(self, key: int, raw: bytes, refs: list[int]) -> Template:
        names = self.names

        def props(raw_props):
            return [Prop(pid, names.get(pid, "0x%08x" % pid), t, _py_value(v)) for pid, t, v in raw_props]

        bp_index, subs_raw, ov_raw = N.parse_temp(raw, refs)
        subs = [SubEntity(EntityRef(*parent), tref, props(p), props(post)) for parent, tref, p, post in subs_raw]
        overrides = [Override(EntityRef(*owner), props([pr])[0]) for owner, pr in ov_raw]
        root = next((i for i, s in enumerate(subs) if s.parent.index < 0), len(subs) - 1)
        tpl = Template(key, self.source.names.name(key), root, subs, overrides, refs)
        if 0 <= bp_index < len(refs):
            self._blueprint(tpl, refs[bp_index])
        return tpl

    def _template_py(self, key: int, tp, refs: list[int]) -> Template:
        data, types = parse_bin1(tp.read_bytes())
        names = self.names
        _st, bp_index, _root = struct.unpack_from("<iii", data, 0)
        subs = []
        b, e = _array(data, 0x20)
        for o in range(b, e, 0x78):
            t = struct.unpack_from("<i", data, o + 0x28)[0]
            subs.append(SubEntity(_ref(data, o), refs[t] if 0 <= t < len(refs) else 0,
                                  _props(data, types, o + 0x30, refs, names),
                                  _props(data, types, o + 0x48, refs, names)))
        overrides = []
        b, e = _array(data, 0x38)
        for o in range(b, e, 64):
            owner = _ref(data, o)
            pid, _pad, tidx, ptr = struct.unpack_from("<IIqq", data, o + 40)
            ty, v = _value(data, types, tidx, ptr, refs)
            overrides.append(Override(owner, Prop(pid, names.get(pid, "0x%08x" % pid), ty, v)))
        root = next((i for i, s in enumerate(subs) if s.parent.index < 0), len(subs) - 1)
        tpl = Template(key, self.source.names.name(key), root, subs, overrides, refs)
        if 0 <= bp_index < len(refs):
            self._blueprint(tpl, refs[bp_index])
        return tpl

    def _blueprint(self, tpl: Template, key: int) -> None:
        bp = self.archive.find("TBLU", key)
        if bp is None:
            return
        if N is not None:
            try:
                root, subs = N.parse_tblu(bp.read_bytes())
            except ValueError:
                pass
            else:
                for s, (eid, name, aliases) in zip(tpl.subs, subs):
                    s.entity_id, s.name, s.aliases = eid, name, aliases
                if 0 <= root < len(tpl.subs):
                    tpl.root = root
                return
        data, _types = parse_bin1(bp.read_bytes())
        root = struct.unpack_from("<i", data, 4)[0]
        b, e = _array(data, 0x18)
        for i, o in enumerate(range(b, e, 0xB0)):
            if i >= len(tpl.subs):
                break
            s = tpl.subs[i]
            s.entity_id = struct.unpack_from("<Q", data, o + 0x30)[0]
            s.name = _zstring(data, o + 0x58)
            ab, ae = _array(data, o + 0x68)
            s.aliases = [(_zstring(data, x), struct.unpack_from("<q", data, x + 16)[0], _zstring(data, x + 24))
                         for x in range(ab, ae, 40)]
        if 0 <= root < len(tpl.subs):
            tpl.root = root
