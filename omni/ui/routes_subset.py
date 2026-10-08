"""Targeted extraction: the models named in a text file, converted into an addon of their own (see ``omni/subset.py``)."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import subset
from ..core.windows import pick_folder


class SubsetRequest(BaseModel):
    text: str = ""                 # the list, one model per line
    source: str = ""               # game tried first when a path names none
    out: str = ""                  # addon folder to write (run only)


def register(app: FastAPI, *, jobs, source_ids, source_of, catalog_of) -> None:
    def resolved(req: SubsetRequest) -> list[subset.Entry]:
        lines = subset.parse(req.text)
        if not lines:
            raise HTTPException(400, "Aucun modèle dans la liste (un chemin .mdl par ligne)")
        if len(lines) > 20000:
            raise HTTPException(400, "Liste trop longue (20 000 modèles au plus)")
        return subset.resolve(lines, ids=source_ids(), source_of=source_of, catalog_of=catalog_of, default=req.source)

    @app.post("/api/subset/resolve")
    def subset_resolve(req: SubsetRequest):
        """What each line of the list is: its game, prop or playermodel, or why it cannot be found."""
        entries = resolved(req)
        return {**subset.summary(entries), "entries": [asdict(e) for e in entries[:5000]]}

    @app.post("/api/subset/pick")
    def subset_pick():
        try:
            return {"path": pick_folder("Dossier de l’addon à créer")}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(501, f"Sélecteur de dossier indisponible : {e}")

    def start(sid: str, body: dict) -> dict:
        req = SubsetRequest(**body)
        if not req.out.strip():
            raise HTTPException(400, "Choisis le dossier de l’addon à créer")
        out = Path(req.out.strip())
        if out.exists() and not out.is_dir():
            raise HTTPException(400, "Ce chemin est un fichier, pas un dossier")
        entries = [e for e in resolved(req) if e.ok]
        if not entries:
            raise HTTPException(409, "Aucun modèle de la liste n’a été retrouvé dans les jeux")
        first = entries[0].source
        job = jobs.create("props", f"Sélection : {len(entries)} modèle(s)", first, len(entries),
                          request={"op": "subset", "sid": first, "body": body})

        def run(job):
            def on_result(r):
                jobs.result(job, {"key": r["key"], "status": r["status"], "model": r.get("model", ""),
                                  "errors": r.get("errors", []), "notes": r.get("notes", [])[:3],
                                  "seconds": (r.get("seconds") or {}).get("total", 0) if isinstance(r.get("seconds"), dict)
                                  else r.get("seconds", 0)})
            stats = subset.run(entries, out, titles={i: source_of(i).title for i in {e.source for e in entries}},
                               on_result=on_result, cancel=jobs.cancel_event(job), on_plan=lambda sizes, w: jobs.plan(job, sizes, w),
                               say=lambda m: jobs.log(job, m))
            return stats
        jobs.run(job, run)
        return {"job": job["id"], "folder": str(out)}
    jobs.starters["subset"] = start

    @app.post("/api/subset/run")
    def subset_run(req: SubsetRequest):
        return start(req.source, req.model_dump())
