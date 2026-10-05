"""Link / unlink a staged addon into Garry's Mod, and build a .gma."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ...core.config import CONFIG, Config


def link_path(cfg: Config, source_id: str) -> Path:
    return cfg.gmod / "garrysmod" / "addons" / f"omni_{source_id}"


def write_addon_json(addon: Path, source_id: str, title: str) -> None:
    (addon / "addon.json").write_text(json.dumps({
        "title": f"omni - {title}", "type": "model", "tags": ["fun", "roleplay"], "ignore": ["*.psd", "*.vcproj", "*.svn*"],
    }, indent=2))


def deploy(source_id: str, title: str, cfg: Config = CONFIG) -> str:
    addon = cfg.addon_dir(source_id)
    addon.mkdir(parents=True, exist_ok=True)
    write_addon_json(addon, source_id, title)
    lp = link_path(cfg, source_id)
    if lp.exists() or lp.is_symlink():
        if _is_link(lp):
            return f"already linked: {lp} -> {addon}"
        raise RuntimeError(f"{lp} exists and is not a link; refusing to touch it")
    lp.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(lp), str(addon)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return f"linked {lp} -> {addon}"


def _is_link(p: Path) -> bool:
    try:
        return bool(os.readlink(p))
    except OSError:
        return False


def undeploy(source_id: str, cfg: Config = CONFIG) -> str:
    lp = link_path(cfg, source_id)
    if lp.exists() and _is_link(lp):
        os.rmdir(lp)          # removes the junction only, never the target's content
        return f"unlinked {lp}"
    return "nothing to unlink"


def build_gma(source_id: str, cfg: Config = CONFIG) -> Path:
    addon = cfg.addon_dir(source_id)
    out = cfg.workspace / "gma"
    out.mkdir(parents=True, exist_ok=True)
    gmad = cfg.gmod / "bin" / "gmad.exe"
    target = out / f"omni_{source_id}.gma"
    r = subprocess.run([str(gmad), "create", "-folder", str(addon), "-out", str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return target
