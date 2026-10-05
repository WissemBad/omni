"""SQLite catalog of convertible assets, shared by the CLI and the web UI.

One connection, guarded by a lock: FastAPI's thread pool and the build thread use it at the same time. A rebuild
fills a scratch table and swaps it in one transaction, so readers never see a half-built catalog. ``VERSION`` is
stored in ``user_version``: bumping it (new row layout, new path rules) rebuilds the catalog at the next start.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import CONFIG

VERSION = 2          # 2: output paths unique per resource (shared readable paths get a hash suffix)

_COLUMNS = "key TEXT PRIMARY KEY, name TEXT, rel TEXT, size INTEGER, cat TEXT, skinned INTEGER, linked INTEGER"


class Catalog:
    def __init__(self, source, path: Path | None = None):
        self.source = source
        self.path = path or CONFIG.workspace / f"catalog_{source.id}.sqlite"
        self.lock = threading.RLock()
        self._building = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            if self.db.execute("PRAGMA user_version").fetchone()[0] != VERSION:
                self.db.execute("DROP TABLE IF EXISTS assets")
            self.db.execute(f"CREATE TABLE IF NOT EXISTS assets({_COLUMNS})")
            self.db.execute("CREATE INDEX IF NOT EXISTS assets_cat ON assets(cat)")
            self.db.commit()

    def _query(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def count(self) -> int:
        return self._query("SELECT COUNT(*) FROM assets")[0][0]

    def build(self, force: bool = False) -> int:
        if self.count() and not force:
            return self.count()
        if not self._building.acquire(blocking=False):        # a build is already running: do not start a second
            return self.count()
        try:
            return self._build()
        finally:
            self._building.release()

    def _build(self) -> int:
        rows = [(r["key"], r["name"], r["rel"], r["size"], r["cat"], int(r["skinned"]), int(r["linked"]))
                for r in self.source.catalog_rows()]          # read the game outside the lock
        with self.lock:
            db = self.db
            db.execute("DROP TABLE IF EXISTS assets_new")
            db.execute(f"CREATE TABLE assets_new({_COLUMNS})")
            db.executemany("INSERT OR REPLACE INTO assets_new VALUES(?,?,?,?,?,?,?)", rows)
            db.execute("DROP TABLE assets")
            db.execute("ALTER TABLE assets_new RENAME TO assets")
            db.execute("CREATE INDEX IF NOT EXISTS assets_cat ON assets(cat)")
            db.execute(f"PRAGMA user_version={VERSION}")
            db.commit()
        return self.count()

    @staticmethod
    def _where(q: str, cat: str, skinned: int | None, named_only: bool) -> tuple[str, list]:
        sql, args = " WHERE 1=1", []
        for tok in q.lower().split():
            sql += " AND (lower(name) LIKE ? OR key LIKE ?)"
            args += [f"%{tok}%", f"{tok.upper()}%"]
        if cat:
            sql += " AND (cat = ? OR cat LIKE ?)"
            args += [cat, cat + "/%"]
        if skinned is not None:
            sql += " AND skinned=?"
            args.append(skinned)
        if named_only:
            sql += " AND name != ''"
        return sql, args

    def search(self, q: str = "", cat: str = "", skinned: int | None = None, named_only: bool = True,
               limit: int = 100, offset: int = 0) -> list[dict]:
        where, args = self._where(q, cat, skinned, named_only)
        return [dict(r) for r in self._query(
            f"SELECT * FROM assets{where} ORDER BY rel LIMIT ? OFFSET ?", [*args, limit, offset])]

    def total(self, q: str = "", cat: str = "", skinned: int | None = None, named_only: bool = True) -> int:
        where, args = self._where(q, cat, skinned, named_only)
        return self._query(f"SELECT COUNT(*) FROM assets{where}", args)[0][0]

    def keys(self, q: str = "", cat: str = "", skinned: int | None = None, named_only: bool = True,
             limit: int = 20000) -> list[str]:
        where, args = self._where(q, cat, skinned, named_only)
        return [r[0] for r in self._query(f"SELECT key FROM assets{where} ORDER BY rel LIMIT ?", [*args, limit])]

    def categories(self) -> list[dict]:
        return [dict(r) for r in self._query(
            "SELECT cat, COUNT(*) n FROM assets WHERE name != '' GROUP BY cat ORDER BY cat")]
