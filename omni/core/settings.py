"""User settings, stored in ``<workspace>/config/settings.json`` and read by the backend itself (conversions, sound export,
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
        "namespace": "omni",         # addon folder of models and materials: models/<namespace>/<game>/ (free path, e.g. wissem/omni)
    },
    "paths": {                       # "" = automatic
        "game": "",                  # folder of the game that holds Runtime/*.rpkg (extraction)
        "assets": "",                # extracted resources (Sorted: chunk0/PRIM/...), default <workspace>/assets/Sorted
        "exports": "",               # where the exports go (default <Omni folder>/exports)
        "gmod": "",                  # Garry's Mod folder (Steam default)
        "studiomdl": "",             # cestudiomdl.exe (default: downloaded into <workspace>/tools)
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
        "lods": True,                # the game's lower levels of detail become $lod models; off: only the highest level is kept
        "collision": "game",         # game | parts | hull | coacd
        "workers": max(1, min(16, CPUS // 2)),
        "blend": False,
        "gltf": False,               # also a .glb per model (exports/<game>/gltf)
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
    "updates": {
        "check": True,               # look for a newer release at launch (GitHub)
        "token": "",                 # read access to the release when the repository is private
    },
}

_lock = threading.Lock()
_cache: tuple[tuple[str, float], dict] | None = None


def _file():
    return CONFIG.settings_file


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
            f = _file()
            stamp = (str(f), f.stat().st_mtime if f.exists() else 0.0)
            if _cache and _cache[0] == stamp:
                return copy.deepcopy(_cache[1])
            raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            stamp, raw = ("", 0.0), {}
        merged = _merge(DEFAULTS, raw)
        _cache = (stamp, merged)
        return copy.deepcopy(merged)


_save_lock = threading.Lock()


def _bounded(section: dict) -> dict:
    """Keep numeric settings in a range the machine accepts (0 or 500 workers would hang or crash a batch)."""
    for key, (lo, hi) in {"workers": (1, 61), "port": (1024, 65535)}.items():
        if key in section:
            try:
                section[key] = max(lo, min(hi, int(section[key])))
            except (TypeError, ValueError):
                section.pop(key)
    return section


def save(patch: dict) -> dict:
    """Merge ``patch`` (any subset of sections/keys) into the stored settings."""
    with _save_lock:                       # concurrent PUTs would otherwise overwrite each other's keys
        cur = load()
        for section, values in (patch or {}).items():
            if section in cur and isinstance(values, dict):
                cur[section].update(_bounded(dict(values)))
        merged = _merge(DEFAULTS, cur)
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    apply(merged)
    return merged


def reset() -> dict:
    _file().unlink(missing_ok=True)
    s = load()
    apply(s)
    return s


def get(section: str, key: str):
    return load()[section][key]


def apply(s: dict | None = None) -> None:
    """Push settings that live in module globals (paths, encoder effort)."""
    s = s or load()
    CONFIG.refresh(s["paths"])
    from .config import clean_namespace
    CONFIG.namespace = clean_namespace(s["general"]["namespace"])
    try:
        from ..targets.source import textures
        textures.ENCODER_QUALITY = int(s["textures"]["encoder"])
    except Exception:  # noqa: BLE001 - optional dependencies of the texture module
        pass
