"""SQLite catalog of a source's textures and of the links texture -> material -> model (the Textures workbench).

Built once from ``source.texture_index()`` (a few seconds) and rebuilt on demand. Any source offering the
``textures`` capability gets search, folders, filters and the "used by" lists for free.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import CONFIG

VERSION = 3


class TextureCatalog:
    def __init__(self, source, path: Path | None = None):
        self.source = source
        self.path = path or CONFIG.workspace / f"textures_{source.id}.sqlite"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._start = threading.Lock()
        self.building = False
        self.progress = ""
        self.error = ""
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE IF NOT EXISTS textures(key TEXT PRIMARY KEY, name TEXT, folder TEXT, fmt TEXT,
                    width INTEGER, height INTEGER, mips INTEGER, bytes INTEGER, role TEXT, named INTEGER,
                    materials INTEGER DEFAULT 0, models INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS materials(key TEXT PRIMARY KEY, name TEXT, cls TEXT, source TEXT);
                CREATE TABLE IF NOT EXISTS tex_mat(tex TEXT, mat TEXT, slot TEXT, role TEXT);
                CREATE TABLE IF NOT EXISTS mat_model(mat TEXT, model TEXT);
                CREATE INDEX IF NOT EXISTS tex_folder ON textures(folder);
                CREATE INDEX IF NOT EXISTS tm_tex ON tex_mat(tex);
                CREATE INDEX IF NOT EXISTS tm_mat ON tex_mat(mat);
                CREATE INDEX IF NOT EXISTS mm_mat ON mat_model(mat);
                CREATE INDEX IF NOT EXISTS mm_model ON mat_model(model);
            """)

    # ---- build -------------------------------------------------------------------------------------------
    def ready(self) -> bool:
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k='version'").fetchone()
            n = self.db.execute("SELECT COUNT(*) FROM textures").fetchone()[0]
        return bool(r) and int(r[0]) == VERSION and n > 0

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM textures").fetchone()[0]

    def build(self) -> None:
        with self._start:                      # two callers must not both start a build
            if self.building:
                return
            self.building, self.error = True, ""
        t0 = time.perf_counter()
        try:
            def say(m):
                self.progress = m
            idx = self.source.texture_index(progress=say)
            say("écriture du catalogue")
            users: dict[str, set] = {}
            for tex, mat, _slot, _role in idx["tex_mat"]:
                users.setdefault(tex, set()).add(mat)
            mat_models: dict[str, set] = {}
            for mat, model in idx["mat_model"]:
                mat_models.setdefault(mat, set()).add(model)
            with self.lock:
                db = self.db
                db.execute("DELETE FROM textures")
                db.execute("DELETE FROM materials")
                db.execute("DELETE FROM tex_mat")
                db.execute("DELETE FROM mat_model")
                db.executemany("INSERT OR REPLACE INTO textures VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
                    (r["key"], r["name"], r["folder"], r["fmt"], r["width"], r["height"], r["mips"], r["bytes"],
                     r["role"], int(r["named"]), len(users.get(r["key"], ())),
                     len(set().union(*(mat_models.get(m, set()) for m in users.get(r["key"], ())))))
                    for r in idx["textures"]])
                db.executemany("INSERT OR REPLACE INTO materials VALUES(?,?,?,?)",
                               [(m["key"], m["name"], m["cls"], m.get("source", "")) for m in idx["materials"]])
                db.executemany("INSERT INTO tex_mat VALUES(?,?,?,?)", idx["tex_mat"])
                db.executemany("INSERT INTO mat_model VALUES(?,?)", idx["mat_model"])
                db.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (str(VERSION),))
                db.execute("INSERT OR REPLACE INTO meta VALUES('built', ?)", (str(time.time()),))
                db.commit()
            self.progress = f"{len(idx['textures'])} textures indexées en {time.perf_counter() - t0:.0f} s"
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.building = False

    def build_async(self) -> None:
        if not self.building:
            threading.Thread(target=self.build, daemon=True).start()

    # ---- queries -----------------------------------------------------------------------------------------
    @staticmethod
    def _where(q: str, folder: str, fmt: str, role: str, usage: str, min_size: int) -> tuple[str, list]:
        sql, args = " WHERE 1=1", []
        for tok in q.lower().split():
            sql += " AND (lower(name) LIKE ? OR key LIKE ?)"
            args += [f"%{tok}%", f"{tok.upper()}%"]
        if folder:
            sql += " AND (folder = ? OR folder LIKE ?)"
            args += [folder, folder + "/%"]
        if fmt:
            sql += " AND fmt = ?"
            args.append(fmt)
        if role:
            sql += " AND role = ?"
            args.append(role)
        if usage == "used":
            sql += " AND materials > 0"
        elif usage == "unused":
            sql += " AND materials = 0"
        elif usage == "named":
            sql += " AND named = 1"
        if min_size:
            sql += " AND max(width, height) >= ?"
            args.append(min_size)
        return sql, args

    SORTS = {"name": "folder, name", "size": "bytes DESC", "dims": "width * height DESC", "used": "models DESC"}

    def search(self, q="", folder="", fmt="", role="", usage="", min_size=0, sort="name", limit=200, offset=0):
        where, args = self._where(q, folder, fmt, role, usage, min_size)
        order = self.SORTS.get(sort, self.SORTS["name"])
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM textures{where}", args).fetchone()[0]
            rows = [dict(r) for r in self.db.execute(
                f"SELECT * FROM textures{where} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset])]
        return total, rows

    def facets(self, q="", folder="", usage=""):
        where, args = self._where(q, folder, "", "", usage, 0)
        with self.lock:
            fmts = [dict(r) for r in self.db.execute(
                f"SELECT fmt AS value, COUNT(*) AS n FROM textures{where} GROUP BY fmt ORDER BY n DESC", args)]
            roles = [dict(r) for r in self.db.execute(
                f"SELECT role AS value, COUNT(*) AS n FROM textures{where} GROUP BY role ORDER BY n DESC", args)]
        return {"fmt": fmts, "role": roles}

    def categories(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT folder AS cat, COUNT(*) AS n FROM textures GROUP BY folder ORDER BY folder")]

    def get(self, key: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM textures WHERE key = ?", (key.upper(),)).fetchone()
        return dict(r) if r else None

    def users(self, key: str, limit: int = 300) -> dict:
        """Materials using a texture (with the slot) and the models using those materials."""
        key = key.upper()
        with self.lock:
            mats = [dict(r) for r in self.db.execute(
                "SELECT m.key, m.name, m.cls, m.source, t.slot, t.role FROM tex_mat t JOIN materials m ON m.key = t.mat "
                "WHERE t.tex = ? ORDER BY m.name LIMIT ?", (key, limit))]
            models = [r[0] for r in self.db.execute(
                "SELECT DISTINCT mm.model FROM tex_mat t JOIN mat_model mm ON mm.mat = t.mat WHERE t.tex = ? LIMIT ?",
                (key, limit))]
        return {"materials": mats, "models": models}

    def textures_of_model(self, model: str) -> list[dict]:
        """Every texture a model's materials use (the reverse link, for the model inspectors)."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT DISTINCT tx.*, t.slot, t.role AS slot_role, t.mat FROM mat_model mm "
                "JOIN tex_mat t ON t.mat = mm.mat JOIN textures tx ON tx.key = t.tex WHERE mm.model = ?",
                (model.upper(),))]

    def stats(self) -> dict:
        with self.lock:
            r = self.db.execute("SELECT COUNT(*), SUM(bytes), SUM(named), SUM(materials > 0) FROM textures").fetchone()
        return {"textures": r[0] or 0, "bytes": r[1] or 0, "named": r[2] or 0, "used": r[3] or 0}

    def material_textures(self, key: str) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT tx.key, tx.name, tx.fmt, tx.width, tx.height, tx.role, t.slot FROM tex_mat t "
                "JOIN textures tx ON tx.key = t.tex WHERE t.mat = ?", (key.upper(),))]

    def material_for_vmt(self, vmt_name: str) -> dict | None:
        """The game material a converted VMT comes from: omni names it ``<slug>_<6 hex of the material key>``,
        optionally followed by a variant tag (``_<6 hex>``)."""
        import re as _re
        parts = vmt_name.lower().split("_")
        for i in (len(parts) - 1, len(parts) - 2):
            if i > 0 and _re.fullmatch(r"[0-9a-f]{6}", parts[i]):
                prefix = "_".join(parts[:i + 1])
                with self.lock:
                    r = self.db.execute("SELECT * FROM materials WHERE key LIKE ? AND lower(name) = ?",
                                        (f"%{parts[i].upper()}", prefix)).fetchone()
                if r:
                    return {**dict(r), "textures": self.material_textures(r["key"])}
        return None
