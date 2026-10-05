"""User settings, stored in ``workspace/settings.json`` and read by the backend itself (conversions, sound export,
previews, paths), so the web UI, the batch workers and the CLI all apply the same choices.

Values are merged over ``DEFAULTS`` section by section: a new setting gets its default on old files, an unknown or
ill-typed value is ignored.
"""
from __future__ import annotations

import copy
import json
import os
import threading

from .config import CONFIG

CPUS = os.cpu_count() or 8

DEFAULTS: dict = {
    "general": {
        "open_browser": True,        # `omni ui` / the launcher opens the interface
        "port": 8770,
    },
    "paths": {                       # "" = automatic
        "game": "",                  # folder of the game that holds Runtime/*.rpkg (extraction)
        "assets": "",                # extracted resources (Sorted: chunk0/PRIM/...), default <data>/Assets/Sorted
        "gmod": "",                  # Garry's Mod folder (Steam default)
        "studiomdl": "",             # cestudiomdl.exe (default: downloaded into <data>/tools)
        "ffmpeg": "",                # ffmpeg.exe, MP3 and Vorbis re-encoding only
        "blender": "",               # blender.exe, optional .blend output
    },
    "textures": {
        "quality": "max",            # max | high | balanced | light (see pipeline.QUALITY)
        "encoder": 2,                # DXT effort: 0 fast, 1 good, 2 best
        "lossless_normals": False,   # BGRA8888 normal maps (4x heavier, no block artefacts)
    },
    "props": {
        "physics": True,
        "collision": "game",         # game | parts | hull | coacd
        "workers": max(1, min(16, CPUS // 2)),
        "blend": False,
    },
    "characters": {
        "max_tris": 60000,
        "preview_size": 512,         # texture size of the 3D preview of a character
    },
    "sounds": {
        "format": "auto",            # auto | flac | wav | mp3
        "workers": CPUS,
        "tags": True,                # title / album / language... written into each file
        "skip_stubs": True,          # skip bank copies of the first bytes of streamed music
        "languages": "all",          # all | english | neutral
    },
    "viewer": {
        "texture_size": 1024,        # textures of the 3D previews
    },
}

_FILE = CONFIG.workspace / "settings.json"
_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for section, values in (over or {}).items():
        if section not in out or not isinstance(values, dict):
            continue
        for k, v in values.items():
            if k not in out[section]:
                continue
            d = out[section][k]
            if isinstance(d, bool):
                if isinstance(v, bool):
                    out[section][k] = v
            elif isinstance(d, int):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[section][k] = int(v)
            elif isinstance(d, str):
                if isinstance(v, str):
                    out[section][k] = v
    return out


def load() -> dict:
    global _cache
    with _lock:
        try:
            mtime = _FILE.stat().st_mtime if _FILE.exists() else 0.0
            if _cache and _cache[0] == mtime:
                return copy.deepcopy(_cache[1])
            raw = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else {}
        except (OSError, ValueError):
            mtime, raw = 0.0, {}
        merged = _merge(DEFAULTS, raw)
        _cache = (mtime, merged)
        return copy.deepcopy(merged)


def save(patch: dict) -> dict:
    """Merge ``patch`` (any subset of sections/keys) into the stored settings."""
    cur = load()
    for section, values in (patch or {}).items():
        if section in cur and isinstance(values, dict):
            cur[section].update(values)
    merged = _merge(DEFAULTS, cur)
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _FILE)
    apply(merged)
    return merged


def reset() -> dict:
    _FILE.unlink(missing_ok=True)
    s = load()
    apply(s)
    return s


def get(section: str, key: str):
    return load()[section][key]


def apply(s: dict | None = None) -> None:
    """Push settings that live in module globals (paths, encoder effort)."""
    s = s or load()
    CONFIG.refresh(s["paths"])
    try:
        from ..targets.source import textures
        textures.ENCODER_QUALITY = int(s["textures"]["encoder"])
    except Exception:  # noqa: BLE001 - optional dependencies of the texture module
        pass
