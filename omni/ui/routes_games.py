"""Game library API: what is added, what Steam offers, add by folder, remove."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import games
from ..core.windows import pick_folder


class AddGame(BaseModel):
    path: str


def register(app: FastAPI, *, source_ids, jobs=None, reset_runtime=None) -> None:
    def start_prepare(game_id: str) -> str | None:
        """Prepare a game in the background (heavy job queue): omni needs nothing else than its folder."""
        if jobs is None:
            return None
        g = games.get(game_id)
        if g is None or not g.get("supported") or game_id == "007fl":
            return None
        job = jobs.create("setup", f"Préparation : {g['title']}", game_id, 4, dedupe=f"prepare:{game_id}")

        def run(job):
            res = games.prepare(game_id, lambda m: jobs.log(job, m), lambda d, t: jobs.count(job, d, t),
                                jobs.cancel_event(job))
            if reset_runtime is not None:
                reset_runtime(purge=False)
            return res
        jobs.run(job, run)
        return job["id"]
    app.state.prepare_game = start_prepare

    @app.get("/api/games")
    def list_games():
        """The library, each game with its preparation status (``ready``: it can be opened now)."""
        known = set(source_ids())
        out = []
        for g in games.load():
            st = games.status(g["id"])
            out.append({**g, "known": g["id"] in known, "ready": g["id"] in known and st["ready"], "status": st})
        return {"games": out, "candidates": games.scan_steam()}

    @app.get("/api/games/{game_id}/status")
    def game_status(game_id: str):
        return games.status(game_id)

    @app.post("/api/games/{game_id}/prepare")
    def prepare_game(game_id: str):
        if games.get(game_id) is None:
            raise HTTPException(404, "Jeu inconnu")
        jid = start_prepare(game_id)
        if jid is None:
            raise HTTPException(400, "Ce jeu ne se prépare pas ici")
        return {"job": jid}

    @app.post("/api/games")
    def add_game(req: AddGame):
        try:
            g = games.add(req.path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {**g, "job": start_prepare(g["id"])}

    @app.post("/api/games/pick")
    def pick_and_add():
        """The native folder dialog, then add what the chosen folder holds."""
        try:
            path = pick_folder("Dossier du jeu")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(501, f"Sélecteur de dossier indisponible : {e}")
        if not path:
            return {"cancelled": True}
        try:
            g = games.add(path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {**g, "job": start_prepare(g["id"])}

    @app.delete("/api/games/{game_id}")
    def remove_game(game_id: str):
        if not games.remove(game_id):
            raise HTTPException(404, "Jeu inconnu")
        return {"ok": True}
