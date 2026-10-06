"""Material variants of props, read from the game's entity templates.

The same mesh is often placed through several templates that recolour or re-material it: tintable props
(colour-mask constants), painted/rusty/clean versions (material overwrites), lights (emissive colours).
Each template referencing a mesh is resolved like an outfit part (see outfit.py): material overwrites are
properties named CRC32(slot .mi path) holding another material instance, colours are parameter values on
material entities. Distinct results become Source skins of the converted prop.

The mesh -> templates index (17 s to build from the .meta files) is cached in ``workspace/cache``.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from ...core.memo import DiskMemo
from ...native import N
from .outfit import TARGETS_PROP, OutfitResolver

MAX_PRIMS_PER_TEMPLATE = 64        # larger templates are level scenes: per-instance tweaks, not variants
MAX_VARIANTS = 31                  # + the mesh's own materials = Source's 32 skins
_MEMO = DiskMemo("prop_variants", "1")     # bump when the resolution below changes


class PropVariants:
    _lock = threading.Lock()

    def __init__(self, source, cache_dir: Path):
        self.source = source
        self.archive = source.archive
        self.db_path = Path(cache_dir) / f"templates_{source.id}.sqlite"
        self.resolver = OutfitResolver(source)
        self._sig = None

    # ---- index ----------------------------------------------------------------------
    def ensure(self) -> None:
        """Build the mesh -> templates index if it is missing. Processes take a lock file so that only one does it
        (the others wait for the finished index instead of racing to replace it, which Windows refuses)."""
        if self.db_path.exists():
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.db_path.with_suffix(".lock")
        while not self.db_path.exists():
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 600:       # a builder died: take over
                        lock.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.5)
                continue
            try:
                os.close(fd)
                self._build()
            finally:
                lock.unlink(missing_ok=True)
            break

    def _db(self) -> sqlite3.Connection:
        self.ensure()
        return sqlite3.connect(self.db_path)

    def _build(self) -> None:
        with self._lock:
            fresh = not self.db_path.exists()
            if fresh:
                tmp = self.db_path.with_suffix(f".{os.getpid()}.tmp")
                tmp.unlink(missing_ok=True)
                db = sqlite3.connect(tmp)
                db.execute("CREATE TABLE uses (prim INTEGER, temp INTEGER)")
                prims = set(self.archive.index("PRIM"))
                temps = self.archive.index("TEMP")
                rows = [(_s64(r), _s64(t)) for r, t in
                        N.template_index([(h, str(p)) for h, p in temps.items()], list(prims), MAX_PRIMS_PER_TEMPLATE)]
                db.executemany("INSERT INTO uses VALUES (?, ?)", rows)
                db.execute("CREATE INDEX i_prim ON uses (prim)")
                db.commit()
                db.close()
                tmp.replace(self.db_path)

    def templates(self, prim: int) -> list[int]:
        db = self._db()
        try:
            return [_u64(t) for (t,) in db.execute("SELECT temp FROM uses WHERE prim = ?", (_s64(prim),))]
        finally:
            db.close()

    # ---- resolution -----------------------------------------------------------------
    def variants(self, prim: int) -> list[dict]:
        """[{slot MATI: (final MATI, {param: value})}] - only templates that change something, deduplicated.
        Memoised on disk (resolving the templates reads thousands of files): the key holds the game data's
        signature, so a game update recomputes it."""
        if self._sig is None:
            self._sig = [self.archive._signature(k) for k in ("TEMP", "TBLU", "MATI", "MATT", "ASET", "PRIM")]
        key = _MEMO.key(prim, self._sig, str(self.archive.root))
        hit = _MEMO.get(key)
        if hit is None:
            hit = self._variants(prim)
            _MEMO.put(key, hit)
        return hit

    def _variants(self, prim: int) -> list[dict]:
        slots = self.resolver.slot_mati(prim)
        if not slots:
            return []
        paths = self.resolver.slot_paths(prim)
        out, seen = [], set()
        for t in self.templates(prim):
            collected: list[dict] = []
            try:
                _g, mats = self.resolver._expand(t, {}, "", collect=collected)
            except Exception:  # noqa: BLE001 - one unreadable template must not hide the others
                continue
            overwrite = {}
            for props in collected:
                for pid, p in props.items():
                    m = paths.get(pid)
                    if m is not None and isinstance(p.value, int) and self.archive.find("MATI", p.value) is not None:
                        overwrite[m] = p.value
            variant = {}
            for s in slots:
                final = overwrite.get(s, s)
                params = {}
                for me in mats:
                    if me.mati == final:
                        for p in me.props.values():
                            if p.pid != TARGETS_PROP and p.value is not None and not p.name.startswith(("0x", "m_")):
                                params[p.name] = p.value
                variant[s] = (final, params)
            if all(f == s and not prm for s, (f, prm) in variant.items()):
                continue
            key = tuple((s, f, tuple(sorted((k, _freeze(v)) for k, v in prm.items()))) for s, (f, prm) in sorted(variant.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(variant)
            if len(out) >= MAX_VARIANTS:
                break
        return out


def _freeze(v):
    return tuple(round(float(x), 4) for x in v) if isinstance(v, (tuple, list)) else (round(v, 4) if isinstance(v, float) else v)


def _s64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def _u64(v: int) -> int:
    return v + (1 << 64) if v < 0 else v
