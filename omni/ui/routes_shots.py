"""Viewer screenshots: the share card drawn by the interface is stored in ``<workspace>/screenshots`` and put on the clipboard."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from ..core.config import CONFIG
from ..core.naming import slug
from ..core.windows import copy_image, reveal

PNG = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 40 << 20


def register(app: FastAPI) -> None:
    @app.post("/api/screenshots")
    async def save_screenshot(request: Request, name: str = "omni", copy: bool = True):
        data = await request.body()
        if not data.startswith(PNG) or len(data) > MAX_BYTES:
            raise HTTPException(400, "Image PNG attendue")
        folder = CONFIG.screenshots
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{slug(name, 60) or 'omni'}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path.write_bytes(data)
        return {"path": str(path), "name": path.name, "copied": copy_image(path) if copy else False}

    @app.post("/api/screenshots/reveal")
    def reveal_screenshots(path: str = ""):
        folder = CONFIG.screenshots.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        target = (folder / path).resolve() if path else folder
        if target != folder and folder not in target.parents:
            raise HTTPException(404, "Introuvable")
        reveal(target)
        return {"ok": True}

    @app.post("/api/screenshots/copy")
    def copy_screenshot(path: str):
        folder = CONFIG.screenshots.resolve()
        target = (folder / Path(path).name).resolve()
        if folder not in target.parents or not target.is_file():
            raise HTTPException(404, "Introuvable")
        return {"copied": copy_image(target)}
