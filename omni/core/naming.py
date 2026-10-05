"""Hash-list name handling and game-safe path/identifier generation."""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

_STRIP_PREFIXES = ("_knt/", "_pro/", "_licensed/", "environment/geometry/", "environment/", "geometry/")


def ioi_hash(path: str) -> int:
    """Runtime resource id of a game path, 007 First Light flavour: md5(lower-case path), first 8 bytes big
    endian, top byte replaced by 0x01 (Hitman 3 uses 0x00). Verified on the hash list."""
    import hashlib
    v = int.from_bytes(hashlib.md5(path.lower().encode()).digest()[:8], "big")
    return (v & 0x00FFFFFFFFFFFFFF) | (1 << 56)


def slug(s: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    return s[:maxlen].strip("_") or "x"


def parse_ioi(name: str) -> tuple[list[str], str, str]:
    """``[assembly:/a/b/c.wl2?/leaf.prim].prim`` -> (['a','b'], 'c', 'leaf').

    Returns (directories, container stem, leaf stem). Container is '' when the
    path has no ``?/`` part (then leaf is the file stem).
    """
    n = name.strip()
    n = re.sub(r"^[\[\(]*assembly:/", "", n)
    n = re.sub(r"\]\.[a-z0-9]+$", "", n)
    n = re.sub(r"\]\(.*$", "", n)
    n = n.rstrip("]")
    container = ""
    leaf = ""
    if "?/" in n:
        left, leaf = n.split("?/", 1)
    else:
        left = n
    parts = [p for p in left.split("/") if p]
    file_part = parts.pop() if parts else ""
    stem = file_part.split(".")[0]
    if leaf:
        container = stem
        leaf = leaf.split(".")[0]
    else:
        leaf = stem
    joined = "/".join(parts) + "/"
    for pre in _STRIP_PREFIXES:
        while joined.startswith(pre):
            joined = joined[len(pre):]
    return [p for p in joined.split("/") if p], container, leaf


class Names:
    """hash -> (type, ioi name) lookup, backed by a SQLite cache built once from hash_list.txt."""

    def __init__(self, hash_list: Path, db_path: Path):
        self.hash_list, self.db_path = hash_list, db_path
        self._db: sqlite3.Connection | None = None
        self._lock = threading.RLock()      # one connection shared by the web server's and the exporters' threads

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            return self._open()

    def _open(self) -> sqlite3.Connection:
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.db_path.exists()
            db = sqlite3.connect(self.db_path, check_same_thread=False)
            if fresh or not db.execute("SELECT name FROM sqlite_master WHERE name='names'").fetchone():
                self._build(db)
            self._db = db
        return self._db

    def _build(self, db: sqlite3.Connection) -> None:
        db.execute("DROP TABLE IF EXISTS names")
        db.execute("CREATE TABLE names(h INTEGER PRIMARY KEY, t TEXT, n TEXT)")
        batch = []
        with open(self.hash_list, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if len(line) < 20 or line[0] == "#":
                    continue
                try:
                    h = int(line[:16], 16)
                except ValueError:
                    continue
                dot = line.find(",", 17)
                if dot < 0:
                    continue
                batch.append((_s64(h), line[17:dot], line[dot + 1:].strip()))
                if len(batch) >= 200000:
                    db.executemany("INSERT OR REPLACE INTO names VALUES(?,?,?)", batch)
                    batch.clear()
        if batch:
            db.executemany("INSERT OR REPLACE INTO names VALUES(?,?,?)", batch)
        db.commit()

    def get(self, h: int) -> tuple[str, str] | None:
        # hash_list stores the name keyed by the 64-bit hash; sqlite ints are signed 64-bit
        with self._lock:
            row = self._connect().execute("SELECT t,n FROM names WHERE h=?", (_s64(h),)).fetchone()
        return (row[0], row[1]) if row else None

    def name(self, h: int) -> str:
        r = self.get(h)
        return r[1] if r else ""

    def type_of(self, h: int) -> str:
        r = self.get(h)
        return r[0] if r else ""


def _s64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v
