"""Setup API: what is configured, detection, choosing folders, and the long steps as jobs (extraction, names, compiler)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..core import settings, setup
from ..core.config import CONFIG
from ..core.windows import pick_folder
from ..sources import registry


class Paths(BaseModel):
    game: str | None = None
    assets: str | None = None
    gmod: str | None = None
    studiomdl: str | None = None
    ffmpeg: str | None = None
    blender: str | None = None


def register(app: FastAPI, *, jobs, reset_runtime) -> None:
    @app.get("/api/setup/status")
    def status():
        return setup.status()

    @app.post("/api/setup/detect")
    def detect():
        """Game and Garry's Mod in the Steam libraries; what is found is saved as the configured location."""
        found = {}
        g = setup.detect_game()
        if g:
            found["game"] = str(g)
        gm = CONFIG.gmod
        if (gm / "garrysmod").is_dir():
            found["gmod"] = str(gm)
        if found and not CONFIG.game and "game" in found:
            settings.save({"paths": {"game": found["game"]}})
        return {"found": found, "status": setup.status()}

    @app.post("/api/setup/paths")
    def set_paths(body: Paths):
        patch = {k: v.strip().strip('"') for k, v in body.model_dump().items() if v is not None}
        for k, v in patch.items():
            if v and k in ("game", "assets", "gmod") and not Path(v).is_dir():
                raise HTTPException(400, f"Ce dossier n’existe pas : {v}")
            if v and k in ("studiomdl", "ffmpeg", "blender") and not Path(v).is_file():
                raise HTTPException(400, f"Ce fichier n’existe pas : {v}")
        if patch.get("game"):
            if not setup.game_packages(patch["game"]):
                raise HTTPException(400, "Aucun fichier chunk*.rpkg trouvé : choisis le dossier du jeu (il contient Runtime\\chunk0.rpkg).")
            patch["game"] = str(setup.game_packages(patch["game"])[0].parent)
        settings.save({"paths": patch})
        if "assets" in patch:
            reset_runtime()
        return setup.status()

    @app.post("/api/setup/pick")
    def pick(kind: str = "game"):
        titles = {"game": "Dossier du jeu (contient Runtime\\chunk0.rpkg)", "assets": "Dossier des ressources extraites (Sorted)",
                  "gmod": "Dossier de Garry’s Mod"}
        try:
            return {"path": pick_folder(titles.get(kind, "Choisir un dossier"))}
        except Exception as e:  # noqa: BLE001 - no display / dialog failed: the user types the path
            raise HTTPException(501, f"Sélecteur de dossier indisponible : {e}")

    @app.get("/api/setup/estimate")
    def estimate():
        game = CONFIG.game or setup.detect_game()
        found = setup.game_packages(game) if game else None
        if not found:
            raise HTTPException(404, "Jeu introuvable")
        try:
            return setup.estimate(found[1])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}")

    def _job(kind: str, label: str, fn, purge: bool = True):
        job = jobs.create("setup", label, "", 0, dedupe=f"setup:{kind}")

        def run(job):
            res = fn(job)
            if purge:                           # a download of tools changes nothing the catalogs were built from
                reset_runtime()
            return res if isinstance(res, dict) else {}
        jobs.run(job, run)
        return {"job": job["id"]}

    @app.post("/api/setup/extract")
    def extract():
        game = CONFIG.game or setup.detect_game()
        if not game:
            raise HTTPException(400, "Choisis d’abord le dossier du jeu")
        return _job("extract", "Extraction des ressources du jeu", lambda job: setup.extract_assets(
            Path(game), lambda m: jobs.log(job, m), lambda d, t: jobs.count(job, d, t), jobs.cancel_event(job)))

    @app.post("/api/setup/names")
    def names():
        return _job("names", "Liste des noms (Bond-Hashes)", lambda job: setup.fetch_names(
            lambda m: jobs.log(job, m), lambda d, t: jobs.count(job, d, t), jobs.cancel_event(job),
            release=registry.reset))

    @app.post("/api/setup/studiomdl")
    def studiomdl():
        return _job("studiomdl", "Compilateur de modèles (StudioMDL-CE)", lambda job: setup.fetch_studiomdl(
            lambda m: jobs.log(job, m), lambda d, t: jobs.count(job, d, t), jobs.cancel_event(job)), purge=False)
