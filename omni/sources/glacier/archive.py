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

from .meta import Meta, parse_meta

log = logging.getLogger("omni.archive")


def _chunk_order(p: Path) -> tuple[int, str]:
    digits = "".join(c for c in p.name if c.isdigit())
    return (int(digits) if digits else 1 << 30, p.name)


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
                    idx = {int(h, 16): self.root / c / kind / f"{h}.{kind}" for c, hs in data["items"].items() for h in hs}
            except (OSError, ValueError, KeyError):
                idx = None
        if idx is None:
            idx = {}
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
                                idx[h] = Path(e.path)
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

    def meta(self, path: Path) -> Meta | None:
        mp = Path(str(path) + ".meta")
        if not mp.is_file():
            return None
        try:
            return parse_meta(mp.read_bytes())
        except (OSError, struct.error, IndexError, ValueError) as e:
            log.warning("unreadable .meta %s: %s", mp.name, e)
            return None
