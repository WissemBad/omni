"""Persistent memo of expensive, deterministic results (convex decompositions, template resolutions...).

One SQLite file per memo in ``<workspace>/cache``, shared by every batch worker (WAL: readers never wait for a writer)
and kept between sessions. Values are pickled. A memo is an optimisation only: any error reading or writing it is
ignored and the caller computes the value again. Keys must contain everything the value depends on (input bytes,
parameters, a version that is bumped when the code producing the value changes).
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("omni.memo")


class DiskMemo:
    def __init__(self, name: str, version: str = "1", directory: Path | None = None):
        self.name, self.version, self._dir = name, version, directory
        self._db: sqlite3.Connection | None = None
        self._pid = 0
        self._lock = threading.Lock()
        self._mem: dict[str, object] = {}
        self.disabled = os.environ.get("OMNI_MEMO", "1") == "0"

    @property
    def path(self) -> Path:
        if self._dir is None:
            from .config import CONFIG
            return CONFIG.cache / f"memo_{self.name}.sqlite"
        return self._dir / f"memo_{self.name}.sqlite"

    def _conn(self) -> sqlite3.Connection | None:
        if self._db is not None and self._pid == os.getpid():
            return self._db
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(p, timeout=30, check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("CREATE TABLE IF NOT EXISTS memo (k TEXT PRIMARY KEY, v BLOB)")
        self._db, self._pid, self._mem = db, os.getpid(), {}
        return db

    def key(self, *parts) -> str:
        """Hash of ``parts`` (numpy arrays by their bytes, shape and dtype; anything else by its repr)."""
        h = hashlib.blake2b(digest_size=20)
        h.update(f"{self.name}:{self.version}".encode())
        for x in parts:
            if hasattr(x, "tobytes") and hasattr(x, "dtype"):
                h.update(f"{x.dtype}{x.shape}".encode())
                h.update(x.tobytes())
            elif isinstance(x, (bytes, bytearray, memoryview)):
                h.update(bytes(x))
            elif isinstance(x, dict):
                h.update(repr(sorted(x.items())).encode())
            else:
                h.update(repr(x).encode())
            h.update(b"\x00")
        return h.hexdigest()

    def get(self, key: str):
        if self.disabled:
            return None
        if key in self._mem:
            return self._mem[key]
        try:
            with self._lock:
                row = self._conn().execute("SELECT v FROM memo WHERE k = ?", (key,)).fetchone()
            if row is None:
                return None
            v = pickle.loads(row[0])
            self._mem[key] = v
            return v
        except Exception as e:  # noqa: BLE001 - a memo never breaks a conversion
            log.debug("memo %s read: %s", self.name, e)
            return None

    def put(self, key: str, value) -> None:
        if self.disabled:
            return
        self._mem[key] = value
        try:
            blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            with self._lock:
                self._conn().execute("INSERT OR REPLACE INTO memo VALUES (?, ?)", (key, blob))
        except Exception as e:  # noqa: BLE001
            log.debug("memo %s write: %s", self.name, e)
