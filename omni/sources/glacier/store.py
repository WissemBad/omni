"""Glacier resources read in place from the game's packages (no extraction): the same interface as ``Archive``.

``StoreArchive`` answers ``find`` / ``index`` / ``meta`` like the extracted ``Assets/Sorted`` archive does, but its
"paths" are ``ResPath`` handles on resources of the native ``GlacierStore`` (``read_bytes()``, ``stat().st_size``,
``open("rb")``). The parsers and builders work unchanged on both; sound media are read by range through
``read_range`` (any process, one store each).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from ...core.ir import SoundRef

from .meta import Meta


class _Stat:
    __slots__ = ("st_size", "st_mtime_ns", "st_mtime")

    def __init__(self, size: int, mtime_ns: int):
        self.st_size = size
        self.st_mtime_ns = mtime_ns
        self.st_mtime = mtime_ns / 1e9


class ResPath:
    """A resource of the packages, used where an extracted file path was."""
    __slots__ = ("store", "hash", "type", "_size", "_mtime")

    def __init__(self, store: "StoreArchive", h: int, ty: str, size: int, mtime_ns: int = 0):
        self.store, self.hash, self.type, self._size, self._mtime = store, h, ty, size, mtime_ns

    @property
    def name(self) -> str:
        return f"{self.hash:016X}.{self.type}"

    @property
    def suffix(self) -> str:
        return "." + self.type

    def read_bytes(self) -> bytes:
        return self.store.read(self.hash, self.type)

    def stat(self) -> _Stat:
        return _Stat(self._size, self._mtime)

    def exists(self) -> bool:
        return True

    def is_file(self) -> bool:
        return True

    def open(self, mode: str = "rb"):
        if "r" not in mode or "b" not in mode:
            raise OSError("package resources are read-only binary")
        return io.BytesIO(self.read_bytes())

    def __str__(self) -> str:
        return f"rpkg:{self.hash:016X}.{self.type}"

    __repr__ = __str__

    def __eq__(self, other) -> bool:
        return isinstance(other, ResPath) and other.hash == self.hash and other.type == self.type

    def __hash__(self) -> int:
        return hash((self.hash, self.type))


class _StoreIndex(dict):
    """hash -> ResPath of one type (handles made on demand: a type can hold 250 000 resources)."""

    def __init__(self, archive: "StoreArchive", kind: str, sizes: dict[int, int]):
        super().__init__(sizes)
        self._a, self._k = archive, kind

    def _p(self, h: int, size: int) -> ResPath:
        return ResPath(self._a, h, self._k, size, self._a.mtime_ns)

    def get(self, key, default=None):
        v = dict.get(self, key)
        return default if v is None else self._p(key, v)

    def __getitem__(self, key):
        return self._p(key, dict.__getitem__(self, key))

    def items(self):
        return ((h, self._p(h, s)) for h, s in dict.items(self))

    def values(self):
        return (self._p(h, s) for h, s in dict.items(self))


def packages_of(folder: Path) -> list[Path]:
    """Every package of a game's ``Runtime`` folder, base chunks and patches."""
    return sorted(p for p in folder.glob("chunk*.rpkg") if p.is_file())


class StoreArchive:
    def __init__(self, packages: list[Path], text_hook=None):
        from ...native import N
        self.packages = [Path(p) for p in packages]
        self.root = self.packages[0].parent if self.packages else Path(".")
        self.store = N.GlacierStore([str(p) for p in self.packages])
        self.mtime_ns = max((p.stat().st_mtime_ns for p in self.packages), default=0)
        self._index: dict[str, _StoreIndex] = {}
        # (type -> bytes transform): a game whose resources differ only by a header (Hitman TEXT) is read through
        # the decoders written for the other one
        self.text_hook = text_hook
        self.chunks: list = []

    def _signature(self, kind: str) -> list:
        return [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in self.packages]

    def index(self, kind: str) -> _StoreIndex:
        idx = self._index.get(kind)
        if idx is None:
            sizes = {}
            for h in self.store.of_type(kind):
                m = self.store.meta(h)
                sizes[h] = m[1] if m else 0
            idx = self._index[kind] = _StoreIndex(self, kind, sizes)
        return idx

    def find(self, kind: str, h: int) -> ResPath | None:
        return self.index(kind).get(h)

    def labels(self, kind: str, hashes: list[int]) -> list[str]:
        return self.store.labels(list(hashes))

    def read_many(self, kind: str, hashes: list[int]) -> list[bytes | None]:
        out = []
        for h in hashes:
            try:
                out.append(self.read(h, kind))
            except (RuntimeError, ValueError, OSError):
                out.append(None)
        return out

    def flagged_refs(self, kind: str, hashes: list[int]) -> dict[int, list[tuple[int, int]]]:
        out = {}
        for h in hashes:
            m = self.store.meta(h)
            if m is not None:
                out[h] = list(m[2])
        return out

    def read(self, h: int, kind: str = "") -> bytes:
        data = self.store.read(h)
        if self.text_hook is not None and kind in ("TEXT",):
            data = self.text_hook(data)
        return data

    def meta(self, path) -> Meta | None:
        h = path.hash if isinstance(path, ResPath) else int(Path(str(path)).name[:16], 16)
        m = self.store.meta(h)
        if m is None:
            return None
        return Meta(h, m[0], list(m[2]))

    def refs_many(self, paths: list) -> list[list[int]]:
        out = []
        for p in paths:
            m = self.store.meta(p.hash if isinstance(p, ResPath) else int(Path(str(p)).name[:16], 16))
            out.append([h for h, _f in m[2]] if m else [])
        return out


_STORES: dict[tuple, StoreArchive] = {}


def shared_store(packages: list) -> StoreArchive:
    """One store per process and package set (sound workers read media through it)."""
    key = tuple(str(p) for p in packages)
    s = _STORES.get(key)
    if s is None:
        s = _STORES[key] = StoreArchive([Path(p) for p in packages])
    return s


def read_range(packages: list, file: str, offset: int, size: int) -> bytes:
    """Bytes of a sound reference whose ``file`` is a package resource (``rpkg:<hash>.<type>``)."""
    h = int(file[5:21], 16)
    data = shared_store(packages).store.read(h)
    return data[offset:] if size < 0 else data[offset:offset + size]


@dataclass
class PackageSoundRef(SoundRef):
    """A sound inside the packages: ``file`` is ``rpkg:<hash>.<type>``, read by range in any process."""
    packages: list = field(default_factory=list)

    def read(self) -> bytes:
        return read_range(self.packages, self.file, self.offset, self.size)


def package_refs(refs: list, packages: list) -> list:
    """Sound references of a store-backed source, made readable in the export workers."""
    pk = [str(p) for p in packages]
    return [r if isinstance(r, PackageSoundRef) or not str(r.file).startswith("rpkg:") else
            PackageSoundRef(r.path, r.file, r.offset, r.size, r.source_id, r.priority, r.meta, pk) for r in refs]
