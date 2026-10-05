"""omni web API: everything the Nuxt front-end (``omni/web``) needs, for any number of sources.

Every source route lives under ``/api/<source id>/...`` and talks to the source through the contract of
``sources/base.py``, so a new game only implements that contract. ``/api/sources`` tells the front which
workbenches (props / characters / textures / sounds) a source offers.

Modules: routes_models (props, characters), routes_textures, routes_sounds, output (the model viewer), jobs (long
operations). This module wires them and adds the cross-cutting routes: system status, home overview, settings,
GMod link, maintenance. The built front-end (``web/.output/public``) is served at ``/``.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..core import settings
from ..core.catalog import Catalog
from ..core.config import CONFIG
from ..core.texcatalog import TextureCatalog
from ..sources import registry
from .jobs import Jobs

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / ".output" / "public"
from .. import __version__ as VERSION  # noqa: E402


def _dir_size(p: Path, budget: float = 3.0) -> int:
    """Bytes under ``p`` (os.scandir walk, stops after ``budget`` seconds: an estimate is enough for the UI)."""
    total, stack, t0 = 0, [p], time.perf_counter()
    while stack and time.perf_counter() - t0 < budget:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        try:
                            total += e.stat().st_size
                        except OSError:
                            pass
        except OSError:
            continue
    return total


def create_app(sources: list[str] | None = None) -> FastAPI:
    from . import security
    @asynccontextmanager
    async def lifespan(_app):
        settings.apply()
        warm()
        yield

    app = FastAPI(title="omni", version=VERSION, lifespan=lifespan)
    security.install(app)
    ids = sources or registry.source_ids()
    jobs = Jobs()
    catalogs: dict[str, Catalog] = {}
    texcats: dict[str, TextureCatalog] = {}
    sizes: dict[str, tuple[float, dict]] = {}

    def source_of(sid: str):
        try:
            return registry.get_source(sid)
        except KeyError:
            raise HTTPException(404, f"unknown source {sid}")

    def need(sid: str, capability: str):
        src = source_of(sid)
        if capability not in getattr(src, "capabilities", ()):
            raise HTTPException(404, f"{sid} has no {capability}")
        return src

    instances = threading.Lock()          # two first requests must not each open (and build) a catalog

    def catalog_of(sid: str) -> Catalog:
        with instances:
            if sid not in catalogs:
                cat = Catalog(source_of(sid))
                if not cat.count():
                    threading.Thread(target=cat.build, daemon=True, name=f"catalog-{sid}").start()
                catalogs[sid] = cat
            return catalogs[sid]

    def texcat_of(sid: str) -> TextureCatalog | None:
        src = source_of(sid)
        if "textures" not in src.capabilities:
            return None
        with instances:
            if sid not in texcats:
                texcats[sid] = TextureCatalog(src)
            return texcats[sid]

    conv_cache: dict[str, tuple[float, set]] = {}

    def converted(sid: str) -> set:
        """Model paths (``omni/<sid>/...`` without extension) present in the addon, refreshed every 20 s."""
        root = CONFIG.addon_dir(sid) / "models"
        now = time.time()
        if sid not in conv_cache or now - conv_cache[sid][0] > 20:
            conv_cache[sid] = (now, {p.relative_to(root).with_suffix("").as_posix() for p in root.rglob("*.mdl")}
                               if root.exists() else set())
        return conv_cache[sid][1]

    def conv_reset(sid: str) -> None:
        conv_cache.pop(sid, None)
        sizes.pop(sid, None)

    def reset_runtime(purge: bool = True) -> None:
        """After a setup step: sources, catalogs and caches are rebuilt from the new data."""
        registry.reset()
        for c in catalogs.values():
            try:
                c.db.close()
            except Exception:  # noqa: BLE001
                pass
        catalogs.clear()
        for t in texcats.values():
            try:
                t.db.close()
            except Exception:  # noqa: BLE001
                pass
        texcats.clear()
        conv_cache.clear()
        sizes.clear()
        if purge:                                   # everything derived from the old data
            for pat in ("catalog_*.sqlite", "textures_*.sqlite"):
                for f in CONFIG.workspace.glob(pat):
                    f.unlink(missing_ok=True)
            shutil.rmtree(CONFIG.cache / "archive", ignore_errors=True)
            for pat in ("characters_*.json", "sounds_*.pkl", "templates_*.sqlite", "property_names_*.json", "wwise_banks_*.json"):
                for f in CONFIG.cache.glob(pat):
                    f.unlink(missing_ok=True)
        warm()

    def warm():
        """Slow first requests (outfit index, catalogs) are computed in the background at launch."""
        def run():
            from ..core import setup
            if not (setup.assets_ok()["ok"] and setup.names_ok()["ok"]):
                return                                  # not set up yet: nothing to index
            for sid in ids:
                try:
                    src = registry.get_source(sid)
                    if "props" in src.capabilities:
                        catalog_of(sid)
                    if "characters" in src.capabilities:
                        src.characters()
                    tc = texcat_of(sid)
                    if tc is not None and not tc.ready():
                        tc.build()
                except Exception:  # noqa: BLE001  (surfaced by the routes themselves)
                    pass
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------------------------------- sources
    @app.get("/api/sources")
    def sources_():
        from ..targets.source import deploy
        out = []
        for sid in ids:
            cls = registry.source_class(sid)
            out.append({"id": sid, "title": cls.title, "description": getattr(cls, "description", ""),
                        "capabilities": list(getattr(cls, "capabilities", ())),
                        "deployed": deploy.link_path(CONFIG, sid).exists()})
        return out

    @app.get("/api/{sid}/info")
    def info(sid: str):
        from ..targets.source import deploy
        src = source_of(sid)
        lp = deploy.link_path(CONFIG, sid)
        cat = catalog_of(sid) if "props" in src.capabilities else None
        return {"id": sid, "title": src.title, "capabilities": list(src.capabilities),
                "assets": cat.count() if cat else 0, "deployed": lp.exists(), "link": str(lp)}

    @app.post("/api/{sid}/deploy")
    def deploy_(sid: str, remove: bool = False):
        from ..targets.source import deploy
        src = source_of(sid)
        try:
            return {"message": deploy.undeploy(sid) if remove else deploy.deploy(sid, src.title)}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    @app.post("/api/{sid}/gma")
    def gma(sid: str):
        source_of(sid)
        job = jobs.create("maintenance", "Paquet .gma", sid)

        def run(job):
            from ..targets.source import deploy
            p = deploy.build_gma(sid)
            return {"path": str(p), "bytes": p.stat().st_size}
        jobs.run(job, run)
        return {"job": job["id"]}

    # ---------------------------------------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def jobs_():
        return jobs.recent()

    @app.get("/api/jobs/{jid}")
    def job_(jid: str):
        return jobs.get(jid)

    @app.post("/api/jobs/{jid}/cancel")
    def job_cancel(jid: str):
        return jobs.cancel(jid)

    # ------------------------------------------------------------------------------------------ workbenches
    from . import output, routes_models, routes_setup, routes_sounds, routes_textures
    routes_setup.register(app, jobs=jobs, reset_runtime=reset_runtime)
    routes_models.register(app, jobs=jobs, need=need, catalog_of=catalog_of, texcat_of=texcat_of,
                           converted=converted, conv_reset=conv_reset)
    routes_textures.register(app, jobs=jobs, need=need, texcat_of=texcat_of, catalog_of=catalog_of,
                             converted=converted)
    routes_sounds.register(app, jobs=jobs, need=need)
    output.register(app, catalog_of=catalog_of, resolve_source=source_of, texcat_of=texcat_of)

    # -------------------------------------------------------------------------------------- system / home
    @app.get("/api/system")
    def system():
        from .. import native
        core = native.status()
        r = native.R
        gmod = CONFIG.gmod
        tools = [
            {"key": "studiomdl", "label": "StudioMDL (compilation des modèles)", "ok": CONFIG.studiomdl.exists(),
             "path": str(CONFIG.studiomdl)},
            {"key": "gmod", "label": "Garry’s Mod", "ok": (gmod / "garrysmod").is_dir(), "path": str(gmod)},
            {"key": "gmad", "label": "gmad (paquets .gma)", "ok": (gmod / "bin" / "gmad.exe").exists(),
             "path": str(gmod / "bin" / "gmad.exe")},
            {"key": "ffmpeg", "label": "ffmpeg (export MP3 seulement)", "ok": bool(shutil.which(CONFIG.ffmpeg)),
             "path": shutil.which(CONFIG.ffmpeg) or CONFIG.ffmpeg},
            {"key": "web", "label": "Interface construite", "ok": WEB_DIST.exists(), "path": str(WEB_DIST)},
        ]
        functions = ["convert_wem", "bank_links", "encode_vtf", "texture_png", "decode_vtf", "parse_collision",
                     "encode_dxt", "lbs", "smd_triangles", "template_index"]
        from ..core import setup
        ready = setup.status()
        return {"version": VERSION, "workspace": str(CONFIG.workspace), "cpus": os.cpu_count(),
                "setup": {"ready": ready["ready"], "can_convert": ready["can_convert"]},
                "rust": {**{k: v for k, v in core.items() if k != "native_functions"},
                         "backends": {f: r.backend(f) for f in functions}},
                "tools": tools}

    @app.get("/api/{sid}/overview")
    def overview(sid: str):
        """Figures of the home page: catalogs, what is converted, what is exported, the addon."""
        from ..targets.source import deploy
        src = source_of(sid)
        caps = src.capabilities
        out: dict = {"id": sid, "title": src.title, "description": getattr(src, "description", ""),
                     "capabilities": list(caps)}
        conv = converted(sid)
        if "props" in caps:
            c = catalog_of(sid)
            out["props"] = {"total": c.count(), "named": c.total(), "converted": sum(1 for p in conv if "/pm/" not in p)}
        if "characters" in caps:
            try:
                n = len(src.characters())
            except Exception:  # noqa: BLE001
                n = 0
            out["characters"] = {"total": n, "built": sum(1 for p in conv if "/pm/" in p)}
        tc = texcat_of(sid)
        if tc is not None:
            out["textures"] = {"ready": tc.ready(), "building": tc.building, **(tc.stats() if tc.ready() else {})}
        if "sounds" in caps:
            base = CONFIG.workspace / "audio" / sid
            sets = {}
            for f in ("ogg", "mp3", "flac", "wav", ""):
                sj = (base / f / "summary.json") if f else (base / "summary.json")
                if sj.exists():
                    try:
                        sets[f or "auto"] = json.loads(sj.read_text())
                    except (OSError, ValueError):
                        pass
            first = next(iter(sets.values()), None)
            out["sounds"] = {"exported": bool(sets), "sets": {k: {"count": v.get("written", 0), "bytes": v.get("bytes", 0)} for k, v in sets.items()},
                             "count": sum(v.get("written", 0) for v in sets.values()) if len(sets) == 1 else (first or {}).get("written", 0),
                             "bytes": sum(v.get("bytes", 0) for v in sets.values()), "summary": first}
        lp = deploy.link_path(CONFIG, sid)
        cached = sizes.get(sid)
        if not cached or time.time() - cached[0] > 120:
            def measure():
                sizes[sid] = (time.time(), {"addon": _dir_size(CONFIG.addon_dir(sid), 30),
                                            "previews": _dir_size(CONFIG.workspace / "preview" / sid, 10)})
            sizes.setdefault(sid, (time.time(), {"addon": None, "previews": None}))
            threading.Thread(target=measure, daemon=True).start()
        gma_file = CONFIG.workspace / "gma" / f"omni_{sid}.gma"
        out["addon"] = {"path": str(CONFIG.addon_dir(sid)), "deployed": lp.exists(), "link": str(lp),
                        "models": len(conv), **sizes[sid][1],
                        "gma": str(gma_file) if gma_file.exists() else "",
                        "gma_bytes": gma_file.stat().st_size if gma_file.exists() else 0}
        out["jobs"] = [j for j in jobs.recent(12) if j["source"] == sid][:6]
        return out

    # ------------------------------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def settings_get():
        return {"values": settings.load(), "defaults": settings.DEFAULTS}

    @app.put("/api/settings")
    def settings_put(body: dict):
        return {"values": settings.save(body), "defaults": settings.DEFAULTS}

    @app.post("/api/settings/reset")
    def settings_reset():
        return {"values": settings.reset(), "defaults": settings.DEFAULTS}

    # --------------------------------------------------------------------------------------- maintenance
    @app.get("/api/{sid}/storage")
    def storage(sid: str):
        source_of(sid)
        return {"addon": _dir_size(CONFIG.addon_dir(sid)), "previews": _dir_size(CONFIG.workspace / "preview"),
                "audio": _dir_size(CONFIG.workspace / "audio" / sid, 6), "cache": _dir_size(CONFIG.cache),
                "path": str(CONFIG.workspace)}

    @app.post("/api/{sid}/previews/clear")
    def clear_previews(sid: str):
        source_of(sid)
        shutil.rmtree(CONFIG.workspace / "preview" / sid, ignore_errors=True)
        for f in (CONFIG.workspace / "preview").glob("*.glb"):
            f.unlink(missing_ok=True)
        sizes.pop(sid, None)
        return {"ok": True}

    @app.post("/api/{sid}/catalog/rebuild")
    def catalog_rebuild(sid: str):
        need(sid, "props")
        job = jobs.create("maintenance", "Catalogue des props", sid)

        def run(job):
            n = catalog_of(sid).build(force=True)
            return {"assets": n}
        jobs.run(job, run)
        return {"job": job["id"]}

    @app.post("/api/native/build")
    def native_build():
        job = jobs.create("maintenance", "Cœur Rust (WebAssembly)", "")

        def run(job):
            from .. import native
            rc = native.build(release=True, wasm_only=True)
            if rc != 0:
                raise RuntimeError(f"cargo a échoué ({rc}) : Rust est-il installé ? (rustup.rs)")
            return {"wasm": str(native.WASM), "note": "redémarrer omni pour charger le nouveau cœur"}
        jobs.run(job, run)
        return {"job": job["id"]}

    @app.post("/api/shutdown")
    def shutdown(force: bool = False):
        """Stop the server (the window closes with it). Running jobs are cut short: asked for confirmation first."""
        running = jobs.running()
        if running and not force:
            raise HTTPException(409, f"{len(running)} travail(aux) en cours : {running[0]['label']}")
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"ok": True}

    @app.get("/api/diagnostic")
    def diagnostic():
        """Everything a bug report needs: versions, paths, set-up state, the tail of the log."""
        import platform
        import sys

        from .. import native
        from ..core import log as logs
        from ..core import setup as setup_mod
        info = [f"omni {VERSION}", f"python {sys.version.split()[0]} on {platform.platform()}",
                f"frozen: {bool(getattr(sys, 'frozen', False))}", f"workspace: {CONFIG.workspace}",
                f"assets: {CONFIG.assets_sorted}", f"gmod: {CONFIG.gmod}", f"studiomdl: {CONFIG.studiomdl}"]
        try:
            st = setup_mod.status()
            info.append(f"setup: ready={st['ready']} can_convert={st['can_convert']}")
        except Exception as e:  # noqa: BLE001
            info.append(f"setup: {type(e).__name__}: {e}")
        core = native.status()
        info.append(f"native: {core['native']} {core['native_version']} {core['native_error']} | wasm: {core['wasm']}")
        for j in jobs.recent(10):
            info.append(f"job {j['id']} {j['kind']} {j['phase']} {j.get('error', '')[:120]}")
        return {"text": "\n".join(info) + "\n\n--- log ---\n" + logs.tail(200)}

    # ---------------------------------------------------------------------------------------------- pages
    if WEB_DIST.exists():
        class SPA(StaticFiles):
            """Static files with a fallback to the SPA shell for client-side routes (deep links, reloads)."""
            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                if resp.status_code == 404 and not path.startswith(("_nuxt/", "api/")) and "." not in path.rsplit("/", 1)[-1]:
                    resp = await super().get_response("200.html", scope)
                # the shell must be re-read after an update; hashed assets never change
                if path.startswith("_nuxt/"):
                    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                else:
                    resp.headers["Cache-Control"] = "no-cache"
                return resp
        app.mount("/", SPA(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def root():
            return ("<meta charset=utf-8><body style='font:16px system-ui;padding:2rem'>"
                    "<h2>omni</h2><p>Interface non construite. <code>cd web &amp;&amp; bun install &amp;&amp; bun run build</code>"
                    " puis relancer omni.</p>")
    return app
