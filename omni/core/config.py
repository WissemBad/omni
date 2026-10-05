"""Where omni keeps its data and finds the outside world (the game, Garry's Mod, tools).

Data folder (``Config.root``), first match wins:
  1. ``OMNI_HOME`` (or the older ``OMNI_ROOT``) environment variable;
  2. the folder that contains this checkout when it already holds ``Assets`` or ``workspace`` (development layout);
  3. ``%LOCALAPPDATA%\\omni`` (installed application).

Inside it: ``Assets/Sorted`` (extracted game resources, see core/setup.py), ``workspace`` (catalogs, caches, exports,
settings.json), ``tools`` (downloaded tools). Locations the user can change (game, assets, Garry's Mod, StudioMDL, ffmpeg,
Blender) live in the settings (``paths``) and are applied by ``Config.refresh()``.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FROZEN = bool(getattr(sys, "frozen", False))


def resolve_home() -> Path:
    env = os.environ.get("OMNI_HOME") or os.environ.get("OMNI_ROOT")
    if env:
        return Path(env)
    parent = REPO.parent
    if not FROZEN and ((parent / "Assets").exists() or (parent / "workspace").exists()):
        return parent
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "omni"


ROOT = resolve_home()


def steam_common() -> list[Path]:
    """``steamapps/common`` folders of every Steam library of this PC (registry + libraryfolders.vdf)."""
    from .windows import steam_libraries
    return [lib / "steamapps" / "common" for lib in steam_libraries()]


def find_gmod() -> Path:
    for common in steam_common():
        if (common / "GarrysMod" / "garrysmod").is_dir():
            return common / "GarrysMod"
    return Path(os.environ.get("OMNI_STEAM_COMMON", r"C:\Program Files (x86)\Steam\steamapps\common")) / "GarrysMod"


def find_studiomdl(home: Path, configured: str = "") -> Path:
    cands = [Path(configured)] if configured else []
    cands += [home / "tools" / "studiomdl-ce" / "cestudiomdl.exe",
              home / "third_party" / "studiomdl-ce" / "x64" / "cestudiomdl.exe"]
    return next((c for c in cands if c.is_file()), cands[0] if configured else cands[-2])


@dataclass
class Config:
    root: Path = ROOT
    workspace: Path = field(default_factory=lambda: Path(os.environ.get("OMNI_WORKSPACE", ROOT / "workspace")))
    assets_sorted: Path = field(default_factory=lambda: ROOT / "Assets" / "Sorted")
    gmod: Path = field(default_factory=find_gmod)
    studiomdl: Path = field(default_factory=lambda: find_studiomdl(ROOT))
    ffmpeg: str = field(default_factory=lambda: os.environ.get("OMNI_FFMPEG", "ffmpeg"))
    blender: str = ""
    game: Path | None = None            # folder of the game (holds Runtime/*.rpkg), for the extraction

    @property
    def hash_list(self) -> Path:
        return self.workspace / "names" / "hash_list.txt"

    @property
    def names_db(self) -> Path:
        return self.workspace / "names" / "names.sqlite"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def sandbox(self) -> Path:
        return self.workspace / "sandbox"

    @property
    def cache(self) -> Path:
        return self.workspace / "cache"

    def addon_dir(self, source_id: str) -> Path:
        return self.workspace / "addons" / f"omni_{source_id}"

    def refresh(self, paths: dict | None = None) -> None:
        """Apply the ``paths`` of the settings (empty value = automatic)."""
        p = paths or {}
        self.assets_sorted = Path(p["assets"]) if p.get("assets") else self.root / "Assets" / "Sorted"
        self.gmod = Path(p["gmod"]) if p.get("gmod") else find_gmod()
        self.studiomdl = find_studiomdl(self.root, p.get("studiomdl", ""))
        self.ffmpeg = p.get("ffmpeg") or os.environ.get("OMNI_FFMPEG", "ffmpeg")
        self.blender = p.get("blender", "")
        self.game = Path(p["game"]) if p.get("game") else None

    def refresh_studiomdl(self) -> None:
        self.studiomdl = find_studiomdl(self.root, str(self.studiomdl) if self.studiomdl.is_file() else "")

    def blender_exe(self) -> Path | None:
        """Blender for the optional ``.blend`` output: setting, PATH, then the usual install folders."""
        if self.blender and Path(self.blender).is_file():
            return Path(self.blender)
        found = shutil.which("blender")
        if found:
            return Path(found)
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
        for d in sorted(base.glob("Blender*"), reverse=True) if base.is_dir() else []:
            if (d / "blender.exe").is_file():
                return d / "blender.exe"
        return None


CONFIG = Config()
