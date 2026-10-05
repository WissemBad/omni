"""First-run setup: find the game, extract its resources, fetch the names and the model compiler.

omni ships without any game data. Setting up means:
  1. game      folder of the game holding ``Runtime/*.rpkg`` (detected in the Steam libraries, or chosen);
  2. assets    the resources omni reads, extracted from the packages into ``Assets/Sorted`` (native extractor, only
               the resource types omni uses, resumable);
  3. names     the readable paths of the resources (glacier-modding/Bond-Hashes, MIT), turned into a lookup database;
  4. gmod      Garry's Mod (detected in Steam): player animations and the folder the addon is linked into;
  5. studiomdl the model compiler (StudioMDL-CE, downloaded and checked against pinned SHA-256 sums).
Each step reports ``ok`` and what is missing; the interface runs the long ones as jobs.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

from .config import CONFIG
from .windows import find_steam_game

# resource types the sources read (kept in sync with the ``archive.index`` calls of sources/glacier)
NEEDED_TYPES = ["ALOC", "ASET", "BORG", "DLGE", "ECPB", "ECPT", "MATI", "MATT", "PRIM", "TBLU", "TEMP", "TEXD", "TEXT",
                "WBNK", "WSGB", "WSGT", "WSWB", "WSWT", "WWEM", "WWES", "WWEV"]

NAMES_URL = "https://github.com/glacier-modding/Bond-Hashes/releases/latest/download/latest-hashes.7z"
STUDIOMDL_COMMIT = "87b85ab8181e4a0f5928791383280f4fcc12ae3f"
STUDIOMDL_BASE = f"https://raw.githubusercontent.com/DeadZoneLuna/StudioMDL-CE/{STUDIOMDL_COMMIT}/bin/x64/"
STUDIOMDL_FILES = {
    "cestudiomdl.exe": "bc5cbbcb1444d539d35a36afdc7a84b7419839fd1b15674e2d0ec6ffc37e0758",
    "cefilesystem_stdio.dll": "db73c645ef0f0d17c303e5cb751981432252edca7af2ee543c07a6b34bb6bd4f",
    "cetr0.dll": "ee91fca6f31005f25aee80fd74a3b9586a603634a8d7adf5d505dcdf40a5b10b",
    "cevphysics.dll": "d5ed5ccc0622e0a544dc22ea3c81246142f5ca88c9f5dc7034f0fa36456fc7a4",
    "cevstdlib.dll": "a001652e249475af802b0777be6f3aec2a541060ac3d37ee8402cf1097671c74",
}
GAME_NAMES = ("007 first light", "first light")


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------------------------------------ game
def game_packages(folder: Path | str) -> tuple[Path, list[Path]] | None:
    """(folder with the packages, [.rpkg]) for a game folder: ``Runtime`` inside, the folder itself, or one level up."""
    base = Path(folder)
    if not base.is_dir():
        return None
    for cand in (base / "Runtime", base, *sorted(base.glob("*/Runtime"))[:3], *sorted(base.glob("*/*/Runtime"))[:3]):
        pk = sorted(cand.glob("chunk*.rpkg")) if cand.is_dir() else []
        if pk:
            return cand, pk
    return None


_detected: tuple[float, Path | None] = (0.0, None)


def detect_game() -> Path | None:
    """The game in the Steam libraries (looked up at most every 30 s: ``status()`` is called on every page load)."""
    global _detected
    import time
    if time.time() - _detected[0] < 30:
        return _detected[1]
    p = find_steam_game(*GAME_NAMES)
    found = p if p and game_packages(p) else None
    _detected = (time.time(), found)
    return found


def assets_ok(root: Path | None = None) -> dict:
    root = root or CONFIG.assets_sorted
    chunks = [c for c in sorted(root.glob("chunk*")) if c.is_dir()] if root.is_dir() else []
    have = {t for c in chunks for t in ("PRIM", "TEXT", "MATI", "TEMP") if (c / t).is_dir()}
    return {"path": str(root), "ok": {"PRIM", "TEXT", "MATI", "TEMP"} <= have, "chunks": [c.name for c in chunks]}


def names_ok() -> dict:
    f = CONFIG.hash_list
    return {"path": str(f), "ok": f.is_file() and f.stat().st_size > 1_000_000, "bytes": f.stat().st_size if f.is_file() else 0}


def studiomdl_ok() -> dict:
    return {"path": str(CONFIG.studiomdl), "ok": CONFIG.studiomdl.is_file()}


def gmod_ok() -> dict:
    g = CONFIG.gmod
    return {"path": str(g), "ok": (g / "garrysmod" / "garrysmod_dir.vpk").is_file(), "gmad": (g / "bin" / "gmad.exe").is_file()}


def status() -> dict:
    """What is configured and what is missing, step by step (``ready``: browsing works, ``can_convert``: models too)."""
    game = CONFIG.game or detect_game()
    pk = game_packages(game) if game else None
    a, n, g, s = assets_ok(), names_ok(), gmod_ok(), studiomdl_ok()
    free = shutil.disk_usage(CONFIG.root if CONFIG.root.exists() else CONFIG.root.parent).free if (CONFIG.root.exists() or CONFIG.root.parent.exists()) else 0
    return {
        "game": {"path": str(game) if game else "", "ok": bool(pk), "packages": [p.name for p in pk[1]] if pk else [],
                 "detected": game is not None and CONFIG.game is None},
        "assets": a, "names": n, "gmod": g, "studiomdl": s,
        "ready": a["ok"] and n["ok"],
        "can_convert": a["ok"] and n["ok"] and g["ok"] and s["ok"],
        "data_dir": str(CONFIG.root), "free_bytes": free,
    }


# ------------------------------------------------------------------------------------------------ downloads
def download(url: str, dest: Path, progress=None, cancel=None, sha256: str = "") -> Path:
    """Download to ``dest`` (atomic), reporting (bytes done, total); checks the SHA-256 when given."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "omni"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except BaseException:
        tmp.unlink(missing_ok=True)            # a cancelled or failed download leaves no .part behind
        raise
    if sha256 and h.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{dest.name}: somme de contrôle inattendue ({h.hexdigest()[:12]}…) : fichier refusé")
    os.replace(tmp, dest)
    return dest


def fetch_names(say=print, count=None, cancel=None, release=None) -> dict:
    """Download the 007 names (Bond-Hashes) and rebuild the lookup database. ``release`` is called before the
    old database is deleted (the running source keeps it open, which Windows does not allow to delete)."""
    import py7zr
    CONFIG.hash_list.parent.mkdir(parents=True, exist_ok=True)
    archive = CONFIG.workspace / "names" / "latest-hashes.7z"
    say("téléchargement de la liste des noms (Bond-Hashes)…")
    download(NAMES_URL, archive, lambda d, t: count and count(d, t), cancel)
    say("décompression…")
    with py7zr.SevenZipFile(archive) as z:
        z.extract(path=CONFIG.hash_list.parent, targets=["hash_list.txt"])
    archive.unlink(missing_ok=True)
    if release is not None:
        release()
    CONFIG.names_db.unlink(missing_ok=True)
    return names_ok()


def fetch_studiomdl(say=print, count=None, cancel=None) -> dict:
    """StudioMDL-CE into ``<data>/tools/studiomdl-ce`` (files pinned by commit and SHA-256)."""
    dest = CONFIG.tools / "studiomdl-ce"
    todo = list(STUDIOMDL_FILES.items())
    for i, (name, sha) in enumerate(todo):
        say(f"téléchargement de {name}…")
        download(STUDIOMDL_BASE + name, dest / name, None, cancel, sha)
        if count:
            count(i + 1, len(todo))
    CONFIG.refresh_studiomdl()
    return studiomdl_ok()


# ------------------------------------------------------------------------------------------------ extraction
def estimate(packages: list[Path]) -> dict:
    """Disk space the extraction needs: the size of the wanted resources, from the package tables."""
    from ..native import N
    total = 0
    count = 0
    for p in packages:
        info = N.rpkg_info(str(p))
        for t in NEEDED_TYPES:
            total += info["type_bytes"].get(t, 0)
            count += info["types"].get(t, 0)
    return {"bytes": total, "files": count}


def extract_assets(game: Path, say=print, count=None, cancel=None, workers: int = 0) -> dict:
    """Extract the needed resources of the game's packages into ``Assets/Sorted`` (resumable)."""
    import threading
    import time
    from ..native import N
    found = game_packages(game)
    if not found:
        raise RuntimeError(f"aucun package .rpkg dans {game} (le dossier du jeu contient normalement Runtime/chunk0.rpkg)")
    pkgs = found[1]
    root = CONFIG.assets_sorted
    root.mkdir(parents=True, exist_ok=True)
    say(f"{len(pkgs)} package(s) : {', '.join(p.name for p in pkgs)}")
    result: dict = {}
    err: list[BaseException] = []

    def run():
        try:
            result.update(N.rpkg_extract([str(p) for p in pkgs], str(root), NEEDED_TYPES, workers))
        except BaseException as e:  # noqa: BLE001
            err.append(e)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    while th.is_alive():
        if cancel is not None and cancel.is_set():
            N.rpkg_cancel()
        done, total = N.rpkg_progress()
        if count:
            count(done, total)
        time.sleep(0.4)
    th.join()
    if err:
        if "cancelled" in str(err[0]):
            raise Cancelled()
        raise RuntimeError(str(err[0]))
    if count:
        count(*N.rpkg_progress())
    say(f"{result['written']} fichiers écrits, {result['skipped']} déjà présents, {len(result['errors'])} erreurs")
    return result
