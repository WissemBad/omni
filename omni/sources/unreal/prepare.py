"""Preparing an Unreal game once it is added: everything omni needs is fetched or derived from the game folder.

Steps (each skipped when already done, so preparing again is also the repair after a game update):
  oodle      Epic's Oodle decompressor, downloaded and checked (SHA-256) — shared by every Unreal game
  mappings   the property layouts, read from the shipping executable (cached with its size and date)
  index      the containers are opened and the packages of each asset class listed (cached with the .utoc dates)
  catalog    the props catalog and the playable characters (humanoid skeletal meshes)
"""
from __future__ import annotations

import json
import time

from . import game as ue

STEPS = [("oodle", "Décompresseur Oodle"), ("mappings", "Structures du jeu (exécutable ou .usmap)"),
         ("index", "Index des ressources"), ("catalog", "Catalogue des modèles et personnages")]


def _state_file(info: dict):
    return ue.game_dir(info["id"]) / "state.json"


def signature(info: dict) -> list:
    from pathlib import Path
    sig = []
    for p in sorted(Path(info["paks"]).glob("*.utoc")):
        try:
            st = p.stat()
            sig.append([p.name, st.st_size, st.st_mtime_ns])
        except OSError:
            pass
    return sig


def status(info: dict) -> dict:
    """What is done; ``ready`` once everything was prepared for the game files as they are now."""
    try:
        state = json.loads(_state_file(info).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    current = state.get("sig") == signature(info)
    steps = []
    for key, label in STEPS:
        if key == "oodle":
            ok = ue.oodle_ok()["ok"]
        elif key == "mappings":
            ok = (ue.game_dir(info["id"]) / "mappings.txt").is_file()
        else:
            ok = bool(state.get(key)) and current
        steps.append({"key": key, "label": label, "ok": ok})
    return {"steps": steps, "ready": all(s["ok"] for s in steps), "updated": bool(state) and not current,
            "prepared_at": state.get("when", 0)}


def run(info: dict, say=print, count=None, cancel=None) -> dict:
    from ...sources import registry
    state = {"sig": signature(info), "when": time.time()}
    total = len(STEPS)

    def step(i: int, label: str) -> None:
        if cancel is not None and cancel.is_set():
            from ...core.setup import Cancelled
            raise Cancelled()
        say(label + "…")
        if count:
            count(i, total)

    step(0, "Oodle")
    ue.ensure_oodle(say, cancel)
    step(1, "Lecture des structures du jeu")
    ue.forget(info["id"])
    ue.mappings(info, say, cancel)
    step(2, "Ouverture des conteneurs et index des ressources")
    registry.reset()
    src = registry.get_source(info["id"])
    counts = {}
    for cls in ("StaticMesh", "SkeletalMesh", "Texture2D", "SoundWave", "Material", "MaterialInstanceConstant"):
        counts[cls] = len(src.index(cls))
    say(", ".join(f"{n} {c}" for c, n in counts.items()))
    state["index"] = True
    step(3, "Catalogue des modèles et des personnages")
    from ...core.catalog import Catalog
    cat = Catalog(src)
    n = cat.build(force=True)
    cat.db.close()
    chars = src.characters()
    say(f"{n} modèles, {len(chars)} personnages jouables")
    state["catalog"] = True
    _state_file(info).write_text(json.dumps(state), encoding="utf-8")
    if count:
        count(total, total)
    return {"modèles": n, "personnages": len(chars), "textures": counts["Texture2D"], "sons": counts["SoundWave"]}
