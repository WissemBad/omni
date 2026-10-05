"""Textures workbench API: every texture of the game, its preview (any channel, any size), who uses it, export."""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from ..core.config import CONFIG
from ..core.windows import reveal

CHANNELS = ("rgb", "rgba", "r", "g", "b", "a")


def register(app: FastAPI, *, jobs, need, texcat_of, catalog_of, converted) -> None:
    def cat(sid: str):
        need(sid, "textures")
        c = texcat_of(sid)
        if c is None:
            raise HTTPException(404, "no texture catalog")
        return c

    @app.get("/api/{sid}/textures/status")
    def tex_status(sid: str):
        c = cat(sid)
        return {"ready": c.ready(), "building": c.building, "progress": c.progress, "error": c.error,
                **(c.stats() if c.ready() else {})}

    @app.post("/api/{sid}/textures/rebuild")
    def tex_rebuild(sid: str):
        c = cat(sid)
        job = jobs.create("textures", "Index des textures", sid)

        def run(job):
            c.build()
            if c.error:
                raise RuntimeError(c.error)
            return c.stats()
        jobs.run(job, run)
        return {"job": job["id"]}

    @app.get("/api/{sid}/textures/categories")
    def tex_categories(sid: str):
        c = cat(sid)
        return c.categories() if c.ready() else []

    @app.get("/api/{sid}/textures")
    def tex_list(sid: str, q: str = "", folder: str = "", fmt: str = "", role: str = "", usage: str = "",
                 min_size: int = 0, sort: str = "name", limit: int = 200, offset: int = 0):
        c = cat(sid)
        if not c.ready():
            c.build_async()
            return {"total": 0, "items": [], "facets": {"fmt": [], "role": []}, "building": True,
                    "progress": c.progress}
        total, items = c.search(q, folder, fmt, role, usage, min_size, sort, min(limit, 500), offset)
        return {"total": total, "items": items, "facets": c.facets(q, folder, usage), "building": False}

    @app.get("/api/{sid}/textures/{key}")
    def tex_detail(sid: str, key: str):
        c = cat(sid)
        row = c.get(key)
        if row is None:
            raise HTTPException(404, "unknown texture")
        src = need(sid, "textures")
        users = c.users(key)
        # models: the props catalog names them, the addon says whether they are converted
        models = []
        if "props" in src.capabilities and users["models"]:
            from ..targets.source.build import _model_path
            db = catalog_of(sid).db
            conv = converted(sid)
            marks = ",".join("?" * len(users["models"]))
            for r in db.execute(f"SELECT key, rel, cat FROM assets WHERE key IN ({marks})", users["models"]):
                models.append({"key": r["key"], "rel": r["rel"], "cat": r["cat"],
                               "converted": _model_path(sid, r["rel"], r["key"]) in conv})
        models.sort(key=lambda m: m["rel"])
        game = src.names.name(int(key, 16)) if hasattr(src, "names") else ""
        return {**row, "game_path": game, "materials": users["materials"], "models": models,
                "model_count": len(users["models"])}

    @app.get("/api/{sid}/textures/{key}/image")
    def tex_image(sid: str, key: str, channel: str = "rgb", size: int = 512, normal: int = -1):
        """PNG of a game texture; ``normal`` -1 = decide from its role (BC5 normals get their blue rebuilt)."""
        src = need(sid, "textures")
        if channel not in CHANNELS:
            raise HTTPException(400, "bad channel")
        if not re.fullmatch(r"[0-9A-Fa-f]{16}", key):
            raise HTTPException(400, "bad key")
        size = max(16, min(size, 8192))
        if normal < 0:
            row = texcat_of(sid).get(key) if texcat_of(sid) is not None else None
            normal = int(bool(row and row["role"] in ("normal", "detail_normal")))
        out = CONFIG.workspace / "preview" / sid / "srctex" / f"{key.upper()}_{channel}_{size}_{normal}.png"
        if not out.exists():
            try:
                png = src.texture_png(key.upper(), channel, size, bool(normal))
            except FileNotFoundError:
                raise HTTPException(404, "texture not found")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(415, f"{type(e).__name__}: {e}")
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(png)
            tmp.replace(out)
        return FileResponse(out, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    # compatibility with the previous props route
    @app.get("/api/{sid}/props/{key}/texture/{tkey}")
    def prop_texture(sid: str, key: str, tkey: str, channel: str = "rgb", size: int = 512, normal: int = -1):
        return tex_image(sid, tkey, channel, size, normal)

    @app.get("/api/{sid}/textures/{key}/download")
    def tex_download(sid: str, key: str, channel: str = "rgba"):
        """Full-resolution PNG, as a file to save."""
        src = need(sid, "textures")
        row = texcat_of(sid).get(key) if texcat_of(sid) is not None else None
        png = src.texture_png(key.upper(), channel, 16384, bool(row and row["role"] in ("normal", "detail_normal")))
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (row["name"] if row else key)).strip("_") or key
        return Response(png, media_type="image/png",
                        headers={"Content-Disposition": f'attachment; filename="{name}_{key[-6:].lower()}.png"'})

    @app.post("/api/{sid}/textures/{key}/export")
    def tex_export(sid: str, key: str):
        """Write the full-resolution PNG to workspace/exports/textures and show it in the Explorer."""
        src = need(sid, "textures")
        row = texcat_of(sid).get(key) if texcat_of(sid) is not None else None
        png = src.texture_png(key.upper(), "rgba", 16384, bool(row and row["role"] in ("normal", "detail_normal")))
        folder = CONFIG.workspace / "exports" / sid / "textures" / ((row["folder"] if row else "") or "misc")
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (row["name"] if row else key)).strip("_") or key
        f = folder / f"{name}_{key[-6:].lower()}.png"
        f.write_bytes(png)
        reveal(f)
        return {"path": str(f)}

    @app.get("/api/{sid}/models/{key}/textures")
    def model_textures(sid: str, key: str):
        """Every game texture a mesh uses (through its materials), from the catalog."""
        c = cat(sid)
        return c.textures_of_model(key) if c.ready() else []
