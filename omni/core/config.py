"""Project-wide paths. Everything is derived from the project root, overridable by env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("OMNI_ROOT", Path(__file__).resolve().parents[3]))


def _steam_common() -> Path:
    return Path(os.environ.get("OMNI_STEAM_COMMON", r"C:\Program Files (x86)\Steam\steamapps\common"))


@dataclass
class Config:
    root: Path = ROOT
    workspace: Path = field(default_factory=lambda: Path(os.environ.get("OMNI_WORKSPACE", ROOT / "workspace")))
    assets_sorted: Path = field(default_factory=lambda: ROOT / "Assets" / "Sorted")
    hash_list: Path = field(default_factory=lambda: ROOT / "workspace" / "names" / "hash_list.txt")
    names_db: Path = field(default_factory=lambda: ROOT / "workspace" / "names" / "names.sqlite")
    studiomdl: Path = field(default_factory=lambda: ROOT / "third_party" / "studiomdl-ce" / "x64" / "cestudiomdl.exe")
    gmod: Path = field(default_factory=lambda: _steam_common() / "GarrysMod")
    # audio tools: vgmstream decodes every game codec, ww2ogg rebuilds standard Ogg Vorbis from Wwise Vorbis
    vgmstream: Path = field(default_factory=lambda: ROOT / "third_party" / "vgmstream" / "vgmstream-cli.exe")
    ww2ogg: Path = field(default_factory=lambda: ROOT / "third_party" / "ww2ogg" / "ww2ogg.exe")
    ww2ogg_codebooks: Path = field(default_factory=lambda: ROOT / "third_party" / "ww2ogg" / "packed_codebooks_aoTuV_603.bin")
    ffmpeg: str = field(default_factory=lambda: os.environ.get("OMNI_FFMPEG", "ffmpeg"))

    @property
    def sandbox(self) -> Path:
        return self.workspace / "sandbox"

    @property
    def cache(self) -> Path:
        return self.workspace / "cache"

    def addon_dir(self, source_id: str) -> Path:
        return self.workspace / "addons" / f"omni_{source_id}"


CONFIG = Config()
