"""Game library API: what is added, what Steam offers, add by folder, remove."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import games
from ..core.windows import pick_folder


class AddGame(BaseModel):
    path: str


def register(app: FastAPI, *, source_ids) -> None:
    @app.get("/api/games")
    def list_games():
        """The library, each game flagged with whether a source can open it now."""
        known = set(source_ids())
        out = []
        for g in games.load():
            out.append({**g, "ready": g["id"] in known})
        return {"games": out, "candidates": games.scan_steam()}

    @app.post("/api/games")
    def add_game(req: AddGame):
        try:
            return games.add(req.path)
        except ValueError as e:
            raise HTTPException(400, str(e))

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
            return games.add(path)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/games/{game_id}")
    def remove_game(game_id: str):
        if not games.remove(game_id):
            raise HTTPException(404, "Jeu inconnu")
        return {"ok": True}
