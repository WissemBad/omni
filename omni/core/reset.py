"""The two resets: the data omni derived or wrote (exports, conversions, caches, catalogs), and the settings.

Neither touches the other, nor what the user has to give again by hand: the library of games, the downloaded tools and
the names of the resources stay, so a reset never asks for a download or a game folder again. Only files omni made are
removed; the addon folder keeps existing (Garry's Mod's link to it stays valid) but ends up empty.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import CONFIG, Config

_WORKSPACE_DIRS = ("cache", "preview", "sandbox", "reports", "updates")
_GAME_FILES = ("catalog.sqlite", "catalog.sqlite-wal", "catalog.sqlite-shm", "textures.sqlite", "textures.sqlite-wal",
               "textures.sqlite-shm", "characters.json")


def _game_ids(cfg: Config) -> list[str]:
    """Games omni knows: the library, the built-in 007 and those it keeps a workspace folder for."""
    ids = {"007fl"}
    try:
        ids |= {g["id"] for g in json.loads(cfg.games_file.read_text(encoding="utf-8")) if isinstance(g, dict) and g.get("id")}
    except (OSError, ValueError, TypeError):
        pass
    games = cfg.workspace / "games"
    if games.is_dir():
        ids |= {g.name for g in games.iterdir() if g.is_dir()}
    return sorted(ids)


def _export_targets(cfg: Config, game: str) -> list[Path]:
    """What omni writes in a game's export folder, and nothing else: the exports folder can be a folder the user shares
    with other files (setting ``paths.exports``), so an entry omni does not name is never removed."""
    out: list[Path] = []
    addon = cfg.export_dir(game) / "garrysmod-addon"
    if addon.is_dir() and not addon.is_symlink():
        # the addon folder stays (the link Garry's Mod follows keeps working): its content goes
        out += sorted(addon.iterdir())
    out += [cfg.gltf_dir(game), cfg.sounds_dir(game), cfg.textures_dir(game), cfg.blend_dir(game), cfg.gma_path(game)]
    return out


def data_targets(cfg: Config = CONFIG, exports: bool = True) -> list[Path]:
    """What a data reset removes (only what exists)."""
    out = [cfg.workspace / d for d in _WORKSPACE_DIRS]
    games = cfg.workspace / "games"
    for g in sorted(games.glob("*")) if games.is_dir() else []:
        out += [g / n for n in _GAME_FILES]
    if exports and cfg.exports.is_dir():
        for game in _game_ids(cfg):
            out += _export_targets(cfg, game)
    return [p for p in out if p.exists() or p.is_symlink()]


def _remove(p: Path) -> str:
    try:
        if p.is_dir() and not p.is_symlink():
            errors: list[str] = []
            shutil.rmtree(p, onexc=lambda _f, path, exc: errors.append(f"{path}: {exc}"))
            return errors[0] if errors else ""
        p.unlink()
        return ""
    except OSError as e:
        return f"{p}: {e}"


def reset_data(cfg: Config = CONFIG, exports: bool = True, progress=None, cancel=None) -> dict:
    """Remove the exports (unless ``exports`` is false), the conversions and the caches. ``progress(done, total)``
    follows the removal; a file that cannot go (Garry's Mod keeps it open) is reported, the rest still goes."""
    targets = data_targets(cfg, exports)
    errors: list[str] = []
    for i, p in enumerate(targets, 1):
        if cancel is not None and cancel.is_set():
            break
        err = _remove(p)
        if err:
            errors.append(err)
        if progress:
            progress(i, len(targets))
    return {"removed": len(targets) - len(errors), "errors": errors[:50], "failed": len(errors)}


def reset_settings(cfg: Config = CONFIG) -> None:
    """Back to the default settings: ``settings.json`` plus the remembered window and viewer folders."""
    from . import settings
    settings.reset()
    for name in ("window.json", "viewer_roots.json"):
        (cfg.config_dir / name).unlink(missing_ok=True)
