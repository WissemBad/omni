"""Where omni keeps its data and finds the outside world (the game, Garry's Mod, tools).

The application (this checkout, or the installed program) holds no user data. Everything the user owns or derives
lives in one **Omni folder** (``Config.home``), split in two:

``workspace/``  what the application manages, nothing the user needs to open::

    config/      settings.json, games.json, viewer_roots.json, window.json
    state/       jobs.sqlite (the queue and its history), run.json
    games/<id>/  per-game catalogs and caches
    tools/       StudioMDL-CE, Oodle (downloaded, pinned by SHA-256)
    names/ cache/ sandbox/ preview/ logs/ reports/ updates/ webview/

``exports/``    what the user takes away, one folder per game::

    <id>/garrysmod-addon/   the Garry's Mod addon (linked into garrysmod/addons)
    <id>/gltf/ sounds/ textures/ blend/   models for Blender, audio, raw textures
    <id>/omni_<id>.gma      the workshop archive

The Omni folder, first match wins: ``OMNI_HOME`` (or the older ``OMNI_ROOT``) environment variable; the choice stored
in ``%LOCALAPPDATA%\\omni\\home.json`` (set from the settings or ``omni home set``); ``Documents\\Omni``. The
exports folder can also be moved on its own (setting ``paths.exports``), and the locations of the game, extracted
assets, Garry's Mod, StudioMDL, ffmpeg and Blender are settings too, applied by ``Config.refresh()``.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FROZEN = bool(getattr(sys, "frozen", False))


def bootstrap_dir() -> Path:
    """Tiny per-user folder that only remembers where the Omni folder is (and holds data of older versions)."""
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "omni"


def default_home() -> Path:
    from .windows import documents_dir
    return documents_dir() / "Omni"


def stored_home() -> Path | None:
    value = _home_file().get("home")
    return Path(value) if isinstance(value, str) and value else None


def resolve_home() -> Path:
    env = os.environ.get("OMNI_HOME") or os.environ.get("OMNI_ROOT")
    if env:
        return Path(env)
    return stored_home() or default_home()


def _home_file() -> dict:
    try:
        data = json.loads((bootstrap_dir() / "home.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_home_file(data: dict) -> None:
    f = bootstrap_dir() / "home.json"
    if not data:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def store_home(path: Path | None) -> None:
    """Remember the Omni folder for the next starts (``None`` goes back to the default)."""
    data = _home_file()
    data.pop("pending", None)
    if path is None:
        data.pop("home", None)
    else:
        data["home"] = str(path)
    _write_home_file(data)


def pending_home() -> Path | None:
    """A move of the Omni folder asked for while omni was running: done at the next start, before any file is open."""
    value = _home_file().get("pending")
    return Path(value) if isinstance(value, str) and value else None


def request_home(path: Path) -> None:
    data = _home_file()
    data["pending"] = str(path)
    _write_home_file(data)


def cancel_home_request() -> None:
    data = _home_file()
    data.pop("pending", None)
    _write_home_file(data)


HOME = resolve_home()


def steam_common() -> list[Path]:
    """``steamapps/common`` folders of every Steam library of this PC (registry + libraryfolders.vdf)."""
    from .windows import steam_libraries
    return [lib / "steamapps" / "common" for lib in steam_libraries()]


def find_gmod() -> Path:
    for common in steam_common():
        if (common / "GarrysMod" / "garrysmod").is_dir():
            return common / "GarrysMod"
    return Path(os.environ.get("OMNI_STEAM_COMMON", r"C:\Program Files (x86)\Steam\steamapps\common")) / "GarrysMod"


def find_studiomdl(tools: Path, configured: str = "") -> Path:
    default = tools / "studiomdl-ce" / "cestudiomdl.exe"
    return Path(configured) if configured and Path(configured).is_file() else default


@dataclass
class Config:
    workspace: Path = field(default_factory=lambda: Path(os.environ.get("OMNI_WORKSPACE") or HOME / "workspace"))
    exports_override: Path | None = None
    assets_override: Path | None = None
    gmod: Path = field(default_factory=find_gmod)
    studiomdl: Path = field(default_factory=lambda: find_studiomdl(HOME / "workspace" / "tools"))
    ffmpeg: str = field(default_factory=lambda: os.environ.get("OMNI_FFMPEG", "ffmpeg"))
    blender: str = ""
    game: Path | None = None            # folder of the game (holds Runtime/*.rpkg), for the extraction

    # ------------------------------------------------------------------------------------------ roots
    @property
    def home(self) -> Path:
        return self.workspace.parent

    @property
    def exports(self) -> Path:
        return self.exports_override or self.home / "exports"

    @property
    def assets_sorted(self) -> Path:
        """Extracted game resources (``Sorted/chunk0/PRIM/...``), only for a game that is extracted instead of read in place."""
        return self.assets_override or self.workspace / "assets" / "Sorted"

    # ------------------------------------------------------------------------------------ workspace
    @property
    def config_dir(self) -> Path:
        return self.workspace / "config"

    @property
    def state_dir(self) -> Path:
        return self.workspace / "state"

    @property
    def tools(self) -> Path:
        return self.workspace / "tools"

    @property
    def sandbox(self) -> Path:
        return self.workspace / "sandbox"

    @property
    def cache(self) -> Path:
        return self.workspace / "cache"

    @property
    def logs(self) -> Path:
        return self.workspace / "logs"

    @property
    def previews(self) -> Path:
        return self.workspace / "preview"

    @property
    def reports(self) -> Path:
        return self.workspace / "reports"

    @property
    def names_dir(self) -> Path:
        return self.workspace / "names"

    @property
    def hash_list(self) -> Path:
        return self.names_dir / "hash_list.txt"

    @property
    def names_db(self) -> Path:
        return self.names_dir / "names.sqlite"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def games_file(self) -> Path:
        return self.config_dir / "games.json"

    @property
    def jobs_db(self) -> Path:
        return self.state_dir / "jobs.sqlite"

    def game_dir(self, source_id: str) -> Path:
        """Catalogs and caches of one game (never shared between games)."""
        return self.workspace / "games" / source_id

    # -------------------------------------------------------------------------------------- exports
    def export_dir(self, source_id: str) -> Path:
        return self.exports / source_id

    def addon_dir(self, source_id: str) -> Path:
        return self.export_dir(source_id) / "garrysmod-addon"

    def sounds_dir(self, source_id: str) -> Path:
        return self.export_dir(source_id) / "sounds"

    def gltf_dir(self, source_id: str) -> Path:
        return self.export_dir(source_id) / "gltf"

    def textures_dir(self, source_id: str) -> Path:
        return self.export_dir(source_id) / "textures"

    def blend_dir(self, source_id: str) -> Path:
        return self.export_dir(source_id) / "blend"

    def gma_path(self, source_id: str) -> Path:
        return self.export_dir(source_id) / f"omni_{source_id}.gma"

    def addon_dirs(self) -> list[Path]:
        """Every game's addon that exists (the sandbox mounts them to preview converted materials)."""
        base = self.exports
        return sorted(d / "garrysmod-addon" for d in base.glob("*") if (d / "garrysmod-addon").is_dir()) if base.is_dir() else []

    # ----------------------------------------------------------------------------------- locations
    def refresh(self, paths: dict | None = None) -> None:
        """Apply the ``paths`` of the settings (empty value = automatic)."""
        p = paths or {}
        self.assets_override = Path(p["assets"]) if p.get("assets") else None
        self.exports_override = Path(p["exports"]) if p.get("exports") else None
        self.gmod = Path(p["gmod"]) if p.get("gmod") else find_gmod()
        self.studiomdl = find_studiomdl(self.tools, p.get("studiomdl", ""))
        self.ffmpeg = p.get("ffmpeg") or os.environ.get("OMNI_FFMPEG", "ffmpeg")
        self.blender = p.get("blender", "")
        self.game = Path(p["game"]) if p.get("game") else None

    def refresh_studiomdl(self) -> None:
        self.studiomdl = find_studiomdl(self.tools, str(self.studiomdl) if self.studiomdl.is_file() else "")

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
