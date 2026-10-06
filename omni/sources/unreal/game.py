"""Opening an Unreal Engine 5 game with nothing but its folder.

Everything else is fetched or derived here, once, and cached under ``<workspace>/games/<game id>/``:
  * Oodle (``oo2core_9_win64.dll``, Epic's redistributable, SHA-256 pinned) into ``<data>/tools/oodle``;
  * the property layouts ("mappings"), read statically from the game's shipping executable by the Rust core
    (``unreal_mappings``) and cached with the executable's size/mtime; a ``.usmap`` (local, else from the community
    archive) replaces it when the executable cannot be read;
  * the engine version, from the executable (``games.identify``).
One ``UnrealGame`` handle is kept per process and game (the containers' tables stay in memory, reads are thread-safe).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ...core.config import CONFIG

log = logging.getLogger("omni.unreal")

OODLE_URL = ("https://github.com/WorkingRobot/OodleUE/raw/refs/heads/main/Engine/Source/Programs/Shared/"
             "EpicGames.Oodle/Sdk/2.9.10/win/redist/oo2core_9_win64.dll")
OODLE_SHA256 = "6f5d41a7892ea6b2db420f2458dad2f84a63901c9a93ce9497337b16c195f457"

_lock = threading.Lock()
_games: dict[str, object] = {}


def game_dir(game_id: str) -> Path:
    d = CONFIG.game_dir(game_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def oodle_path() -> Path:
    return CONFIG.tools / "oodle" / "oo2core_9_win64.dll"


def oodle_ok() -> dict:
    p = oodle_path()
    return {"path": str(p), "ok": p.is_file()}


def ensure_oodle(say=None, cancel=None) -> Path:
    """The Oodle DLL, downloaded (and checked) the first time a game needs it; also looks next to the game."""
    p = oodle_path()
    if not p.is_file():
        from ...core.setup import download
        if say:
            say("téléchargement d’Oodle (oo2core_9_win64.dll)…")
        download(OODLE_URL, p, None, cancel, OODLE_SHA256)
    from ...native import N
    N.oodle_load(str(p))
    return p


ARCHIVE_TREE = "https://api.github.com/repos/TheNaeem/Unreal-Mappings-Archive/git/trees/HEAD?recursive=1"
ARCHIVE_RAW = "https://raw.githubusercontent.com/TheNaeem/Unreal-Mappings-Archive/HEAD/"
MIN_STRUCTS = 200          # fewer than this read from the executable: the extraction is not trusted


def _norm(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def _natural(text: str) -> list:
    import re
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", text.lower()) if t]


def archive_pick(paths: list[str], names: list[str]) -> str | None:
    """The archive's .usmap for a game known as any of ``names``: the file at the root of its folder, else the most
    recent version folder (test / beta / PTB builds last)."""
    wanted = {_norm(n) for n in names if _norm(n)}
    files = [p for p in paths if p.lower().endswith(".usmap") and _norm(p.split("/")[0]) in wanted]
    if not files:
        return None
    root = [p for p in files if p.count("/") == 1]
    if root:
        return root[0]

    def rank(p: str):
        folder = p.split("/")[1].lower()
        return (not any(w in folder for w in ("ptb", "test", "beta", "demo", "playtest")), _natural(folder))
    return max(files, key=rank)


def _archive_usmap(info: dict, dest: Path, say=None, cancel=None) -> Path | None:
    import urllib.request
    from urllib.parse import quote
    req = urllib.request.Request(ARCHIVE_TREE, headers={"User-Agent": "omni", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tree = json.loads(r.read())
    paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
    names = [info.get("title") or "", info.get("project") or "", Path(info.get("root") or "").name]
    pick = archive_pick(paths, names)
    if pick is None:
        return None
    from ...core.setup import download
    if say:
        say(f"téléchargement des mappings communautaires ({pick})…")
    download(ARCHIVE_RAW + quote(pick), dest, None, cancel)
    return dest


def _local_usmap(info: dict) -> Path | None:
    """A .usmap put by the user in the game's workspace folder or in the game folder (first levels)."""
    cands = sorted(game_dir(info["id"]).glob("*.usmap"))
    root = Path(info.get("root") or "")
    if root.is_dir():
        for pattern in ("*.usmap", "*/*.usmap", "*/*/*.usmap"):
            cands += sorted(root.glob(pattern))
    return next((p for p in cands if p.name != "archive.usmap"), None)


def mappings(info: dict, say=None, cancel=None) -> str:
    """Property layouts of the game (text), from the cache, else read from its executable; when that fails (protected
    or unusual executable), from a .usmap found locally or in the community archive (TheNaeem/Unreal-Mappings-Archive)."""
    exe = Path(info.get("exe") or "")
    if not exe.is_file():
        raise FileNotFoundError("exécutable du jeu introuvable : les données Unreal ne peuvent pas être lues sans lui")
    st = exe.stat()
    sig = {"exe": str(exe), "size": st.st_size, "mtime": st.st_mtime_ns, "version": 1}
    d = game_dir(info["id"])
    meta, text_file = d / "mappings.json", d / "mappings.txt"
    local = _local_usmap(info)
    if local is not None:
        lst = local.stat()
        sig["usmap"] = [str(local), lst.st_size, lst.st_mtime_ns]
    try:
        if json.loads(meta.read_text(encoding="utf-8")) == sig and text_file.is_file():
            return text_file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        pass
    from ...native import N
    text, problem = "", ""
    if local is None:
        if say:
            say("lecture des structures du jeu dans son exécutable…")
        try:
            text, nstructs, nenums = N.unreal_mappings(str(exe))
            log.info("%s: %d structures, %d enums read from %s", info["id"], nstructs, nenums, exe.name)
            if nstructs < MIN_STRUCTS:
                problem, text = f"seulement {nstructs} structures lues dans l’exécutable", ""
        except Exception as e:  # noqa: BLE001 - any failure falls back to community mappings
            problem = f"lecture de l’exécutable impossible ({e})"
    if not text:
        usmap = local
        if usmap is None:
            log.warning("%s: %s, trying the community mappings", info["id"], problem)
            try:
                usmap = _archive_usmap(info, d / "archive.usmap", say, cancel)
            except OSError as e:
                raise RuntimeError(f"{problem} ; archive de mappings injoignable ({e})") from e
            if usmap is None:
                raise RuntimeError(f"{problem}, et ce jeu n’est pas dans l’archive de mappings communautaire : "
                                   f"place un fichier .usmap (UE4SS / Dumper-7) dans {d}")
        text, nstructs, nenums = N.unreal_usmap(str(usmap))
        log.info("%s: %d structures, %d enums read from %s", info["id"], nstructs, nenums, usmap)
        sig["from"] = str(usmap)
    text_file.write_text(text, encoding="utf-8")
    meta.write_text(json.dumps(sig), encoding="utf-8")
    return text


def open_game(info: dict, say=None):
    """The ``UnrealGame`` of ``info`` (games.identify entry), opened once per process."""
    gid = info["id"]
    with _lock:
        g = _games.get(gid)
        if g is not None:
            return g
        from ...native import N
        ensure_oodle(say)
        text = mappings(info, say)
        g = N.UnrealGame(info["paks"], info.get("version") or "5.1", text, info.get("aes_key", ""))
        _games[gid] = g
        return g


def forget(game_id: str | None = None) -> None:
    with _lock:
        if game_id is None:
            _games.clear()
        else:
            _games.pop(game_id, None)
