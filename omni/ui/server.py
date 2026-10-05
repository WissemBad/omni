"""Local web UI: search the catalog, preview in 3D, convert selection, deploy to GMod."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..core.catalog import Catalog
from ..core.config import CONFIG

STATIC = Path(__file__).with_name("index.html")


class ConvertRequest(BaseModel):
    keys: list[str]
    physics: bool = True
    blend: bool = False
    workers: int = 4


class PMRequest(BaseModel):
    title: str = ""
    variants: list[int] | None = None


def create_app(source) -> FastAPI:
    app = FastAPI(title="omni")
    cat = Catalog(source)
    jobs: dict[str, dict] = {}
    lock = threading.Lock()

    @app.on_event("startup")
    def _startup():
        if not cat.count():
            threading.Thread(target=cat.build, daemon=True).start()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return STATIC.read_text(encoding="utf-8")

    @app.get("/api/info")
    def info():
        from ..targets.source import deploy
        lp = deploy.link_path(CONFIG, source.id)
        return {"source": source.id, "title": source.title, "assets": cat.count(),
                "deployed": lp.exists(), "link": str(lp)}

    @app.get("/api/categories")
    def categories():
        return cat.categories()

    @app.get("/api/search")
    def search(q: str = "", cat_: str = "", skinned: int | None = None, named: int = 1, limit: int = 120, offset: int = 0):
        return cat.search(q, cat_, skinned, bool(named), min(limit, 500), offset)

    @app.get("/api/glb/{key}")
    def glb(key: str):
        key = key.upper()
        out = CONFIG.workspace / "preview" / f"{key}.glb"
        if not out.exists():
            from ..preview.glb import export_glb
            try:
                model, mats = source.load_model(int(key, 16))
            except Exception as e:
                raise HTTPException(404, str(e))
            export_glb(source, model, mats, out)
        return FileResponse(out, media_type="model/gltf-binary")

    def _run(job_id: str, req: ConvertRequest):
        from ..pipeline import run_batch
        job = jobs[job_id]

        def on_result(r):
            with lock:
                job["done"] += 1
                job["results"].append({"key": r["key"], "status": r["status"], "model": r.get("model", ""),
                                       "errors": r.get("errors", []), "notes": r.get("notes", [])[:3],
                                       "seconds": r.get("seconds", {}).get("total", 0)})
        try:
            job["stats"] = run_batch(source.id, req.keys, req.workers, req.physics, on_result=on_result)
            if req.blend:
                job["phase"] = "blend"
                from ..targets.blender.export import export_blends
                job["blends"] = [str(p) for p in export_blends(source, req.keys)]
            job["phase"] = "done"
        except Exception as e:  # noqa: BLE001
            job["phase"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"

    @app.post("/api/convert")
    def convert(req: ConvertRequest):
        jid = uuid.uuid4().hex[:8]
        jobs[jid] = {"id": jid, "total": len(req.keys), "done": 0, "results": [], "phase": "convert",
                     "started": time.time()}
        threading.Thread(target=_run, args=(jid, req), daemon=True).start()
        return {"job": jid}

    @app.get("/api/job/{jid}")
    def job(jid: str):
        if jid not in jobs:
            raise HTTPException(404)
        with lock:
            return jobs[jid]

    # ---- characters: game outfit families -> player models (bodygroups + skins) -------------------
    outfit_cache: dict = {}

    def families() -> dict:
        if "fam" not in outfit_cache:
            from collections import defaultdict
            from ..sources.glacier.outfit import OutfitResolver, body_type
            fam = defaultdict(list)
            for f, v, h, _n in OutfitResolver(source).outfits():
                fam[f].append({"v": v, "key": "%016X" % h})
            outfit_cache["fam"] = {f: {"family": f, "body": body_type(f), "variants": sorted(x, key=lambda y: y["v"])}
                                   for f, x in fam.items()}
        return outfit_cache["fam"]

    def _plan(family: str, variants: list[int] | None = None):
        from ..targets.source.pm_build import PMOptions
        from ..targets.source.pm_outfit import prepare
        fam = families().get(family)
        if fam is None:
            raise HTTPException(404, f"unknown outfit family {family}")
        keys = [int(x["key"], 16) for x in fam["variants"] if variants is None or x["v"] in variants]
        return prepare(source, keys, PMOptions(), CONFIG, family.replace("outfit_", ""))

    @app.get("/api/outfits")
    def outfits(q: str = "", limit: int = 300):
        words = q.lower().split()
        out = [{"family": f["family"], "body": f["body"], "count": len(f["variants"])}
               for f in families().values() if all(w in f["family"].lower() for w in words)]
        out.sort(key=lambda x: (-x["count"], x["family"]))
        return out[: min(limit, 2000)]

    @app.get("/api/outfit/{family}/preview")
    def outfit_preview(family: str, refresh: int = 0):
        import json
        from ..targets.source.pm_outfit import export_preview
        out = CONFIG.workspace / "preview" / "outfits" / f"{family}.glb"
        if refresh or not out.with_suffix(".json").exists():
            try:
                export_preview(_plan(family), out)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"{type(e).__name__}: {e}")
        return json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))

    @app.get("/api/outfit/{family}/glb")
    def outfit_glb(family: str):
        out = CONFIG.workspace / "preview" / "outfits" / f"{family}.glb"
        if not out.exists():
            raise HTTPException(404)
        return FileResponse(out, media_type="model/gltf-binary")

    def _run_pm(jid: str, family: str, req: PMRequest):
        from dataclasses import asdict
        from ..targets.source.pm_build import PMOptions
        from ..targets.source.pm_outfit import build_outfit_pm
        job = jobs[jid]
        try:
            fam = families()[family]
            keys = [int(x["key"], 16) for x in fam["variants"] if req.variants is None or x["v"] in req.variants]
            r = build_outfit_pm(source, keys, PMOptions(), CONFIG, family.replace("outfit_", ""), req.title)
            job["results"].append({"key": family, "status": r.status, "model": r.model, "errors": r.errors[:2],
                                   "notes": r.notes, "seconds": r.seconds})
            job["done"] = 1
            job["phase"] = "done"
        except Exception as e:  # noqa: BLE001
            job["phase"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"

    @app.post("/api/outfit/{family}/build")
    def outfit_build(family: str, req: PMRequest):
        if family not in families():
            raise HTTPException(404)
        jid = uuid.uuid4().hex[:8]
        jobs[jid] = {"id": jid, "total": 1, "done": 0, "results": [], "phase": "playermodel", "started": time.time()}
        threading.Thread(target=_run_pm, args=(jid, family, req), daemon=True).start()
        return {"job": jid}

    # ---- sounds: whole-game audio export (format chosen in the page) --------------------------------
    @app.post("/api/sounds")
    def sounds(format: str = "auto", match: str = ""):
        from ..targets.audio.export import export_sounds
        jid = uuid.uuid4().hex[:8]
        jobs[jid] = {"id": jid, "phase": "sounds", "log": [], "started": time.time()}

        def say(m):
            with lock:
                jobs[jid]["log"].append(m)

        def run():
            try:
                out = CONFIG.workspace / "audio" / source.id
                s = export_sounds(source.list_sounds(progress=say), out, format, None, match, 0, progress=say)
                say(f"-> {out}")
                with lock:
                    jobs[jid].update(phase="done", summary=s)
            except Exception as e:  # noqa: BLE001
                with lock:
                    jobs[jid].update(phase="error", error=f"{type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True).start()
        return {"job": jid}

    @app.post("/api/deploy")
    def deploy_(remove: bool = False):
        from ..targets.source import deploy
        try:
            return {"message": deploy.undeploy(source.id) if remove else deploy.deploy(source.id, source.title)}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    return app
