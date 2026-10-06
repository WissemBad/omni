"""Opening an Unreal Engine 5 game with nothing but its folder.

Everything else is fetched or derived here, once, and cached under ``<workspace>/games/<game id>/``:
  * Oodle (``oo2core_9_win64.dll``, Epic's redistributable, SHA-256 pinned) into ``<data>/tools/oodle``;
  * the property layouts ("mappings"), read statically from the game's shipping executable by the Rust core
    (``unreal_mappings``) and cached with the executable's size/mtime;
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
    d = CONFIG.workspace / "games" / game_id
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


def mappings(info: dict, say=None) -> str:
    """Property layouts of the game (text), from the cache or extracted from its executable."""
    exe = Path(info.get("exe") or "")
    if not exe.is_file():
        raise FileNotFoundError("exécutable du jeu introuvable : les données Unreal ne peuvent pas être lues sans lui")
    st = exe.stat()
    sig = {"exe": str(exe), "size": st.st_size, "mtime": st.st_mtime_ns, "version": 1}
    d = game_dir(info["id"])
    meta, text_file = d / "mappings.json", d / "mappings.txt"
    try:
        if json.loads(meta.read_text(encoding="utf-8")) == sig and text_file.is_file():
            return text_file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        pass
    from ...native import N
    if say:
        say("lecture des structures du jeu dans son exécutable…")
    text, nstructs, nenums = N.unreal_mappings(str(exe))
    log.info("%s: %d structures, %d enums read from %s", info["id"], nstructs, nenums, exe.name)
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
