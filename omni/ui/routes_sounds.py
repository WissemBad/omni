"""Sounds workbench API: export (Rust core, parallel), browse the exported tree with its tags, listen."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import settings
from ..core.config import CONFIG
from ..core.windows import reveal


class SoundExport(BaseModel):
    format: str | None = None
    match: str = ""
    languages: str | None = None
    folder: str = ""               # sub-folder of workspace/audio/<source> (default: the format name)
    force: bool = False            # rewrite files already exported (new names / tags)
    clean: bool = False            # then remove the files an earlier naming left
    workers: int | None = None


class SoundIndex:
    """index.csv of an exported sound tree, loaded once and searched in memory."""

    ORDER = {"events": 0, "voices": 1, "banks": 2}

    def __init__(self, root: Path):
        self.root, self._mtime, self.rows, self.low = root, 0.0, [], []

    def load(self) -> bool:
        f = self.root / "index.csv"
        if not f.exists():
            self.rows, self.low = [], []
            return False
        if f.stat().st_mtime != self._mtime:
            with open(f, newline="", encoding="utf-8") as fh:
                self.rows = list(csv.DictReader(fh))
            self.rows.sort(key=lambda r: (self.ORDER.get(r["file"].split("/", 1)[0], 3), r["file"]))
            self.low = [(r["file"] + " " + r.get("aliases", "") + " " + r.get("title", "") + " " + r.get("album", "")).lower()
                        for r in self.rows]
            self._mtime = f.stat().st_mtime
        return True

    @staticmethod
    def is_named(file: str) -> bool:
        """False for files only known by their hash (``banks/0100.../128d4fdb.flac``)."""
        return not re.fullmatch(r"[0-9a-f]{6,16}", file.rsplit("/", 1)[-1].rsplit(".", 1)[0])

    def search(self, q: str, top: str, limit: int, offset: int, named: bool = False, lang: str = "", ext: str = ""):
        words = q.lower().split()
        hits = [i for i, low in enumerate(self.low)
                if (not top or self.rows[i]["file"].startswith(top + "/")) and all(w in low for w in words)
                and (not named or self.is_named(self.rows[i]["file"]))
                and (not lang or self.rows[i].get("language", "") == lang
                     or (not self.rows[i].get("language") and f"/{lang}/" in self.rows[i]["file"]))
                and (not ext or self.rows[i]["file"].endswith("." + ext))]
        return len(hits), [self.rows[i] for i in hits[offset:offset + limit]]


def register(app: FastAPI, *, jobs, need) -> None:
    sound_idx: dict[str, SoundIndex] = {}

    SETS = ("ogg", "mp3", "flac", "wav")

    def root_of(sid: str, fmt: str = "") -> Path:
        """``workspace/audio/<source>/<format>``: one folder per exported format (auto lives at the root)."""
        base = CONFIG.workspace / "audio" / sid
        if fmt in SETS:
            return base / fmt
        for f in SETS:                                   # default: the first set that has been exported
            if (base / f / "index.csv").exists():
                return base / f
        return base

    def index_of(sid: str, fmt: str = "") -> SoundIndex:
        r = root_of(sid, fmt)
        idx = sound_idx.get((sid, str(r)))
        if idx is None:
            idx = sound_idx[(sid, str(r))] = SoundIndex(r)
        return idx

    @app.get("/api/{sid}/sounds/status")
    def sound_status(sid: str, set: str = ""):
        need(sid, "sounds")
        root = root_of(sid, set)
        idx = index_of(sid, set)
        summary = root / "summary.json"
        running = jobs.running("sounds", sid)
        return {"exported": idx.load(), "path": str(root), "count": len(idx.rows),
                "summary": json.loads(summary.read_text()) if summary.exists() else None,
                "running": running[0]["id"] if running else None,
                "tagged": bool(idx.rows) and "title" in idx.rows[0], "set": root.name if root.name in SETS else "",
                "sets": [f for f in SETS if (CONFIG.workspace / "audio" / sid / f / "index.csv").exists()]}

    def start_sounds(sid: str, body: dict) -> dict:
        req = SoundExport(**body)
        src = need(sid, "sounds")
        st = settings.load()["sounds"]
        fmt = req.format or st["format"]
        job = jobs.create("sounds", f"Sons ({fmt})", sid, request={"op": "sounds", "sid": sid, "body": body})

        def run(job):
            from ..targets.audio.export import export_sounds
            say = lambda m: jobs.log(job, m)  # noqa: E731
            st = jobs.stager(job, 3)
            st("Liste des sons")
            refs = src.list_sounds(progress=say)
            return export_sounds(refs, root_of(sid, req.folder or fmt), fmt, req.workers or st["workers"], req.match, 0, progress=say,
                                 tags=st["tags"], skip_stubs=st["skip_stubs"], languages=req.languages or st["languages"],
                                 cancel=jobs.cancel_event(job), on_count=lambda d, t: jobs.count(job, d, t),
                                 force=req.force, clean=req.clean, stage=st)
        jobs.run(job, run)
        return {"job": job["id"]}
    jobs.starters["sounds"] = start_sounds

    @app.post("/api/{sid}/sounds/export")
    def sound_export(sid: str, req: SoundExport):
        """Queue the export; an interrupted one resumes where it stopped (existing files are kept)."""
        return start_sounds(sid, req.model_dump())

    @app.get("/api/{sid}/sounds")
    def sounds(sid: str, q: str = "", top: str = "", limit: int = 100, offset: int = 0, named: int = 0,
               lang: str = "", ext: str = "", set: str = ""):
        need(sid, "sounds")
        idx = index_of(sid, set)
        if not idx.load():
            return {"total": 0, "items": [], "tops": [], "langs": []}
        total, page = idx.search(q, top, min(limit, 500), offset, bool(named), lang, ext)
        tops: dict[str, int] = {}
        langs: dict[str, int] = {}
        for r in idx.rows:
            t = r["file"].split("/", 1)[0]
            tops[t] = tops.get(t, 0) + 1
            lg = r.get("language", "")
            if lg:
                langs[lg] = langs.get(lg, 0) + 1
        return {"total": total,
                "tops": [{"value": k, "n": v} for k, v in sorted(tops.items(), key=lambda kv: (SoundIndex.ORDER.get(kv[0], 3), kv[0]))],
                "langs": [{"value": k, "n": v} for k, v in sorted(langs.items())],
                "items": [{"file": r["file"], "seconds": r["seconds"], "channels": r["channels"], "rate": r["rate"],
                           "codec": r["codec"], "named": idx.is_named(r["file"]), "title": r.get("title", ""),
                           "album": r.get("album", ""), "genre": r.get("genre", ""), "language": r.get("language", ""),
                           "sources": r.get("sources", "").split()[:4],
                           "aliases": [a.strip() for a in r["aliases"].split("|") if a.strip()][:6],
                           "alias_count": r["aliases"].count("|") + 1 if r["aliases"] else 0} for r in page]}

    @app.get("/api/{sid}/sounds/file")
    def sound_file(sid: str, path: str, set: str = ""):
        need(sid, "sounds")
        root = root_of(sid, set).resolve()
        f = (root / path).resolve()
        if root not in f.parents or not f.is_file():
            raise HTTPException(404, "Introuvable")
        return FileResponse(f)

    @app.post("/api/{sid}/sounds/reveal")
    def sound_reveal(sid: str, path: str = "", set: str = ""):
        need(sid, "sounds")
        root = root_of(sid, set).resolve()
        f = (root / path).resolve() if path else root
        if f != root and root not in f.parents:
            raise HTTPException(404, "Introuvable")
        reveal(f)
        return {"ok": True}

    @app.post("/api/{sid}/sounds/relist")
    def sound_relist(sid: str):
        """Rebuild the list of the game's sounds (names, banks): after an update of the game or of omni."""
        src = need(sid, "sounds")
        job = jobs.create("maintenance", "Liste des sons", sid, cancellable=False)

        def run(job):
            refs = src.list_sounds(progress=lambda m: jobs.log(job, m), fresh=True)
            return {"references": len(refs)}
        jobs.run(job, run)
        return {"job": job["id"]}
