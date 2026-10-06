"""Outfits of 007 First Light: what the game really puts on a character.

An outfit template (``templates/outfits/<mission>/outfits_*.template?/outfit_<name>_<body>_v<N>``) lists body
parts (kit garments, head, hair, arms) as instances of part templates, each with property values. Resolving
it means walking the template tree the way the engine does:

* a value set on a template instance goes to that template's root entity, unless the root declares an alias
  with that name, which forwards it to an inner entity/property (``BaseColor`` -> each material entity);
* a part's root is a ZBodyPartEntity + ZMaterialOverwriteAspect: ``PRIM_PROP`` is its mesh (an outfit can
  swap it), and a property whose id is CRC32(path of a slot's .mi) replaces that slot's material;
* material entities (type = a MATT, i.e. a material instance exposed as an entity) target geometry entities
  (``TARGETS_PROP``) and carry the parameter values applied to their material instance;
* the outfit root lists the visible body parts (``PARTS_PROP``) and the rig (``RIG_PROP``).

Variations of an outfit are sibling templates ``..._v0``, ``..._v1`` ...: same parts with other colours /
materials (-> Source skins) and sometimes other meshes (-> bodygroups).
"""
from __future__ import annotations

import re
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

from .entity import EntityReader, Prop, parse_bin1, _array, _zstring

PRIM_PROP = 0xF7C7EF8D       # body part geometry (ZRuntimeResourceID -> .weightedprim)
TARGETS_PROP = 0xCF7517E8    # material entity -> geometry entities it applies to
PARTS_PROP = 0xDC933FAB      # outfit root -> visible body parts
RIG_PROP = 0x42190F15        # outfit root -> BORG

_VARIANT = re.compile(r"^(.*)_v(\d+)$")
_BODY = re.compile(r"_(male_reg|male_large|male_lc|male_maincast|fem_reg|fem_lc|fem_maincast)(?:_|$)")


def crc(name: str) -> int:
    return zlib.crc32(name.encode())


@dataclass
class Geom:
    prim: int
    props: dict                    # pid -> Prop on the geometry entity (material overwrites, ...)
    label: str                     # name of the outfit part it belongs to
    origin: int = -1               # index of that part in the outfit template
    aspect: int = 0                # entity type of the geometry (ZBodyPartEntity + ZMaterialOverwriteAspect)
    template: str = ""             # path of the part template of the outfit this geometry belongs to (the game's catalogue folder)


@dataclass
class MatEnt:
    mati: int
    props: dict
    targets: list = field(default_factory=list)   # Geom objects
    name: str = ""


@dataclass
class Slot:
    """One material slot of a part, as rendered."""
    source: int                    # MATI referenced by the mesh
    final: int                     # MATI after material overwrites
    params: dict                   # parameter name -> value from material entities


@dataclass
class OutfitPart:
    label: str
    prim: int
    slots: dict                    # source MATI -> Slot
    template: str = ""             # path of its part template: the folder says what it is (see slots.py)


@dataclass
class Outfit:
    key: int
    name: str
    family: str
    variant: int
    body: str
    parts: list
    rig: int = 0


def outfit_name(full: str) -> str:
    m = re.search(r"/([^/?\]]+)\.entitytemplate\]", full)
    return m.group(1) if m else full


def split_variant(name: str) -> tuple[str, int]:
    m = _VARIANT.match(name)
    return (m.group(1), int(m.group(2))) if m else (name, 0)


def body_type(name: str) -> str:
    m = _BODY.search(name)
    return m.group(1) if m else ""


class OutfitResolver:
    def __init__(self, source):
        self.source = source
        self.archive = source.archive
        self.er = EntityReader(source)
        self._mati_of: dict[int, int] = {}
        self._slot_paths: dict[int, dict[int, int]] = {}
        self._kinds: dict[int, str] = {}
        self._aspects: dict[int, list[int]] = {}

    # ---- catalogue -----------------------------------------------------------------
    def outfits(self) -> list[tuple[str, int, int, str]]:
        """[(family, variant, TEMP hash, full name)] of every outfit template."""
        best: dict[str, tuple[int, str]] = {}
        for h in self.archive.index("TEMP"):
            n = self.source.names.name(h)
            low = n.lower()
            if "/templates/outfits/" not in low or ".entitytemplate]" not in low:
                continue
            nm = outfit_name(low)
            # prefer the "].entitytemplate" factory over the "].entitytype" wrapper
            if nm not in best or low.endswith("].entitytemplate"):
                best[nm] = (h, n)
        out = []
        for nm, (h, n) in best.items():
            fam, v = split_variant(nm)
            out.append((fam, v, h, n))
        return sorted(out)

    # ---- helpers --------------------------------------------------------------------
    def mati_of(self, matt: int) -> int:
        """Material instance behind a material entity type (MATT -> its MATI reference)."""
        if matt not in self._mati_of:
            m = 0
            p = self.archive.find("MATT", matt)
            meta = self.archive.meta(p) if p else None
            for rh, _f in (meta.refs if meta else []):
                if self.archive.find("MATI", rh) is not None:
                    m = rh
                    break
            self._mati_of[matt] = m
        return self._mati_of[matt]

    def slot_mati(self, prim: int) -> list[int]:
        """Material instances referenced by a mesh (its material slots)."""
        return [rh for rh in self.source.material_refs(prim) if self.archive.find("MATI", rh) is not None]

    def slot_paths(self, prim: int, aspect: int = 0) -> dict[int, int]:
        """CRC32(slot material path) -> slot MATI, for the material overwrites of a mesh: from the names of
        its material instances, completed by the material-overwrite aspect blueprint (it lists the path of
        every overwritable slot, also for instances whose name is unknown)."""
        key = (prim, aspect)
        if key in self._slot_paths:
            return self._slot_paths[key]
        out = {}
        for rh in self.slot_mati(prim):
            m = re.match(r"^\[(.+?\.mi)\]", self.source.names.name(rh))
            if m:
                out[crc(m.group(1))] = rh
        for path, mati in self._overwrite_slots(aspect):
            out.setdefault(crc(path), mati)
        self._slot_paths[key] = out
        return out

    def _overwrite_slots(self, aspect: int) -> list[tuple[str, int]]:
        """ASET (aspect) -> ECPT (material overwrite aspect) -> ECPB: [{ZString path, u32, u32, u64 MATI}]."""
        out = []
        ap = self.archive.find("ASET", aspect) if aspect else None
        meta = self.archive.meta(ap) if ap else None
        for rh, _f in (meta.refs if meta else []):
            ep = self.archive.find("ECPT", rh)
            emeta = self.archive.meta(ep) if ep else None
            for bh, _g in (emeta.refs if emeta else []):
                bp = self.archive.find("ECPB", bh)
                if bp is None:
                    continue
                try:
                    data, _types = parse_bin1(bp.read_bytes())
                    b, e = _array(data, 0)
                    for o in range(b, e, 32):
                        out.append((_zstring(data, o), struct.unpack_from("<Q", data, o + 24)[0]))
                except Exception:  # noqa: BLE001
                    pass
        return out

    def _kind(self, ref: int) -> str:
        k = self._kinds.get(ref)
        if k is None:
            k = self._kinds[ref] = self._kind_uncached(ref)
        return k

    def _kind_uncached(self, ref: int) -> str:
        if self.archive.find("TEMP", ref) is not None:
            return "TEMP"
        if self.archive.find("MATT", ref) is not None:
            return "MATT"
        if self.archive.find("ASET", ref) is not None:
            return "ASET"
        return ""

    def _aspect_templates(self, ref: int) -> list[int]:
        """An aspect entity type (ASET) combines several entity types into one entity: the template among
        them (e.g. a garment + a name or cloth-collider aspect) holds the geometry."""
        if ref not in self._aspects:
            p = self.archive.find("ASET", ref)
            meta = self.archive.meta(p) if p else None
            self._aspects[ref] = [rh for rh, _f in (meta.refs if meta else []) if self.archive.find("TEMP", rh) is not None]
        return self._aspects[ref]

    # ---- template walk -------------------------------------------------------------
    def _expand(self, tkey: int, incoming: dict, label: str, depth: int = 0, collect: list | None = None):
        t = self.er.template(tkey)
        if t is None or depth > 12:
            return [], []
        props = [{p.pid: p for p in s.props + s.post_props} for s in t.subs]
        aliases = defaultdict(list)
        if 0 <= t.root < len(t.subs):
            for alias, idx, pname in t.subs[t.root].aliases:
                aliases[crc(alias)].append((idx, pname))
        for pid, p in incoming.items():
            if pid in aliases:
                for idx, pname in aliases[pid]:
                    if 0 <= idx < len(props):
                        props[idx][crc(pname)] = Prop(crc(pname), pname, p.type, p.value)
            elif 0 <= t.root < len(props):
                props[t.root][pid] = p
        geoms_of: dict[int, list] = {}
        geoms, mats, local = [], [], []
        for i, s in enumerate(t.subs):
            if collect is not None:
                collect.append(props[i])
            kind = self._kind(s.type_ref)
            if PRIM_PROP in props[i] and kind != "TEMP":
                g = Geom(props[i][PRIM_PROP].value, props[i], label, aspect=s.type_ref)
                geoms_of[i] = [g]
                geoms.append(g)
            elif kind in ("TEMP", "ASET"):
                g, m = [], []
                for tref in ([s.type_ref] if kind == "TEMP" else self._aspect_templates(s.type_ref)):
                    gg, mm = self._expand(tref, props[i], label if depth else (s.name or label), depth + 1, collect)
                    g += gg
                    m += mm
                if depth == 0:
                    tname = self.source.names.name(s.type_ref)
                    for x in g:
                        x.origin = i
                        x.template = tname
                geoms_of[i] = g
                geoms += g
                mats += m
            elif kind == "MATT":
                me = MatEnt(self.mati_of(s.type_ref), props[i], name=s.name)
                local.append(me)
        for me in local:
            tp = me.props.get(TARGETS_PROP)
            refs = tp.value if tp is not None and isinstance(tp.value, list) else []
            me.targets = [g for r in refs for g in geoms_of.get(r.index, [])] or list(geoms)
        return geoms, mats + local

    def resolve(self, key: int) -> Outfit:
        full = self.source.names.name(key)
        name = outfit_name(full.lower())
        fam, var = split_variant(name)
        t = self.er.template(key)
        geoms, mats = self._expand(key, {}, name)
        # visible body parts: the list on the outfit root (indices into the outfit template)
        root_props = {p.pid: p for p in t.subs[t.root].props + t.subs[t.root].post_props} if t else {}
        visible = None
        if PARTS_PROP in root_props and isinstance(root_props[PARTS_PROP].value, list):
            visible = {r.index for r in root_props[PARTS_PROP].value}
        rig = root_props[RIG_PROP].value if RIG_PROP in root_props else 0
        parts = []
        for g in geoms:
            if not isinstance(g.prim, int) or self.archive.find("PRIM", g.prim) is None:
                continue
            if visible is not None and g.origin not in visible:
                continue
            paths = self.slot_paths(g.prim, g.aspect)
            slots = {m: Slot(m, m, {}) for m in self.slot_mati(g.prim)}
            for pid, p in g.props.items():          # material overwrites: CRC32(slot .mi path) -> MATI
                if pid in paths and paths[pid] in slots and isinstance(p.value, int)                         and self.archive.find("MATI", p.value) is not None:
                    slots[paths[pid]].final = p.value
            for s in slots.values():
                for me in mats:
                    if g in me.targets and me.mati == s.final:
                        for p in me.props.values():
                            if p.pid != TARGETS_PROP and p.value is not None and not p.name.startswith(("0x", "m_")):
                                s.params[p.name] = p.value
            parts.append(OutfitPart(g.label, g.prim, slots, g.template))
        return Outfit(key, name, fam, var, body_type(name), parts, rig if isinstance(rig, int) else 0)
