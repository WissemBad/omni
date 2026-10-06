"""Locate extracted Glacier resources: Assets/Sorted/chunkN/<TYPE>/<HASH>.<TYPE>(.meta).

Listing a type folder (230 000 dialogue files...) takes seconds on the first launch: each listing is cached in
``cache/archive/<TYPE>.json`` and reused while the folders' modification times are unchanged (adding or removing
a file updates them).
"""
from __future__ import annotations

import json
import logging
import os
import struct
from pathlib import Path

from ...native import N
from .meta import Meta, parse_meta

log = logging.getLogger("omni.archive")


def _chunk_order(p: Path) -> tuple[int, str]:
    digits = "".join(c for c in p.name if c.isdigit())
    return (int(digits) if digits else 1 << 30, p.name)


class _Index(dict):
    """hash -> path of one resource type. Paths are kept as strings (a type holds up to 250 000 entries: building
    as many Path objects took seconds in every worker) and handed out as Path."""

    def get(self, key, default=None):
        v = dict.get(self, key)
        return default if v is None else Path(v)

    def __getitem__(self, key):
        return Path(dict.__getitem__(self, key))

    def items(self):
        return ((k, Path(v)) for k, v in dict.items(self))

    def values(self):
        return (Path(v) for v in dict.values(self))


class Archive:
    def __init__(self, sorted_root: Path, cache_dir: Path | None = None):
        self.root = sorted_root
        # chunk2 before chunk10: a resource present in several chunks comes from the lowest-numbered one
        self.chunks = sorted((p for p in sorted_root.iterdir() if p.is_dir()), key=_chunk_order) if sorted_root.exists() else []
        self._index: dict[str, dict[int, Path]] = {}
        self.cache_dir = cache_dir

    def _signature(self, kind: str) -> list:
        sig = []
        for chunk in self.chunks:
            d = chunk / kind
            try:
                sig.append([chunk.name, d.stat().st_mtime_ns])
            except OSError:
                pass
        return sig

    def index(self, kind: str) -> dict[int, Path]:
        idx = self._index.get(kind)
        if idx is not None:
            return idx
        sig = self._signature(kind)
        cache = self.cache_dir / "archive" / f"{kind}.json" if self.cache_dir else None
        if cache is not None and cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data["sig"] == sig and data["root"] == str(self.root):
                    root, sep = str(self.root), os.sep
                    idx = _Index((int(h, 16), f"{root}{sep}{c}{sep}{kind}{sep}{h}.{kind}")
                                 for c, hs in data["items"].items() for h in hs)
            except (OSError, ValueError, KeyError):
                idx = None
        if idx is None:
            idx = _Index()
            per_chunk: dict[str, list[str]] = {}
            for chunk in self.chunks:
                d = chunk / kind
                if not d.is_dir():
                    continue
                suffix = "." + kind
                names = per_chunk.setdefault(chunk.name, [])
                with os.scandir(d) as it:
                    for e in it:
                        n = e.name
                        if n.endswith(suffix) and len(n) == 16 + len(suffix):
                            try:
                                h = int(n[:16], 16)
                            except ValueError:
                                continue
                            if h not in idx:
                                dict.__setitem__(idx, h, e.path)
                                names.append(n[:16])
            if cache is not None:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cache.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"root": str(self.root), "sig": sig, "items": per_chunk}), encoding="utf-8")
                    os.replace(tmp, cache)
                except OSError:
                    pass
        self._index[kind] = idx
        return idx

    def find(self, kind: str, h: int) -> Path | None:
        return self.index(kind).get(h)

    def labels(self, kind: str, hashes: list[int]) -> list[str]:
        """Original Wwise name of each .wem (``""`` when it has none), read by the Rust core in parallel."""
        idx = self.index(kind)
        return N.wem_labels([(dict.get(idx, h) or "", 0, -1) for h in hashes])

    def read_many(self, kind: str, hashes: list[int]) -> list[bytes | None]:
        """Whole resources, read in parallel (``None`` for one that cannot be read)."""
        idx = self.index(kind)
        return N.read_files([dict.get(idx, h) or "" for h in hashes])

    def flagged_refs(self, kind: str, hashes: list[int]) -> dict[int, list[tuple[int, int]]]:
        """{hash: [(referenced hash, flag)]} from the ``.meta`` files, read in parallel (absent ones are left out)."""
        idx = self.index(kind)
        rows = N.meta_refs_flags([dict.get(idx, h) or "" for h in hashes])
        return {h: r for h, r in zip(hashes, rows) if r is not None}

    def meta(self, path: Path) -> Meta | None:
        mp = Path(str(path) + ".meta")
        if not mp.is_file():
            return None
        try:
            return parse_meta(mp.read_bytes())
        except (OSError, struct.error, IndexError, ValueError) as e:
            log.warning("unreadable .meta %s: %s", mp.name, e)
            return None
