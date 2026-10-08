"""What the game says about its own data: entity class schemas (CPPT) and enums (ENUM).

* **Class schemas.** A CPPT lists, for one entity class, the CRC32 of each property name and its type. Names are
  not stored, so a schema cannot name a property, but it does say which class has which property and of which type.
  omni reads entity templates with a few property ids it knows by name (outfit parts, rig, mesh, material targets):
  :meth:`SchemaBook.verify` checks those ids against the game's own schema, :meth:`SchemaBook.audit` checks every
  property a template stores (known to the schema, same type) so a misread layout shows up instead of silently
  giving wrong outfits.
* **Enums.** Member names of the game's enumerations (outfit variations, archetypes...).
"""
from __future__ import annotations

import logging
import os
import pickle
import re
from collections import defaultdict

from ...native import N

log = logging.getLogger("omni.schema")

_CLASS = re.compile(r"^\[modules:/(.+?)\.class\](\(\w+\))?\.entity(?:type|blueprint)$", re.I)

# the same type is written differently in templates and in class schemas
_ALIAS = {"SEntityTemplateReference": "ZEntityReference"}


def class_name(full: str) -> str:
    """``[modules:/zbodypartentity.class].entitytype`` -> ``zbodypartentity``; the variant of a class keeps its tag
    (``zlinkedentity(materials)``); the name itself when it is not a class path."""
    m = _CLASS.match(full)
    return (m.group(1) + (m.group(2) or "")).lower() if m else full.lower()


def canonical(type_name: str) -> str:
    """Type name of a template property in the spelling of the class schemas."""
    for a, b in _ALIAS.items():
        type_name = type_name.replace(a, b)
    return type_name


class SchemaBook:
    """Every class schema of a game (about 2300 small files for 007 First Light), read once and cached."""

    VERSION = 2

    def __init__(self, source):
        self.source = source
        self.archive = source.archive
        self._classes: dict[str, list[tuple[int, str, bool]]] | None = None
        self._types: dict[int, frozenset[str]] | None = None

    # ---- loading ------------------------------------------------------------------
    @property
    def classes(self) -> dict[str, list[tuple[int, str, bool]]]:
        if self._classes is None:
            self._classes = self._load()
        return self._classes

    def _cache_file(self):
        from ...core.config import CONFIG
        return CONFIG.cache / f"schema_{self.source.id}.pkl"

    def _load(self):
        sig = [len(self.archive.index("CPPT")), self.archive._signature("CPPT"), self.VERSION]
        cache = self._cache_file()
        try:
            with open(cache, "rb") as f:
                got, data = pickle.load(f)
            if got == sig:
                return data
        except Exception:  # noqa: BLE001 - stale or corrupt cache: read the files again
            pass
        out = {}
        for h, p in self.archive.index("CPPT").items():
            try:
                out[class_name(self.source.names.name(h))] = N.class_schema(p.read_bytes())
            except (ValueError, OSError) as e:
                log.warning("unreadable class schema %016X: %s", h, e)
        if out:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(f".{os.getpid()}.tmp")
                with open(tmp, "wb") as f:
                    pickle.dump((sig, out), f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cache)
            except OSError:
                pass
        return out

    @property
    def types(self) -> dict[int, frozenset[str]]:
        """property id -> every type some class gives it."""
        if self._types is None:
            acc: dict[int, set[str]] = defaultdict(set)
            for props in self.classes.values():
                for crc, ty, _d in props:
                    acc[crc].add(ty)
            self._types = {k: frozenset(v) for k, v in acc.items()}
        return self._types

    # ---- queries ------------------------------------------------------------------
    def prop_type(self, cls: str, crc: int) -> str | None:
        for c, ty, _d in self.classes.get(cls.lower(), ()):
            if c == crc:
                return ty
        return None

    def check(self, crc: int, type_name: str) -> str:
        """``ok`` | ``unknown`` (no class has the property: a material parameter, a script value...) | ``mismatch``."""
        types = self.types.get(crc)
        if types is None:
            return "unknown"
        t = canonical(type_name)
        return "ok" if t in types or t.startswith("?") else "mismatch"

    def blueprint_classes(self) -> set[str]:
        """Classes named by the blueprints (CBLU), as the blueprints themselves spell them."""
        out = set()
        for p in self.archive.index("CBLU").values():
            try:
                out.add(N.blueprint_class(p.read_bytes()).lower())
            except (ValueError, OSError):
                pass
        return out

    def without_schema(self) -> list[str]:
        """Blueprint classes that have no class schema: entities the property check cannot cover."""
        known = {c.split("(")[0] for c in self.classes}
        return sorted(self.blueprint_classes() - known)

    def verify(self, expected: dict[str, tuple[str, int, str]]) -> list[str]:
        """Problems of the properties the code reads by id: ``{label: (class, id, type)}`` -> messages (empty: all fine)."""
        out = []
        for label, (cls, crc, ty) in expected.items():
            got = self.prop_type(cls, crc)
            if got is None:
                out.append(f"{label}: the class {cls} has no property {crc:#010x}")
            elif got != ty:
                out.append(f"{label}: {cls} types {crc:#010x} as {got}, not {ty}")
        return out

    def audit(self, props) -> dict[str, int]:
        """Counts of ``ok`` / ``unknown`` / ``mismatch`` over decoded properties (anything with ``pid`` and ``type``)."""
        n = {"ok": 0, "unknown": 0, "mismatch": 0}
        for p in props:
            n[self.check(p.pid, p.type)] += 1
        return n


# ---- enums ----------------------------------------------------------------------------
def read_enums(source) -> dict[str, dict]:
    """Every ENUM of the game: ``name -> {"members": [(name, value)], "legacy": [names]}``."""
    out = {}
    for h, p in source.archive.index("ENUM").items():
        try:
            name, members, legacy = N.enum_def(p.read_bytes())
        except (ValueError, OSError):
            continue
        out[name] = {"members": members, "legacy": legacy, "path": source.names.name(h)}
    return out
