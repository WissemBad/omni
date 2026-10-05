"""SQLite catalog of convertible assets, shared by the CLI and the web UI."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import CONFIG


class Catalog:
    def __init__(self, source, path: Path | None = None):
        self.source = source
        self.path = path or CONFIG.workspace / f"catalog_{source.id}.sqlite"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS assets(
            key TEXT PRIMARY KEY, name TEXT, rel TEXT, size INTEGER, cat TEXT, skinned INTEGER, linked INTEGER)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS assets_cat ON assets(cat)")

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def build(self, force: bool = False) -> int:
        if self.count() and not force:
            return self.count()
        self.db.execute("DELETE FROM assets")
        batch = []
        for r in self.source.catalog_rows():
            batch.append((r["key"], r["name"], r["rel"], r["size"], r["cat"], int(r["skinned"]), int(r["linked"])))
            if len(batch) >= 2000:
                self.db.executemany("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            self.db.executemany("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?)", batch)
        self.db.commit()
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
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM assets{where} ORDER BY rel LIMIT ? OFFSET ?", [*args, limit, offset])]

    def total(self, q: str = "", cat: str = "", skinned: int | None = None, named_only: bool = True) -> int:
        where, args = self._where(q, cat, skinned, named_only)
        return self.db.execute(f"SELECT COUNT(*) FROM assets{where}", args).fetchone()[0]

    def keys(self, q: str = "", cat: str = "", skinned: int | None = None, named_only: bool = True,
             limit: int = 20000) -> list[str]:
        where, args = self._where(q, cat, skinned, named_only)
        return [r[0] for r in self.db.execute(f"SELECT key FROM assets{where} ORDER BY rel LIMIT ?", [*args, limit])]

    def categories(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT cat, COUNT(*) n FROM assets WHERE name != '' GROUP BY cat ORDER BY cat")]
