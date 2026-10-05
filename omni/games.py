"""The game library: give omni a game folder, it works out which game and engine it is.

``identify(folder)`` looks at what is inside (Glacier ``Runtime/chunk*.rpkg``, Unreal ``<Project>/Content/Paks`` with
``.pak``/``.utoc`` and a ``*-Shipping.exe``) and returns what omni needs to know: engine, profile (the converter
variant), title and the capabilities its source offers. The library (``<workspace>/games.json``) lists the games the
user added; ``scan_steam()`` offers the ones installed in the Steam libraries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from .core.config import CONFIG
from .core.windows import steam_libraries

log = logging.getLogger("omni.games")

# profile -> what the source of that profile can do today
PROFILES = {
    "007fl": {"engine": "glacier", "title": "007 First Light", "capabilities": ["props", "characters", "textures", "sounds"]},
    "hitman3": {"engine": "glacier", "title": "HITMAN World of Assassination", "capabilities": []},
    "unreal": {"engine": "unreal", "title": "", "capabilities": []},
}
_lock = threading.Lock()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "game"


def _glacier(folder: Path) -> dict | None:
    from .core.setup import game_packages
    found = game_packages(folder)
    if not found:
        return None
    probe = " ".join(p.name.lower() for p in [folder, *folder.parents[:1]]) + " " + " ".join(
        p.name.lower() for p in folder.glob("Retail/*.exe"))
    if "007" in probe or "firstlight" in probe.replace(" ", ""):
        return {"profile": "007fl", "title": PROFILES["007fl"]["title"], "root": str(folder)}
    if "hitman" in probe:
        return {"profile": "hitman3", "title": PROFILES["hitman3"]["title"], "root": str(folder)}
    return None


def _unreal_version(exe: Path) -> str:
    """``5.1`` from the ``++UE5+Release-5.1`` string compiled into every Unreal executable."""
    ascii_pat = re.compile(rb"\+\+UE[45]\+Release-([45]\.\d+)")
    try:
        with open(exe, "rb") as f:
            tail = b""
            while chunk := f.read(8 << 20):
                data = tail + chunk
                m = ascii_pat.search(data)
                if m:
                    return m.group(1).decode()
                m = ascii_pat.search(data.replace(b"\0", b""))        # UTF-16 strings (UE4 builds)
                if m:
                    return m.group(1).decode()
                tail = chunk[-96:]
    except OSError:
        pass
    return ""


def _unreal(folder: Path) -> dict | None:
    for project in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())[:20]] if folder.is_dir() else []:
        paks = project / "Content" / "Paks"
        if not paks.is_dir():
            continue
        containers = [p for p in paks.iterdir() if p.suffix.lower() in (".pak", ".utoc")]
        if not containers:
            continue
        exes = sorted((project / "Binaries" / "Win64").glob("*-Shipping.exe")) or sorted(folder.glob("**/*-Shipping.exe"))[:1]
        return {"profile": "unreal", "title": project.name, "root": str(folder), "project": project.name,
                "paks": str(paks), "iostore": any(p.suffix.lower() == ".utoc" for p in containers),
                "version": _unreal_version(exes[0]) if exes else ""}
    return None


def identify(folder: str | Path) -> dict | None:
    """What game ``folder`` holds, or None when omni does not recognise it."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    info = _glacier(folder) or _unreal(folder)
    if info is None:
        return None
    profile = PROFILES[info["profile"]]
    info.update(engine=profile["engine"], capabilities=profile["capabilities"], supported=bool(profile["capabilities"]))
    info["id"] = info["profile"] if info["profile"] != "unreal" else "ue-" + slug(info["title"])
    return info


# ------------------------------------------------------------------------------------------------ library
def _file() -> Path:
    return CONFIG.workspace / "games.json"


def load() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return [g for g in data if isinstance(g, dict) and g.get("id")]
    except (OSError, ValueError):
        return []


def _save(games: list[dict]) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_name(f"games.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(games, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _file())


def add(folder: str | Path) -> dict:
    """Add (or refresh) the game in ``folder``; raises ValueError when it is not a game omni knows."""
    info = identify(folder)
    if info is None:
        raise ValueError("Ce dossier ne contient pas de jeu reconnu (Glacier : Runtime\\chunk0.rpkg ; Unreal : "
                         "<Projet>\\Content\\Paks).")
    with _lock:
        games = [g for g in load() if g["id"] != info["id"]]
        games.append(info)
        _save(sorted(games, key=lambda g: g["title"].lower()))
    return info


def remove(game_id: str) -> bool:
    with _lock:
        games = load()
        kept = [g for g in games if g["id"] != game_id]
        _save(kept)
        return len(kept) != len(games)


def scan_steam() -> list[dict]:
    """Games of the Steam libraries that omni recognises and that are not in the library yet."""
    have = {g["id"] for g in load()}
    out = []
    for lib in steam_libraries():
        common = lib / "steamapps" / "common"
        if not common.is_dir():
            continue
        for d in sorted(common.iterdir()):
            if not d.is_dir():
                continue
            try:
                info = identify(d)
            except OSError:
                continue
            if info and info["id"] not in have:
                out.append(info)
    return out


def ensure_defaults() -> None:
    """The game omni was set up with (settings or Steam) is in the library from the first launch."""
    try:
        from .core.setup import detect_game
        folder = CONFIG.game or detect_game()
        if folder and not any(g["id"] == "007fl" for g in load()):
            add(folder)
    except Exception:  # noqa: BLE001 - the library is a convenience
        log.debug("default game not added", exc_info=True)
