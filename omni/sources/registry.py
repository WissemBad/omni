"""Known game sources. The CLI, the batch workers and the web UI resolve a source by id here and nowhere else.

Two kinds of sources:
  * built-in ones (``SOURCES``): 007 First Light, set up through the first-run wizard (extracted ``Assets/Sorted``);
  * the games of the library (``omni.games``, ``<workspace>/games.json``): any game the user added by its folder
    whose engine omni reads (Unreal Engine 5, Glacier/HITMAN), opened in place from the game's own files.
Sources are imported lazily (a source may need heavy dependencies) and instantiated once per process.

A source class exposes (see sources/base.py for the contract, glacier/adapter.py and unreal/adapter.py for the
implementations): id, title, capabilities, catalog_rows(), load_model/load_material/load_texture, characters(),
texture_index(), list_sounds().
"""
from __future__ import annotations

import importlib
import threading

SOURCES: dict[str, tuple[str, str]] = {
    "007fl": ("omni.sources.glacier.adapter", "GlacierSource"),
}
ALIASES = {"glacier": "007fl"}
# engine/profile of a library game -> (module, class); the class is built with the game's library entry
ENGINES: dict[str, tuple[str, str]] = {
    "unreal": ("omni.sources.unreal.adapter", "UnrealSource"),
    "hitman3": ("omni.sources.glacier.hitman", "HitmanSource"),
}

_instances: dict[str, object] = {}
_lock = threading.RLock()


def _library() -> dict[str, dict]:
    """Library games omni has a source for, by id (built-in ids excluded: they keep their own setup)."""
    try:
        from .. import games
        out = {}
        for g in games.load():
            gid = g.get("id", "")
            if gid in SOURCES or not g.get("supported"):
                continue
            if g.get("profile") in ENGINES or g.get("engine") in ENGINES:
                out[gid] = g
        return out
    except Exception:  # noqa: BLE001 - a broken library must not break the built-in sources
        return {}


def resolve(source_id: str) -> str:
    sid = ALIASES.get(source_id, source_id)
    if sid not in SOURCES and sid not in _library():
        raise KeyError(f"unknown source '{source_id}' (known: {', '.join(source_ids())})")
    return sid


def game_info(source_id: str) -> dict | None:
    return _library().get(ALIASES.get(source_id, source_id))


def source_class(source_id: str):
    sid = resolve(source_id)
    if sid in SOURCES:
        module, cls = SOURCES[sid]
    else:
        g = _library()[sid]
        module, cls = ENGINES.get(g.get("profile")) or ENGINES[g["engine"]]
    return getattr(importlib.import_module(module), cls)


def get_source(source_id: str):
    sid = resolve(source_id)
    with _lock:
        if sid not in _instances:
            cls = source_class(sid)
            _instances[sid] = cls() if sid in SOURCES else cls(_library()[sid])
        return _instances[sid]


def describe(source_id: str) -> dict:
    """id, title, description and capabilities without opening the game."""
    sid = resolve(source_id)
    cls = source_class(sid)
    g = game_info(sid)
    if g is None:
        return {"id": sid, "title": cls.title, "description": getattr(cls, "description", ""),
                "capabilities": list(getattr(cls, "capabilities", ()))}
    return {"id": sid, "title": g.get("title") or sid,
            "description": f"{getattr(cls, 'description', '')} {g.get('version') or ''}".strip(),
            "capabilities": list(g.get("capabilities") or getattr(cls, "capabilities", ())), "engine": g.get("engine")}


def reset() -> None:
    """Forget the instances: a source is rebuilt (new assets folder, new names) at its next use."""
    with _lock:
        for inst in _instances.values():
            close = getattr(inst, "close", None)
            if close is not None:
                try:
                    close()               # files a download is about to replace must not stay open
                except Exception:  # noqa: BLE001
                    pass
        _instances.clear()


def source_ids() -> list[str]:
    return list(SOURCES) + [g for g in _library() if g not in SOURCES]
