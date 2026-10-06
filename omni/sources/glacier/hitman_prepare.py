"""Preparing HITMAN World of Assassination once its folder is added (nothing is extracted).

Steps (skipped when done; preparing again after a game update re-reads the package tables):
  packages   every chunk*.rpkg / patch of Runtime opened in place (tables only)
  names      the readable resource paths (glacier-modding/Hitman-Hashes, MIT), turned into a lookup database
  catalog    the props catalog and the outfits offered as playermodels
"""
from __future__ import annotations

import json
import time

STEPS = [("packages", "Paquets du jeu (lecture directe)"), ("names", "Noms des ressources (Hitman-Hashes)"),
         ("catalog", "Catalogue des modèles et personnages")]


def _dir(info: dict):
    from ...core.config import CONFIG
    d = CONFIG.game_dir(info["id"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def signature(info: dict) -> list:
    from .hitman import runtime_of
    from .store import packages_of
    try:
        return [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in packages_of(runtime_of(info))]
    except OSError:
        return []


def status(info: dict) -> dict:
    from .hitman import names_dir
    try:
        state = json.loads((_dir(info) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    current = bool(state) and state.get("sig") == signature(info)
    hl = names_dir(info["id"]) / "hash_list.txt"
    steps = [{"key": "packages", "label": STEPS[0][1], "ok": bool(state.get("packages")) and current},
             {"key": "names", "label": STEPS[1][1], "ok": hl.is_file() and hl.stat().st_size > 1_000_000},
             {"key": "catalog", "label": STEPS[2][1], "ok": bool(state.get("catalog")) and current}]
    return {"steps": steps, "ready": all(s["ok"] for s in steps), "updated": bool(state) and not current}


def fetch_names(info: dict, say=print, cancel=None) -> None:
    import py7zr
    from ...core.setup import download
    from .hitman import NAMES_URL, names_dir
    d = names_dir(info["id"])
    archive = d / "latest-hashes.7z"
    say("téléchargement des noms (Hitman-Hashes)…")
    download(NAMES_URL, archive, None, cancel)
    with py7zr.SevenZipFile(archive) as z:
        z.extract(path=d, targets=["hash_list.txt"])
    archive.unlink(missing_ok=True)
    (d / "names.sqlite").unlink(missing_ok=True)


def run(info: dict, say=print, count=None, cancel=None) -> dict:
    from ...core.setup import Cancelled
    from ...sources import registry
    state = {"sig": signature(info), "when": time.time()}

    def step(i, label):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        say(label + "…")
        if count:
            count(i, len(STEPS))

    step(0, "Ouverture des paquets du jeu")
    registry.reset()
    st = status(info)
    if not st["steps"][1]["ok"]:
        step(1, "Noms des ressources")
        fetch_names(info, say, cancel)
        registry.reset()
    src = registry.get_source(info["id"])
    types = src.archive.store.types()
    say(f"{len(src.archive.store)} ressources dans {len(src.packages)} paquets "
        f"({types.get('PRIM', 0)} PRIM, {types.get('TEXT', 0)} TEXT, {types.get('MATI', 0)} MATI)")
    state["packages"] = True
    src.names.name(0)                                   # builds the names database once
    step(2, "Catalogue des modèles et des personnages")
    from ...core.catalog import Catalog
    cat = Catalog(src)
    n = cat.build(force=True)
    cat.db.close()
    chars = src.characters()
    say(f"{n} modèles, {len(chars)} tenues")
    state["catalog"] = True
    (_dir(info) / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if count:
        count(len(STEPS), len(STEPS))
    return {"modèles": n, "personnages": len(chars), "textures": types.get("TEXT", 0)}
