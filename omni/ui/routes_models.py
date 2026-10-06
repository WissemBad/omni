"""Models workbench API: props (the source's meshes) and characters (outfit families -> playermodels).

Both answer the same inspector shape (``/details``): figures, materials with the game's own textures (keys usable
with the texture routes), variants, collision and where the converted model is, so the front shows one inspector
for props, characters and the converted output.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import settings
from ..core.config import CONFIG, Config

PM_WORKERS = 8          # parallel playermodel builds (each holds a whole family: ~1 GB at peak)


class PropFilter(BaseModel):
    """The catalog filter of the props list: convert "everything that matches" without shipping 50,000 keys."""
    q: str = ""
    cat: str = ""
    skinned: int | None = None
    named_only: bool = True


class ConvertRequest(BaseModel):
    keys: list[str] = []
    filter: PropFilter | None = None      # used when ``keys`` is empty
    physics: bool | None = None
    collision: str | None = None          # game | parts | hull | coacd (see targets/source/build.py)
    tex_quality: str | None = None        # max | high | balanced | light
    lossless_normals: bool | None = None
    blend: bool | None = None
    gltf: bool | None = None              # also write a .glb per model (Blender, any glTF tool)
    workers: int | None = None


class CharacterBatch(BaseModel):
    ids: list[str] | None = None          # families to build (default: every family)
    skip_built: bool = True
    tex_quality: str | None = None


class CharacterBuild(BaseModel):
    title: str = ""
    variants: list[int] | None = None     # variation numbers to include; the first one is the default look
    tex_quality: str | None = None
    max_tris: int | None = None


@dataclass
class PreviewConfig(Config):
    """Character previews convert their materials into a scratch addon, never into the real one."""

    def addon_dir(self, source_id: str) -> Path:
        return self.workspace / "preview" / source_id / "pm_addon"


def _tex_info(src, key: str, texcat) -> dict:
    """Header-level facts of a game texture (no decoding), from the texture catalog when it is built."""
    row = texcat.get(key) if texcat is not None else None
    if row:
        return {"width": row["width"], "height": row["height"], "format": row["fmt"], "found": True,
                "name": row["name"], "bytes": row["bytes"]}
    try:
        p = src.archive.find("TEXT", int(key, 16))
        if p is None:
            return {"found": False}
        from ..sources.glacier.texture import parse_text_header
        with open(p, "rb") as f:
            hd = parse_text_header(f.read(0x98))
        return {"width": hd.width, "height": hd.height, "format": hd.name, "found": True, "name": "", "bytes": 0}
    except Exception:  # noqa: BLE001 - another source without an archive: the catalog is the reference
        return {"found": False}


def material_entry(src, m, texcat=None) -> dict:
    """A source material as the inspector shows it."""
    cls = next((f[6:] for f in m.flags if f.startswith("class:")), "")
    textures = []
    for t in m.textures:
        textures.append({"role": t.role, "slot": t.slot, "key": t.key, **_tex_info(src, t.key, texcat)})
    params = {k: [round(float(x), 4) for x in v] for k, v in list(m.params.items())[:80]}
    return {"key": m.key, "name": m.name, "source_name": m.source_name, "class": cls,
            "flags": sorted(f for f in m.flags if not f.startswith("class:")), "params": params,
            "textures": textures, "unknown_slots": m.unknown_slots}


def registry_source(sid: str):
    try:
        from ..sources import registry
        return registry.get_source(sid)
    except Exception:  # noqa: BLE001
        return None


def register(app: FastAPI, *, jobs, need, catalog_of, texcat_of, converted, conv_reset) -> None:
    # ------------------------------------------------------------------------------------------- props
    @app.get("/api/{sid}/props/categories")
    def categories(sid: str):
        need(sid, "props")
        return catalog_of(sid).categories()

    def _flt(statues: str, named: int):
        return {"all": None, "no": 0, "only": 1}.get(statues), bool(named)

    @app.get("/api/{sid}/props")
    def props(sid: str, q: str = "", cat: str = "", statues: str = "all", named: int = 1, limit: int = 100,
              offset: int = 0, done: str = ""):
        need(sid, "props")
        c, (sk, nm) = catalog_of(sid), _flt(statues, named)
        from ..targets.source.build import _model_path
        conv = converted(sid)
        if done:                                    # converted / not converted: filtered in memory (catalog is 32k)
            rows = c.search(q, cat, sk, nm, 100000, 0)
            for it in rows:
                it["converted"] = _model_path(sid, it["rel"], it["key"]) in conv
            rows = [r for r in rows if r["converted"] == (done == "yes")]
            total, items = len(rows), rows[offset:offset + min(limit, 500)]
        else:
            items = c.search(q, cat, sk, nm, min(limit, 500), offset)
            total = c.total(q, cat, sk, nm)
            for it in items:
                it["converted"] = _model_path(sid, it["rel"], it["key"]) in conv
        for it in items:
            it.pop("name", None)
        return {"total": total, "items": items}

    @app.get("/api/{sid}/props/keys")
    def prop_keys(sid: str, q: str = "", cat: str = "", statues: str = "all", named: int = 1, limit: int = 50000):
        need(sid, "props")
        sk, nm = _flt(statues, named)
        return catalog_of(sid).keys(q, cat, sk, nm, min(limit, 50000))

    @app.get("/api/{sid}/props/{key}/glb")
    def prop_glb(sid: str, key: str):
        src = need(sid, "props")
        key = key.upper()
        out = CONFIG.workspace / "preview" / f"{key}.glb"
        if not out.exists():
            from ..preview.glb import export_glb
            try:
                model, mats = src.load_model(int(key, 16))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(404, str(e))
            export_glb(src, model, mats, out, tex_size=settings.get("viewer", "texture_size"))
        return FileResponse(out, media_type="model/gltf-binary")

    @app.get("/api/{sid}/props/{key}/details")
    def prop_details(sid: str, key: str):
        """Everything the inspector shows about a game mesh, before conversion."""
        src = need(sid, "props")
        key = key.upper()
        try:
            model, mats = src.load_model(int(key, 16))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(404, str(e))
        tc = texcat_of(sid)
        row = next(iter(catalog_of(sid)._query("SELECT * FROM assets WHERE key = ?", (key,))), None)
        tris = sum(len(sm.indices) // 3 for sm in model.submeshes)
        verts = sum(len(sm.positions) for sm in model.submeshes)
        import numpy as np
        ext = None
        if model.submeshes:
            pts = np.concatenate([sm.positions for sm in model.submeshes])
            ext = (pts.max(0) - pts.min(0)).round(4).tolist()
        coll = None
        try:
            c = src.load_collision(int(key, 16))
            if c:
                kinds: dict[str, int] = {}
                for s in c["shapes"]:
                    kinds[s["type"]] = kinds.get(s["type"], 0) + 1
                coll = {"resource": c.get("resource"), "dynamic": c.get("dynamic"), "shapes": kinds}
        except Exception:  # noqa: BLE001
            pass
        try:
            variants = src.prop_variants(int(key, 16))
        except Exception:  # noqa: BLE001
            variants = []
        from ..targets.source.build import _model_path
        mpath = _model_path(sid, row["rel"], key) if row else ""
        mdl = CONFIG.addon_dir(sid) / "models" / f"{mpath}.mdl"
        return {
            "key": key, "rel": row["rel"] if row else model.name, "cat": row["cat"] if row else "",
            "name": row["name"] if row else "", "size": row["size"] if row else 0,
            "kind": "prop", "skinned": model.skinned, "lod": model.lod, "lods": model.lods,
            "submeshes": len(model.submeshes), "triangles": tris, "vertices": verts, "extent": ext,
            "bones": len(model.rig.names) if getattr(model, "rig", None) is not None and hasattr(model.rig, "names") else 0,
            "warnings": model.warnings[:20],
            "materials": [material_entry(src, m, tc) for m in mats.values()],
            "variants": [{"index": i, "slots": len(v),
                          "params": sum(len(x[1]) for x in v.values() if isinstance(x, (tuple, list)) and len(x) > 1)}
                         for i, v in enumerate(variants)][:64],
            "collision": coll,
            "output": {"path": f"models/{mpath}.mdl" if mpath else "", "converted": mdl.exists(),
                       "mtime": mdl.stat().st_mtime if mdl.exists() else 0},
        }

    @app.get("/api/{sid}/props/{key}/textures")
    def prop_textures(sid: str, key: str):
        """Compatibility: the game textures of a prop, per material (now part of /details)."""
        return prop_details(sid, key)["materials"]

    def start_props(sid: str, body: dict) -> dict:
        req = ConvertRequest(**body)
        src = need(sid, "props")
        if not req.keys and req.filter is not None:           # resolved here, so a retry replays the exact list
            f = req.filter
            req.keys = catalog_of(sid).keys(f.q, f.cat, f.skinned, f.named_only, limit=200000)
        if not req.keys:
            raise HTTPException(400, "Aucun prop à convertir")
        body = {**req.model_dump(), "filter": None}
        st = settings.load()
        opts = dict(physics=req.physics if req.physics is not None else st["props"]["physics"],
                    collision=req.collision or st["props"]["collision"],
                    tex_quality=req.tex_quality or st["textures"]["quality"],
                    lossless_normals=req.lossless_normals if req.lossless_normals is not None else st["textures"]["lossless_normals"])
        from ..pipeline import clamp_workers
        workers = clamp_workers(req.workers or st["props"]["workers"])
        blend = req.blend if req.blend is not None else st["props"]["blend"]
        gltf = req.gltf if req.gltf is not None else st["props"]["gltf"]
        job = jobs.create("props", f"{len(req.keys)} prop(s)", sid, len(req.keys),
                          request={"op": "props", "sid": sid, "body": body, "field": "keys"})

        def run(job):
            from ..pipeline import run_batch
            st = jobs.stager(job, 1 + bool(blend) + bool(gltf))
            st("Conversion pour GMod")

            def on_result(r):
                jobs.result(job, {"key": r["key"], "status": r["status"], "model": r.get("model", ""),
                                  "errors": r.get("errors", []), "notes": r.get("notes", [])[:3],
                                  "seconds": r.get("seconds", {}).get("total", 0)})
            stats = run_batch(sid, req.keys, workers, opts["physics"], on_result=on_result, collision=opts["collision"],
                              lossless_normals=opts["lossless_normals"], tex_quality=opts["tex_quality"],
                              cancel=jobs.cancel_event(job), on_plan=lambda sizes, w: jobs.plan(job, sizes, w))
            if blend:
                from ..targets.blender.export import export_blends
                count = st("Préparation des .blend", len(req.keys))
                stats["blends"] = len(export_blends(src, req.keys, progress=count,
                                                    on_blender=lambda: st("Blender : écriture des .blend")))
            if gltf:
                from ..targets.gltf.export import export_models
                count = st("Export glTF (.glb)", len(req.keys))
                g = export_models(src, req.keys, progress=count, cancel=jobs.cancel_event(job))
                stats["gltf"], stats["gltf_folder"] = g["written"], g["folder"]
                for k, e in g["failed"][:20]:
                    jobs.log(job, f"glTF {k}: {e}")
            conv_reset(sid)
            return stats
        jobs.run(job, run)
        return {"job": job["id"]}
    jobs.starters["props"] = start_props

    @app.post("/api/{sid}/models/gltf")
    def models_gltf(sid: str, req: ConvertRequest):
        """Export models (props, statues, characters' meshes) to glTF .glb only, without the GMod build."""
        src = need(sid, "props")
        if not req.keys and req.filter is not None:
            f = req.filter
            req.keys = catalog_of(sid).keys(f.q, f.cat, f.skinned, f.named_only, limit=200000)
        if not req.keys:
            raise HTTPException(400, "Aucun modèle à exporter")
        job = jobs.create("maintenance", f"glTF : {len(req.keys)} modèle(s)", sid, len(req.keys))

        def run(job):
            from ..targets.gltf.export import export_models
            count = jobs.stager(job, 1)("Export glTF (.glb)", len(req.keys))
            g = export_models(src, req.keys, progress=lambda d, t: (jobs.count(job, d, t), count(d, t)),
                              cancel=jobs.cancel_event(job))
            for k, e in g["failed"][:50]:
                jobs.log(job, f"{k}: {e}")
            return {"écrits": g["written"], "échecs": len(g["failed"]), "dossier": g["folder"]}
        jobs.run(job, run)
        return {"job": job["id"]}

    @app.post("/api/{sid}/props/convert")
    def prop_convert(sid: str, req: ConvertRequest):
        """Queue a conversion of ``keys`` (or of everything matching ``filter``); one heavy job runs at a time."""
        return start_props(sid, req.model_dump())

    # -------------------------------------------------------------------------------------- characters
    @app.get("/api/{sid}/characters")
    def characters(sid: str, q: str = "", mission: str = "", role: str = "", body: str = "", kind: str = "",
                   built: str = "", limit: int = 400, offset: int = 0):
        src = need(sid, "characters")
        words = q.lower().split()
        every = src.characters()
        done = _built(sid)

        def keep(c, skip=""):
            hay = f"{c['id']} {c['title']} {c['mission']} {c['role']}".lower()
            return (all(w in hay for w in words)
                    and (skip == "mission" or not mission or c["mission"] == mission)
                    and (skip == "role" or not role or c["role"] == role)
                    and (skip == "body" or not body or c["body"] == body)
                    and (skip == "kind" or not kind or c["kind"] == kind)
                    and (not built or (c["id"] in done) == (built == "yes")))

        def facet(field, skip):
            n: dict[str, int] = {}
            for c in every:
                if keep(c, skip):
                    n[c[field]] = n.get(c[field], 0) + 1
            return sorted(({"value": k, "n": v} for k, v in n.items()), key=lambda x: (-x["n"], x["value"]))
        rows = [c for c in every if keep(c)]
        items = [{k: v for k, v in c.items() if k != "variants"} | {"count": len(c["variants"]), "built": c["id"] in done}
                 for c in rows[offset:offset + min(limit, 2000)]]
        return {"total": len(rows), "items": items,
                "facets": {"mission": facet("mission", "mission"), "role": facet("role", "role"),
                           "body": facet("body", "body"), "kind": facet("kind", "kind")}}

    def _built(sid: str) -> set[str]:
        """Character ids whose playermodel exists in the addon (pm_registry.json + the .mdl)."""
        reg = CONFIG.addon_dir(sid) / "pm_registry.json"
        try:
            rows = json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {}
        except (OSError, ValueError):
            rows = {}
        out = set()
        stems = set()
        for path in rows:
            stem = path.rsplit("/", 1)[-1].removesuffix(".mdl")
            if (CONFIG.addon_dir(sid) / path).exists():
                out.add(f"outfit_{stem}")
                stems.add(stem)
        src = registry_source(sid)
        if src is not None and hasattr(src, "build_character"):
            from ..core.naming import slug
            for c in src.characters():
                if slug(c["variants"][0]["name"], 40) in stems:
                    out.add(c["id"])
        return out

    def _character(sid: str, cid: str):
        src = need(sid, "characters")
        c = src.character(cid)
        if c is None:
            raise HTTPException(404, f"Personnage inconnu : {cid}")
        return src, c

    def _preview_dir(sid: str) -> Path:
        return CONFIG.workspace / "preview" / sid / "characters"

    building: dict[str, dict] = {}
    build_lock = threading.Semaphore(2)              # at most two previews prepared at once

    def _build_preview(src, sid: str, cid: str, c: dict):
        from ..pipeline import apply_quality
        from ..targets.source.pm_build import PMOptions
        from ..targets.source.pm_outfit import export_preview, prepare
        state = building[f"{sid}/{cid}"]
        try:
            with build_lock:
                state["stage"] = "matériaux et pose du squelette"
                o = PMOptions()
                apply_quality(o.mat, "light")            # the preview shows 512 px textures: no need for more
                cfg = PreviewConfig(workspace=CONFIG.workspace)
                out = _preview_dir(sid) / f"{cid}.glb"
                out.parent.mkdir(parents=True, exist_ok=True)
                if hasattr(src, "preview_character"):           # sources with their own character builder
                    state["stage"] = "pose du squelette et aperçu 3D"
                    src.preview_character(cid, out, o, cfg, settings.get("characters", "preview_size"))
                else:
                    plan = prepare(src, [int(v["key"], 16) for v in c["variants"]], o, cfg, cid.replace("outfit_", ""))
                    state["stage"] = "aperçu 3D"
                    export_preview(plan, out, tex_size=settings.get("characters", "preview_size"))
            state["status"] = "ready"
        except Exception as e:  # noqa: BLE001
            state.update(status="error", error=f"{type(e).__name__}: {e}")

    @app.get("/api/{sid}/characters/{cid}/preview")
    def character_preview(sid: str, cid: str, refresh: int = 0):
        """Bodygroups, skins, presets and materials of a character (+ its GLB, see ``/glb``). Preparing a family
        converts its materials (light quality, in a scratch addon) and poses its meshes, which takes seconds to a
        minute: the first call starts the work in the background and answers ``{"status": "building"}``; the
        client polls until ``ready``."""
        src, c = _character(sid, cid)
        meta = _preview_dir(sid) / f"{cid}.json"
        key = f"{sid}/{cid}"
        st = building.get(key)
        if st and st["status"] == "error":
            building.pop(key)
            raise HTTPException(500, st["error"])
        if st and st["status"] == "building":
            return {"status": "building", "elapsed": round(time.time() - st["started"], 1), "stage": st.get("stage", "")}
        if refresh or not meta.exists():
            building[key] = {"status": "building", "started": time.time(), "stage": "en attente"}
            threading.Thread(target=_build_preview, args=(src, sid, cid, c), daemon=True).start()
            return {"status": "building", "elapsed": 0, "stage": "en attente"}
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["status"] = "ready"
        data["built"] = bool(data.get("model")) and (CONFIG.addon_dir(sid) / data["model"]).exists()
        data["variants"] = [{"v": v["v"], "name": v["name"], "key": v["key"]} for v in c["variants"]]
        tc = texcat_of(sid)
        for m in data.get("materials", []):
            for t in m.get("textures", []):
                t.update({k: v for k, v in _tex_info(src, t["key"], tc).items() if k not in t})
        return data

    @app.get("/api/{sid}/characters/{cid}/glb")
    def character_glb(sid: str, cid: str):
        _character(sid, cid)
        out = _preview_dir(sid) / f"{cid}.glb"
        if not out.exists():
            raise HTTPException(404, "Aperçu pas encore construit")
        return FileResponse(out, media_type="model/gltf-binary")

    @app.post("/api/{sid}/characters/{cid}/build")
    def character_build(sid: str, cid: str, req: CharacterBuild):
        src, c = _character(sid, cid)
        by_v = {v["v"]: v for v in c["variants"]}
        order = req.variants if req.variants else [v["v"] for v in c["variants"]]
        missing = [v for v in order if v not in by_v]
        if missing:
            raise HTTPException(400, f"Variation(s) inconnue(s) : {missing}")
        st = settings.load()
        job = jobs.create("character", req.title or c["title"], sid, 1)

        def run(job):
            from ..pipeline import apply_quality
            from ..targets.source.pm_build import PMOptions
            from ..targets.source.pm_outfit import build_outfit_pm
            settings.apply(st)
            o = PMOptions(max_tris=req.max_tris or st["characters"]["max_tris"])
            apply_quality(o.mat, req.tex_quality or st["textures"]["quality"])
            o.mat.lossless_normals = st["textures"]["lossless_normals"]
            if hasattr(src, "build_character"):
                r = src.build_character(cid, o, CONFIG, req.title)
            else:
                r = build_outfit_pm(src, [int(by_v[v]["key"], 16) for v in order], o, CONFIG,
                                    cid.replace("outfit_", ""), req.title)
            jobs.result(job, {"key": cid, "status": r.status, "model": r.model, "errors": r.errors[:2],
                              "notes": r.notes, "seconds": r.seconds})
            if r.status != "OK":
                raise RuntimeError(r.errors[0] if r.errors else "build failed")
            conv_reset(sid)
            return asdict(r)
        jobs.run(job, run)
        return {"job": job["id"]}

    def start_characters(sid: str, body: dict) -> dict:
        req = CharacterBatch(**body)
        src = need(sid, "characters")
        every = {c["id"]: c for c in src.characters()}
        ids_ = [i for i in (req.ids or list(every)) if i in every]
        if req.skip_built:
            done = _built(sid)
            ids_ = [i for i in ids_ if i not in done]
        if not ids_:
            raise HTTPException(409, "Tous les playermodels sont déjà construits")
        st = settings.load()
        job = jobs.create("character", f"{len(ids_)} playermodel(s)", sid, len(ids_),
                          request={"op": "characters", "sid": sid, "field": "ids",
                                   "body": {**req.model_dump(), "ids": ids_, "skip_built": False}})

        def run(job):
            from ..pipeline import clamp_workers, run_pm_batch
            settings.apply(st)
            n = {"ok": 0, "failed": 0}

            def on_result(r):
                c = every.get(r["key"], {})
                jobs.log(job, f"{c.get('title', r['key'])} ({len(c.get('variants', []))} variations)")
                jobs.result(job, {"key": r["key"], "status": r["status"], "model": r.get("model", ""),
                                  "errors": r.get("errors", [])[:1], "notes": r.get("notes", [])[:2],
                                  "seconds": r.get("seconds", 0)})
                n["ok" if r["status"] == "OK" else "failed"] += 1
            # families are independent (own materials folder, own sandbox model; the registry is locked):
            # built side by side like props. A playermodel uses more memory than a prop: at most PM_WORKERS.
            workers = min(clamp_workers(st["props"]["workers"]), PM_WORKERS)
            jobs.plan(job, {i: max(len(every[i].get("variants", [])), 1) for i in ids_}, min(workers, len(ids_)))
            run_pm_batch(sid, ids_, workers, {"max_tris": st["characters"]["max_tris"],
                                              "tex_quality": req.tex_quality or st["textures"]["quality"],
                                              "lossless_normals": st["textures"]["lossless_normals"]},
                         on_result=on_result, cancel=jobs.cancel_event(job))
            conv_reset(sid)
            return {"built": n["ok"], "failed": n["failed"], "remaining": len(ids_) - n["ok"] - n["failed"]}
        jobs.run(job, run)
        return {"job": job["id"]}
    jobs.starters["characters"] = start_characters

    @app.post("/api/{sid}/characters/build-all")
    def character_build_all(sid: str, req: CharacterBatch):
        """Every playermodel (or a list), queued behind the running job; built ones are skipped by default."""
        return start_characters(sid, req.model_dump())
