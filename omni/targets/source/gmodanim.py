"""Garry's Mod's own animation models (``m_anm``, ``f_anm``, ``z_anm``): player models include them (``$includemodel``) and
play their sequences. They live inside ``garrysmod_dir.vpk``; the few files that matter are extracted once into the
cache and read from there.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ...core.config import CONFIG

log = logging.getLogger("omni.gmodanim")

MODELS = ("m_anm", "f_anm", "z_anm")
EXTS = (".mdl", ".ani")


def cache_dir() -> Path:
    return CONFIG.cache / "gmod_anims"


def _vpk_path() -> Path:
    return CONFIG.gmod / "garrysmod" / "garrysmod_dir.vpk"


def ensure() -> bool:
    """Extract the animation models of the game into the cache (once per version of the VPK). False without Garry's Mod."""
    src = _vpk_path()
    if not src.is_file():
        return False
    d = cache_dir()
    sig = {"size": src.stat().st_size, "mtime": src.stat().st_mtime_ns, "v": 1}
    marker = d / "sig.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")) == sig and all((d / f"{m}.mdl").is_file() for m in MODELS):
            return True
    except (OSError, ValueError):
        pass
    import vpk
    pak = vpk.open(str(src))
    d.mkdir(parents=True, exist_ok=True)
    for m in MODELS:
        for ext in EXTS:
            name = f"models/{m}{ext}"
            try:
                (d / f"{m}{ext}").write_bytes(pak.get_file(name).read())
            except (KeyError, OSError) as e:
                log.info("%s not in the VPK: %s", name, e)
                (d / f"{m}{ext}").unlink(missing_ok=True)
    marker.write_text(json.dumps(sig), encoding="utf-8")
    return True


def load(name: str, addon: Path | None = None) -> tuple[bytes, bytes | None] | None:
    """(.mdl, .ani or None) of an included model by the name its includer gives (``m_anm.mdl``, ``models/m_anm.mdl``):
    the addon's own copy first, else the extracted one of Garry's Mod."""
    rel = name.replace("\\", "/").lstrip("/")
    if not rel.startswith("models/"):
        rel = "models/" + rel
    stem = rel[:-4] if rel.endswith(".mdl") else rel
    for base in ([addon] if addon else []):
        mdl = base / f"{stem}.mdl"
        if mdl.is_file():
            ani = mdl.with_suffix(".ani")
            return mdl.read_bytes(), ani.read_bytes() if ani.is_file() else None
    leaf = stem.rsplit("/", 1)[-1]
    if leaf in MODELS and ensure():
        d = cache_dir()
        mdl = d / f"{leaf}.mdl"
        if mdl.is_file():
            ani = d / f"{leaf}.ani"
            return mdl.read_bytes(), ani.read_bytes() if ani.is_file() else None
    return None
