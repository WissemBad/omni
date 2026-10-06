"""Model viewer API: browse and inspect compiled Source models (.mdl/.vvd/.vtx/.phy) with their .vmt/.vtf.

Every route lives under ``/api/<source id>/output/...`` and reads a *root* folder: by default the GMod addon omni writes
(``<exports>/<source>/garrysmod-addon``, with the conversion features: origin, comparison, reconversion), or any folder the
user adds (a decompiled addon...), which is only read. What the viewer shows is rebuilt from the compiled files, not
from the game's data, so it is what GMod will load.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from glob import escape as glob_escape
from fastapi.responses import FileResponse, Response

from ..core.config import CONFIG
from ..core.windows import pick_folder, reveal
from . import studio

TEXTURE_PARAMS = ("$basetexture", "$basetexture2", "$bumpmap", "$bumpmap2", "$normalmap", "$envmapmask", "$detail",
                  "$phongexponenttexture", "$selfillummask", "$blendmodulatetexture", "$lightwarptexture",
                  "$texture2", "$maskstexture", "$envmap")
PARAM_LABEL = {"$basetexture": "Couleur", "$basetexture2": "Couleur 2", "$bumpmap": "Normale", "$bumpmap2": "Normale 2",
               "$normalmap": "Normale", "$envmapmask": "Masque d’envmap", "$detail": "Détail",
               "$phongexponenttexture": "Exposant phong", "$selfillummask": "Masque d’émission",
               "$blendmodulatetexture": "Modulation de fondu", "$lightwarptexture": "Light warp",
               "$texture2": "Texture 2", "$maskstexture": "Masques"}


@dataclass
class Root:
    """A folder of models: the addon omni writes (``external`` False) or one the user added."""
    id: str
    path: Path
    label: str
    external: bool


ROOTS_FILE = CONFIG.config_dir / "viewer_roots.json"
VTX_EXTS = (".dx90.vtx", ".dx80.vtx", ".sw.vtx")


def _stored_roots() -> list[dict]:
    try:
        return json.loads(ROOTS_FILE.read_text(encoding="utf-8")) if ROOTS_FILE.exists() else []
    except (OSError, ValueError):
        return []


def _store_roots(rows: list[dict]) -> None:
    ROOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROOTS_FILE.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


def _root_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).lower().encode()).hexdigest()[:10]


def _find_vtx(stem: Path) -> Path | None:
    for ext in VTX_EXTS:
        f = Path(str(stem) + ext)
        if f.exists():
            return f
    return None


def _asset_base(root: Path, rel: str) -> Path:
    """Folder whose ``materials/`` serves a model: the closest parent that has one (a folder of several addons
    keeps each addon's materials next to its models), else the root itself."""
    d = (root / rel).parent
    while True:
        if (d / "materials").is_dir():
            return d
        if d == root or root not in d.parents:
            return root
        d = d.parent


def _within(root: Path, rel: str) -> Path:
    """``rel`` (posix, relative to the addon) as a real file path, refusing anything outside ``root``."""
    f = (root / rel).resolve()
    base = root.resolve()
    if base not in f.parents:
        raise HTTPException(404, "Introuvable")
    return f


# ------------------------------------------------------------------------------------------------- index
class OutputIndex:
    """Every compiled model of a root (a scan of ``models/``, or of the whole folder for an external root),
    refreshed in the background when stale."""

    def __init__(self, sid: str, root: Root):
        self.sid = sid
        self.root = root
        self.addon = root.path
        self.rows: list[dict] = []
        self.low: list[str] = []
        self.scanned = 0.0
        self.lock = threading.Lock()
        self.busy = False
        self.first = threading.Lock()
        self.materials: dict | None = None
        self.titles: dict[str, str] = {}

    def _scan(self) -> None:
        omni = not self.root.external
        top = self.addon / "models" if omni else self.addon
        prefix = f"models/{CONFIG.ns(self.sid)}/"
        rows: list[dict] = []
        stack = [(top, "models" if omni else "")]
        while stack and top.exists():
            d, rel = stack.pop()
            groups: dict[str, dict] = {}
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            if omni or e.name.lower() != "materials":
                                stack.append((Path(e.path), f"{rel}/{e.name}" if rel else e.name))
                            continue
                        stem = e.name.split(".", 1)[0]
                        st = e.stat()
                        g = groups.setdefault(stem, {"bytes": 0, "mtime": 0.0, "mdl": False})
                        g["bytes"] += st.st_size
                        if e.name.endswith(".mdl"):
                            g["mdl"], g["mtime"] = True, st.st_mtime
            except OSError:
                continue
            for stem, g in groups.items():
                if g["mdl"]:
                    path = f"{rel}/{stem}.mdl" if rel else f"{stem}.mdl"
                    inner = path[len(prefix):-4] if path.startswith(prefix) else path[7:-4] if path.startswith("models/") else path[:-4]
                    kind = ("character" if inner.startswith("pm/") else "prop") if omni else "model"
                    folder = inner.rsplit("/", 1)[0] if "/" in inner else ""
                    rows.append({"path": path, "name": stem, "folder": folder, "kind": kind,
                                 "mtime": g["mtime"], "bytes": g["bytes"]})
        registry = self.addon / "pm_registry.json"
        try:
            self.titles = json.loads(registry.read_text(encoding="utf-8")) if omni and registry.exists() else {}
        except (OSError, ValueError):
            self.titles = {}
        for r in rows:
            r["title"] = self.titles.get(r["path"], "")
        rows.sort(key=lambda r: -r["mtime"])
        low = [(r["path"] + " " + r["title"]).lower() for r in rows]
        with self.lock:
            self.rows, self.low, self.scanned = rows, low, time.time()

    def _background(self) -> None:
        try:
            self._scan()
        finally:
            self.busy = False

    def refresh(self, force: bool = False) -> None:
        if not self.scanned:
            with self.first:                              # first call: wait for it (once, even from parallel requests)
                if not self.scanned:
                    self._scan()
        elif (force or time.time() - self.scanned > 20) and not self.busy:
            self.busy = True
            threading.Thread(target=self._background, daemon=True).start()
        if self.materials is None and not getattr(self, "_mat_started", False):
            self._mat_started = True
            threading.Thread(target=self._scan_materials, daemon=True).start()

    def _scan_materials(self) -> None:
        n_vmt = n_vtf = size = 0
        stack = [self.addon / "materials"]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.name.endswith(".vmt"):
                            n_vmt += 1
                        elif e.name.endswith(".vtf"):
                            n_vtf += 1
                            size += e.stat().st_size
            except OSError:
                continue
        self.materials = {"materials": n_vmt, "textures": n_vtf, "texture_bytes": size}

    def query(self, q: str, kind: str, cat: str, sort: str):
        words = q.lower().split()
        pfx = cat + "/" if cat else ""
        with self.lock:
            rows, low = self.rows, self.low
        hits = [i for i, r in enumerate(rows)
                if (not kind or r["kind"] == kind)
                and (not cat or r["folder"] == cat or r["folder"].startswith(pfx))
                and all(w in low[i] for w in words)]
        if sort == "name":
            hits.sort(key=lambda i: rows[i]["path"])
        elif sort == "size":
            hits.sort(key=lambda i: -rows[i]["bytes"])
        return [rows[i] for i in hits]

    def categories(self) -> list[dict]:
        n: dict[str, int] = {}
        with self.lock:
            rows = self.rows
        for r in rows:
            parts = r["folder"].split("/") if r["folder"] else ["racine"]
            n["/".join(parts[:3])] = n.get("/".join(parts[:3]), 0) + 1
        return [{"cat": k, "n": v} for k, v in sorted(n.items())]


# --------------------------------------------------------------------------------------------- description
def _vmt(addon: Path, cds: list[str], name: str) -> tuple[Path | None, str]:
    for cd in cds or [""]:
        p = addon / "materials" / cd / f"{name}.vmt"
        if p.exists():
            return p, p.read_text(encoding="utf-8", errors="replace")
    return None, ""


def _material(addon: Path, cds: list[str], name: str, root: Path | None = None) -> dict:
    """``addon``: the folder holding ``materials/``; paths in the answer are relative to ``root`` (default: addon)."""
    root = root or addon
    vmt, text = _vmt(addon, cds, name)
    entry: dict = {"name": name, "vmt": vmt.relative_to(root).as_posix() if vmt else None, "shader": "", "params": {},
                   "textures": [], "flags": [], "raw": text, "alpha": "OPAQUE"}
    if not vmt:
        return entry
    shader, params, blocks = studio.parse_vmt(text)
    entry["shader"] = shader
    entry["params"] = {k: v for k, v in params.items()}
    for key in params:
        val = params[key].replace("\\", "/").strip("/")
        f = addon / "materials" / f"{val}.vtf"
        if key in TEXTURE_PARAMS or (val and not val.replace(".", "").isdigit() and f.exists()):
            t = {"param": key, "label": PARAM_LABEL.get(key, key), "name": val,
                 "path": (addon / "materials" / f"{val}.vtf").relative_to(root).as_posix(), "exists": f.exists(),
                 # not ours and not in the addon: a texture GMod/the base game ships
                 "stock": not f.exists() and not val.startswith(CONFIG.namespace + "/")}
            if t["exists"]:
                try:
                    t.update(studio.vtf_info(f))
                except (OSError, ValueError):
                    t["exists"] = False
            entry["textures"].append(t)
    flags = []
    if "$alphatest" in params:
        entry["alpha"] = "MASK"
        flags.append("alpha test")
    if "$translucent" in params:
        entry["alpha"] = "BLEND"
        flags.append("translucide")
    for k, label in (("$bumpmap", "normale"), ("$phong", "phong"), ("$envmap", "reflets"), ("$selfillum", "émissif"),
                     ("$nocull", "double face"), ("$additive", "additif"), ("$detail", "détail")):
        if k in params and params[k] not in ("0",):
            flags.append(label)
    if blocks.get("proxies"):
        flags.append("proxies")
    entry["flags"] = flags
    return entry


def _find_source(sid: str, catalog, rel_path: str) -> dict | None:
    """The catalog row a prop output came from (the reverse of ``build._model_path``)."""
    from ..targets.source.build import _model_path
    prefix = f"models/{CONFIG.ns(sid)}/"
    if not rel_path.startswith(prefix) or rel_path.startswith(prefix + "pm/"):
        return None
    inner = rel_path[len(prefix):-4]
    rows = catalog._query("SELECT key, rel, cat, name FROM assets WHERE rel = ?", (inner,))
    tail = inner.rsplit("_", 1)[-1]
    if not rows and len(tail) == 6:
        rows = catalog._query("SELECT key, rel, cat, name FROM assets WHERE lower(key) LIKE ?", (f"%{tail}",))
    for r in rows:
        if _model_path(sid, r["rel"], r["key"]) == f"{CONFIG.ns(sid)}/{inner}":
            return {"key": r["key"], "rel": r["rel"], "cat": r["cat"]}
    return None


def _checks(m: dict) -> list[dict]:
    out: list[dict] = []

    def add(level: str, title: str, detail: str = ""):
        out.append({"level": level, "title": title, "detail": detail})

    miss = [f["name"] for f in m["files"] if f["required"] and not f["exists"]]
    if miss:
        add("error", "Fichiers du modèle manquants", ", ".join(miss))
    else:
        add("ok", "Fichiers du modèle complets", ".mdl, .vvd, .vtx")

    if m["collision"]:
        c = m["collision"]
        add("ok", "Collision présente", f"{c['pieces']} pièce(s), {c['triangles']} triangles, {m['model']['mass']:g} kg")
    elif m["kind"] == "prop":
        add("warn", "Pas de modèle de collision", "Normal pour un décor trop grand ; sinon le prop traversera tout.")

    bad_vmt = [x["name"] for x in m["materials"] if not x["vmt"]]
    if bad_vmt:
        add("error", f"{len(bad_vmt)} matériau(x) introuvable(s)", ", ".join(bad_vmt[:6]) + (" …" if len(bad_vmt) > 6 else ""))
    else:
        add("ok", f"{len(m['materials'])} matériau(x) trouvé(s)")
    bad_tex = [t["name"] for x in m["materials"] for t in x["textures"]
               if not t["exists"] and not t["stock"] and t["param"] != "$envmap"]
    if bad_tex:
        add("error", f"{len(bad_tex)} texture(s) manquante(s)", ", ".join(sorted(set(bad_tex))[:6]))
    odd = sorted({t["name"] for x in m["materials"] for t in x["textures"]
                  if t["exists"] and (t["width"] & (t["width"] - 1) or t["height"] & (t["height"] - 1))})
    if odd:
        add("warn", f"{len(odd)} texture(s) hors puissance de 2", ", ".join(odd[:4]))
    elif m["materials"] and not bad_tex:
        add("ok", "Textures valides", f"{m['texture_count']} fichier(s), {m['texture_bytes'] // 1024} Ko au total")

    tris = m["default_triangles"]
    extra = (f" ({m['triangles']:,} avec toutes les options)".replace(",", " ")) if m["triangles"] != tris else ""
    if tris > 100_000:
        add("warn", f"{tris:,} triangles".replace(",", " "), "Très lourd pour GMod : prévoir des LOD ou réduire." + extra)
    elif tris:
        add("ok", f"{tris:,} triangles affichés".replace(",", " "), f"{len(m['lods'])} niveau(x) de détail{extra}")
    if m["kind"] == "prop" and len(m["lods"]) == 1 and tris > 8000:
        add("info", "Un seul niveau de détail", "Le jeu n’en fournit pas : tous les props de cette taille sont dessinés à pleine résolution.")

    nb = len(m["model"]["bones"])
    if nb > 128:
        add("error", f"{nb} os", "Source en accepte 128 au maximum.")
    if not m["model"]["surfaceprop"]:
        add("warn", "Pas de surface physique", "Les impacts et les sons utiliseront « default ».")
    mass = m["model"]["mass"]
    if m["kind"] == "prop" and m["collision"] and not 0.5 <= mass <= 50000:
        add("warn", f"Masse inhabituelle : {mass:g} kg")

    if m["kind"] == "character":
        vb = sum(b["name"].startswith("ValveBiped.") for b in m["model"]["bones"])
        add("ok" if vb >= 40 else "error", f"Squelette ValveBiped : {vb} os",
            "Animations, ragdoll et hitboxes natifs de GMod." if vb >= 40 else "Les animations de GMod ne s’appliqueront pas.")
        add("ok" if m["model"]["hitboxes"] else "error", f"{len(m['model']['hitboxes'])} hitboxes")
        if not any(a["name"] == "eyes" for a in m["model"]["attachments"]):
            add("warn", "Attachement « eyes » absent", "La vue à la première personne risque d’être décalée.")
        if not m["model"]["includes"]:
            add("warn", "Aucun modèle d’animation inclus", "Le personnage restera en T-pose.")
    else:
        ext, hull = m.get("extent"), m["model"]["hull"]
        if ext and m["collision"]:
            hs = [hull[1][i] - hull[0][i] for i in range(3)]
            if any(h < e * 0.6 for h, e in zip(hs, ext) if e > 8):
                add("warn", "Boîte englobante plus petite que le maillage", "Le prop peut disparaître trop tôt de l’écran.")

    ext = m.get("extent")
    if ext:
        cm = [e / studio.UNITS_PER_METER * 100 for e in ext]
        if max(cm) < 2:
            add("warn", "Dimensions minuscules", f"{max(cm):.1f} cm au plus.")
        elif max(cm) > 5000:
            add("warn", "Dimensions énormes", f"{max(cm) / 100:.0f} m au plus.")
    return out


@lru_cache(maxsize=64)
def _describe(sid: str, root: str, external: bool, rel: str, mtime: float, source_json: str) -> str:
    addon = Path(root)
    mdl_path = addon / rel
    stem = mdl_path.with_suffix("")
    mdl = mdl_path.read_bytes()
    d = studio.parse_mdl(mdl)
    if external:
        kind = "character" if sum(b["name"].startswith("ValveBiped.") for b in d["bones"]) >= 20 else "prop"
    else:
        kind = "character" if rel.startswith(f"models/{CONFIG.ns(sid)}/pm/") else "prop"

    files = []
    present = {p.name[len(stem.name):]: p for p in stem.parent.glob(glob_escape(stem.name) + ".*")}
    vtx_ext = next((e for e in VTX_EXTS if e in present), VTX_EXTS[0])
    required = {".mdl", ".vvd", vtx_ext}
    for ext in sorted(required | set(present)):
        p = present.get(ext)
        files.append({"name": stem.name + ext, "path": p.relative_to(addon).as_posix() if p else None,
                      "bytes": p.stat().st_size if p else 0, "exists": bool(p), "required": ext in required,
                      "abs": str(p) if p else ""})

    # geometry per LOD
    lods, extent = [], None
    vvd, vtx = present.get(".vvd"), present.get(vtx_ext)
    bp_tris: dict[tuple[int, int], int] = {}
    if vvd and vtx:
        vvd_b, vtx_b = vvd.read_bytes(), vtx.read_bytes()
        n_lods, switch = studio.vtx_lods(vtx_b)
        for lod in range(max(1, n_lods)):
            try:
                g = studio.read_geometry(mdl, vvd_b, vtx_b, lod)
            except Exception:  # noqa: BLE001 - a LOD that cannot be read is simply not listed
                break
            used = np.unique(np.concatenate([me["idx"] for me in g.meshes])) if g.meshes else np.zeros(0, int)
            lods.append({"lod": lod, "triangles": sum(len(me["idx"]) // 3 for me in g.meshes), "vertices": int(len(used)),
                         "switch": round(switch[lod], 2) if lod < len(switch) else 0})
            if lod == 0:
                for me in g.meshes:
                    bp_tris[(me["b"], me["m"])] = bp_tris.get((me["b"], me["m"]), 0) + len(me["idx"]) // 3
                if len(used):
                    p = g.pos[used]
                    extent = (p.max(0) - p.min(0)).tolist()
    for bi, bp in enumerate(d["bodyparts"]):
        for mi, mod in enumerate(bp["models"]):
            mod["triangles"] = bp_tris.get((bi, mi), 0)
            mod["name"] = mod["name"].rsplit(".", 1)[0] if mod["name"] else f"option {mi}"
            mod["empty"] = not mod["triangles"]

    # materials: one entry per texture entry the skin table refers to, in a stable order
    tex_used = sorted({t for row in d["skins"] for t in row}) or list(range(len(d["textures"])))
    base = _asset_base(addon, rel)
    materials = [_material(base, d["cdtextures"], d["textures"][t], addon) for t in tex_used if t < len(d["textures"])]
    index_of = {t: i for i, t in enumerate(tex_used)}
    skin_materials = [[index_of.get(t, 0) for t in row] for row in d["skins"]]
    seen = {}
    for x in materials:
        for t in x["textures"]:
            seen[t["path"]] = t.get("bytes", 0)

    phy = present.get(".phy")
    collision = None
    if phy:
        pc = studio.read_phy(phy.read_bytes())
        collision = {"pieces": len(pc["pieces"]) if pc else 0,
                     "triangles": sum(len(p) // 3 for p in pc["pieces"]) if pc else 0} if pc else {"pieces": 0, "triangles": 0}

    out = {
        "path": rel, "kind": kind, "stem": stem.name, "model": d, "files": files,
        "bytes": sum(f["bytes"] for f in files), "lods": lods, "triangles": lods[0]["triangles"] if lods else 0,
        "vertices": lods[0]["vertices"] if lods else 0, "extent": extent,
        # what shows by default: the first option of every bodygroup
        "default_triangles": sum(bp["models"][0]["triangles"] for bp in d["bodyparts"] if bp["models"]),
        "materials": materials, "skin_materials": skin_materials, "texture_count": len(seen),
        "texture_bytes": sum(seen.values()), "collision": collision,
        "source": json.loads(source_json) if source_json else None, "external": external,
    }
    out["checks"] = _checks(out)
    levels = {c["level"] for c in out["checks"]}
    out["verdict"] = "error" if "error" in levels else "warn" if "warn" in levels else "ok"
    return json.dumps(out)


# --------------------------------------------------------------------------------------------------- glb
def build_glb(root: str, rel: str, lod: int, out: Path) -> None:
    """GLB of a compiled model: one node per mesh (``bodypart|model|mesh|skinref``) and one glTF material per
    texture entry of the skin table, textured with the converted VTFs."""
    from ..preview.glb import export_scene
    addon = Path(root)
    stem = (addon / rel).with_suffix("")
    mdl = (addon / rel).read_bytes()
    vvd, vtx = stem.with_suffix(".vvd"), _find_vtx(stem)
    if not (vvd.exists() and vtx):
        raise HTTPException(404, "Fichiers vvd/vtx manquants")
    d = studio.parse_mdl(mdl)
    geo = studio.read_geometry(mdl, vvd.read_bytes(), vtx.read_bytes(), lod)
    tex_used = sorted({t for row in d["skins"] for t in row}) or list(range(len(d["textures"])))
    index_of = {t: i for i, t in enumerate(tex_used)}
    size = 1024 if len(tex_used) <= 12 else 512 if len(tex_used) <= 40 else 384
    materials = []
    for t in tex_used:
        m = (_material(_asset_base(addon, rel), d["cdtextures"], d["textures"][t], addon) if t < len(d["textures"])
             else {"name": f"#{t}", "textures": [], "alpha": "OPAQUE"})
        by = {x["param"]: x for x in m["textures"] if x["exists"]}
        base = by.get("$basetexture")
        norm = by.get("$bumpmap") or by.get("$normalmap")
        materials.append({"name": m["name"], "alpha": m["alpha"],
                          "base": str(addon / base["path"]) if base else None,
                          "normal": "n:" + str(addon / norm["path"]) if norm else None})
    row0 = d["skins"][0] if d["skins"] else []
    nodes = []
    for me in geo.meshes:
        uniq, inv = np.unique(me["idx"], return_inverse=True)
        ti = row0[me["skinref"]] if me["skinref"] < len(row0) else 0
        nodes.append({"name": f"{me['b']}|{me['m']}|{me['k']}|{me['skinref']}",
                      "positions": geo.pos[uniq] / studio.UNITS_PER_METER, "normals": geo.nrm[uniq],
                      "uvs": geo.uv[uniq], "indices": inv.astype(np.uint32), "material": index_of.get(ti, 0)})
    if not nodes:
        raise HTTPException(404, "Le modèle n’a pas de géométrie")

    def read_texture(path, max_size):
        p = str(path)
        a = studio.decode_vtf(Path(p[2:] if p.startswith("n:") else p), max_size)
        if p.startswith("n:"):                         # Source normal maps are Y-down, glTF's are Y-up
            a = a.copy()
            a[..., 1] = 255 - a[..., 1]
        return a
    export_scene(nodes, materials, out, size, read_texture)


# ----------------------------------------------------------------------------------------------- routes
def _pick_folder() -> str:
    return pick_folder("Choisir un dossier de modèles (addon décompilé…)")


def register(app: FastAPI, *, catalog_of, resolve_source, texcat_of=lambda sid: None) -> None:
    """``resolve_source(sid)`` returns the source (404 when unknown); ``catalog_of(sid)`` its prop catalog;
    ``texcat_of(sid)`` its texture catalog (links a converted material back to the game's one)."""
    indexes: dict[tuple[str, str], OutputIndex] = {}

    def roots_of(sid: str) -> list[Root]:
        src = resolve_source(sid)
        out = [Root("", CONFIG.addon_dir(sid), f"Sortie omni · {getattr(src, 'title', sid)}", False)]
        out += [Root(r["id"], Path(r["path"]), r.get("label") or Path(r["path"]).name, True) for r in _stored_roots()]
        return out

    def root_of(sid: str, root: str) -> Root:
        for r in roots_of(sid):
            if r.id == root:
                return r
        raise HTTPException(404, "Dossier inconnu")

    def index_of(sid: str, root: str) -> OutputIndex:
        r = root_of(sid, root)
        return indexes.setdefault((sid, r.id), OutputIndex(sid, r))

    def model_file(sid: str, root: str, path: str) -> tuple[Root, Path]:
        r = root_of(sid, root)
        if not path.endswith(".mdl"):
            raise HTTPException(400, "Ce n’est pas un modèle")
        f = _within(r.path, path)
        if not f.is_file():
            raise HTTPException(404, "Modèle introuvable dans ce dossier")
        return r, f

    def cache_dir(sid: str) -> Path:
        return CONFIG.previews / sid / "output"

    # ---- roots
    @app.get("/api/{sid}/output/roots")
    def roots_list(sid: str):
        rows = []
        for r in roots_of(sid):
            idx = indexes.get((sid, r.id))
            rows.append({"id": r.id, "label": r.label, "path": str(r.path), "external": r.external,
                         "exists": r.path.is_dir(), "models": len(idx.rows) if idx and idx.scanned else None})
        return rows

    @app.post("/api/{sid}/output/roots")
    def roots_add(sid: str, path: str):
        resolve_source(sid)
        p = Path(path.strip().strip('"')).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Ce dossier n’existe pas : {p}")
        rid = _root_id(p)
        rows = _stored_roots()
        if all(r["id"] != rid for r in rows):
            rows.append({"id": rid, "path": str(p.resolve()), "label": p.resolve().name})
            _store_roots(rows)
        return {"id": rid}

    @app.delete("/api/{sid}/output/roots/{rid}")
    def roots_remove(sid: str, rid: str):
        resolve_source(sid)
        _store_roots([r for r in _stored_roots() if r["id"] != rid])
        indexes.pop((sid, rid), None)
        return {"ok": True}

    @app.post("/api/{sid}/output/pick")
    def roots_pick(sid: str):
        resolve_source(sid)
        try:
            return {"path": _pick_folder()}
        except Exception as e:  # noqa: BLE001 - no GUI available: the user types the path instead
            raise HTTPException(501, f"Sélecteur de dossier indisponible : {e}")

    # ---- models
    @app.get("/api/{sid}/output")
    def output_list(sid: str, q: str = "", kind: str = "", cat: str = "", sort: str = "recent", limit: int = 100,
                    offset: int = 0, fresh: int = 0, root: str = ""):
        """``fresh=1`` rescans the folder first (after a conversion), otherwise the index is at most 20 s old."""
        idx = index_of(sid, root)
        if fresh:
            idx._scan()
        idx.refresh()
        base = idx.query(q, "", cat, sort)
        rows = [r for r in base if not kind or r["kind"] == kind]
        kinds = {"prop": 0, "character": 0, "model": 0}
        for r in base:
            kinds[r["kind"]] += 1
        return {"total": len(rows), "kinds": kinds, "scanned": idx.scanned, "items": rows[offset:offset + min(limit, 500)]}

    @app.get("/api/{sid}/output/stats")
    def output_stats(sid: str, root: str = ""):
        idx = index_of(sid, root)
        idx.refresh()
        rows = idx.rows
        return {"models": len(rows), "props": sum(r["kind"] == "prop" for r in rows),
                "characters": sum(r["kind"] == "character" for r in rows), "bytes": sum(r["bytes"] for r in rows),
                "latest": rows[0]["mtime"] if rows else 0, "scanned": idx.scanned, **(idx.materials or {}),
                "path": str(idx.addon), "external": idx.root.external}

    @app.get("/api/{sid}/output/categories")
    def output_categories(sid: str, root: str = ""):
        idx = index_of(sid, root)
        idx.refresh()
        return idx.categories()

    @app.get("/api/{sid}/output/model")
    def output_model(sid: str, path: str, root: str = ""):
        r, f = model_file(sid, root, path)
        source = None
        if not r.external and "props" in getattr(resolve_source(sid), "capabilities", ()):
            try:
                source = _find_source(sid, catalog_of(sid), path)
            except Exception:  # noqa: BLE001 - catalog still being built
                pass
        try:
            text = _describe(sid, str(r.path), r.external, path, f.stat().st_mtime, json.dumps(source) if source else "")
            tc = None if r.external else texcat_of(sid)
            if tc is not None and tc.ready():
                data = json.loads(text)
                for m in data["materials"]:
                    m["origin"] = tc.material_for_vmt(m["name"])
                text = json.dumps(data)
            return Response(text, media_type="application/json")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"Modèle illisible ({type(e).__name__}: {e}). Version de Source non prise en charge ?")

    @app.get("/api/{sid}/output/glb")
    def output_glb(sid: str, path: str, lod: int = 0, root: str = ""):
        r, f = model_file(sid, root, path)
        key = hashlib.sha1(f"{r.id}|{path}".encode()).hexdigest()[:12]
        out = cache_dir(sid) / f"{key}_{lod}_{int(f.stat().st_mtime)}.glb"
        if not out.exists():
            for old in out.parent.glob(f"{key}_*"):
                old.unlink(missing_ok=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                build_glb(str(r.path), path, lod, out)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, f"Rendu impossible : {type(e).__name__}: {e}")
        return FileResponse(out, media_type="model/gltf-binary")

    @app.get("/api/{sid}/output/collision")
    def output_collision(sid: str, path: str, root: str = ""):
        _r, f = model_file(sid, root, path)
        phy = f.with_suffix(".phy")
        if not phy.exists():
            raise HTTPException(404, "Pas de modèle de collision")
        pc = studio.read_phy(phy.read_bytes())
        if not pc or not pc["pieces"]:
            raise HTTPException(404, "Modèle de collision illisible")
        pieces = studio.phy_pieces_to_view(f.read_bytes(), pc["pieces"])
        return {"pieces": [p.round(5).reshape(-1).tolist() for p in pieces], "text": pc["text"][:2000]}

    def texture_file(sid: str, root: str, path: str) -> tuple[Root, Path]:
        r = root_of(sid, root)
        if not path.endswith(".vtf"):
            raise HTTPException(400, "Ce n’est pas une texture")
        f = _within(r.path, path)
        if not f.is_file():
            raise HTTPException(404, "Texture introuvable dans ce dossier")
        return r, f

    @app.get("/api/{sid}/output/texture")
    def output_texture(sid: str, path: str, channel: str = "rgb", size: int = 1024, root: str = ""):
        r, f = texture_file(sid, root, path)
        if channel not in ("rgb", "rgba", "r", "g", "b", "a"):
            raise HTTPException(400, "Canal inconnu")
        size = max(16, min(size, 4096))
        key = hashlib.sha1(f"{r.id}|{path}|{channel}|{size}".encode()).hexdigest()[:16]
        out = cache_dir(sid) / "tex" / f"{key}_{int(f.stat().st_mtime)}.png"
        if not out.exists():
            try:
                png = studio.png_bytes(studio.channel_view(studio.decode_vtf(f, size), channel))
            except (ValueError, KeyError, OSError) as e:
                raise HTTPException(415, str(e))
            for old in out.parent.glob(f"{key}_*"):
                old.unlink(missing_ok=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)
        return FileResponse(out, media_type="image/png")

    @app.get("/api/{sid}/output/uv")
    def output_uv(sid: str, path: str, skinref: int = 0, size: int = 1024, root: str = ""):
        _r, f = model_file(sid, root, path)
        stem = f.with_suffix("")
        vtx = _find_vtx(stem)
        if not (stem.with_suffix(".vvd").exists() and vtx):
            raise HTTPException(404, "Fichiers vvd/vtx manquants")
        geo = studio.read_geometry(f.read_bytes(), stem.with_suffix(".vvd").read_bytes(), vtx.read_bytes(), 0)
        return Response(studio.uv_overlay(geo, skinref, max(64, min(size, 4096))), media_type="image/png")

    @app.post("/api/{sid}/output/reveal")
    def output_reveal(sid: str, path: str, root: str = ""):
        r = root_of(sid, root)
        f = _within(r.path, path)
        if not f.exists():
            raise HTTPException(404, "Introuvable")
        reveal(f)
        return {"ok": True}
