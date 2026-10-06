"""Moving user data: from the layout of earlier versions to the Omni folder, and from one Omni folder to another.

Earlier versions kept everything in one data root (``%LOCALAPPDATA%\\omni`` or the parent of the checkout):
``workspace/`` mixed settings, caches, addons, audio and exports, ``tools/`` held the compiler. ``plan_legacy`` lists
what goes where in the current layout (see ``core/config.py``); ``apply`` performs the moves. Nothing is deleted or
overwritten: an item whose destination already exists is reported and left in place, and a folder is merged child by
child. On one volume a move is a rename (instant, whatever the size).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG, bootstrap_dir, store_home

# (old path under the data root, new path under the Omni folder)
_FIXED = [
    ("workspace/settings.json", "workspace/config/settings.json"),
    ("workspace/games.json", "workspace/config/games.json"),
    ("workspace/viewer_roots.json", "workspace/config/viewer_roots.json"),
    ("workspace/window.json", "workspace/config/window.json"),
    ("workspace/jobs.sqlite", "workspace/state/jobs.sqlite"),
    ("workspace/jobs.sqlite-wal", "workspace/state/jobs.sqlite-wal"),
    ("workspace/jobs.sqlite-shm", "workspace/state/jobs.sqlite-shm"),
    ("workspace/run.json", "workspace/state/run.json"),
    ("workspace/cache", "workspace/cache"),
    ("workspace/names", "workspace/names"),
    ("workspace/logs", "workspace/logs"),
    ("workspace/preview", "workspace/preview"),
    ("workspace/reports", "workspace/reports"),
    ("workspace/sandbox", "workspace/sandbox"),
    ("workspace/webview", "workspace/webview"),
    ("workspace/updates", "workspace/updates"),
    ("workspace/games", "workspace/games"),
]
_TOOLS = ("studiomdl-ce", "oodle")     # what omni downloaded into the old tools folder; any other tool there is the user's
_PER_GAME = [        # (folder under the old workspace holding one entry per game, new folder under exports/<game>)
    ("audio", "sounds"),
    ("blend", "blend"),
]


@dataclass
class Move:
    src: Path
    dst: Path
    note: str = ""


@dataclass
class Report:
    moved: list[Move] = field(default_factory=list)
    skipped: list[Move] = field(default_factory=list)
    errors: list[Move] = field(default_factory=list)
    relinked: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{len(self.moved)} moved, {len(self.skipped)} left in place, {len(self.errors)} failed"


def _same_volume(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a.resolve()))[0].lower() == os.path.splitdrive(str(b.resolve()))[0].lower()


def plan_legacy(old: Path, home: Path) -> list[Move]:
    """What the data root ``old`` holds, mapped into the Omni folder ``home``."""
    out = [Move(old / a, home / b) for a, b in _FIXED if (old / a).exists()]
    out += [Move(old / "tools" / n, home / "workspace" / "tools" / n) for n in _TOOLS if (old / "tools" / n).exists()]
    ws = old / "workspace"
    for f in sorted(ws.glob("catalog_*.sqlite")) + sorted(ws.glob("textures_*.sqlite")):
        m = re.fullmatch(r"(catalog|textures)_(.+)\.sqlite", f.name)
        if m:
            out.append(Move(f, home / "workspace" / "games" / m.group(2) / f"{m.group(1)}.sqlite"))
    for sub, new in _PER_GAME:
        for d in sorted((ws / sub).glob("*")) if (ws / sub).is_dir() else []:
            out.append(Move(d, home / "exports" / d.name / new))
    for d in sorted((ws / "addons").glob("omni_*")) if (ws / "addons").is_dir() else []:
        out.append(Move(d, home / "exports" / d.name.removeprefix("omni_") / "garrysmod-addon", "addon"))
    for d in sorted((ws / "exports").glob("*")) if (ws / "exports").is_dir() else []:
        for child in sorted(d.glob("*")):
            out.append(Move(child, home / "exports" / d.name / child.name))
    for f in sorted((ws / "gma").glob("omni_*.gma")) if (ws / "gma").is_dir() else []:
        out.append(Move(f, home / "exports" / f.stem.removeprefix("omni_") / f.name))
    return out


def _move(src: Path, dst: Path, rep: Report, note: str = "") -> None:
    if not os.path.lexists(src):
        return
    if not dst.exists():
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(src, dst)                       # one volume: a rename, instant
            except OSError:
                shutil.move(str(src), str(dst))
            rep.moved.append(Move(src, dst, note))
        except OSError as e:
            rep.errors.append(Move(src, dst, str(e)))
    elif src.is_dir() and dst.is_dir():
        for child in sorted(src.iterdir()):
            _move(child, dst / child.name, rep)
        try:
            src.rmdir()                                    # empty now (a folder that still holds something stays)
        except OSError:
            pass
    else:
        rep.skipped.append(Move(src, dst, "destination exists"))


def gmod_running() -> bool:
    if os.name != "nt":
        return False
    import subprocess
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq gmod.exe", "/NH"], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "gmod.exe" in r.stdout.lower()


def relink_addons(moves: list[Move], rep: Report, cfg=CONFIG) -> None:
    """Garry's Mod links each addon with a junction: point the ones that followed an addon to its new place."""
    from .windows import is_link, make_junction, remove_junction
    for m in moves:
        if m.note != "addon" or not m.dst.is_dir():
            continue
        lp = cfg.gmod / "garrysmod" / "addons" / m.src.name
        try:
            if lp.exists() and is_link(lp):
                remove_junction(lp)
                make_junction(lp, m.dst)
                rep.relinked.append(f"{lp} -> {m.dst}")
        except (OSError, RuntimeError) as e:
            rep.errors.append(Move(lp, m.dst, f"relink failed: {e}"))


def adopt_legacy_tools(old: Path, home: Path) -> None:
    """The compiler of the development layout (``third_party/studiomdl-ce/x64``) is copied where omni looks for it."""
    legacy = old / "third_party" / "studiomdl-ce" / "x64"
    dest = home / "workspace" / "tools" / "studiomdl-ce"
    if legacy.is_dir() and not (dest / "cestudiomdl.exe").exists():
        shutil.copytree(legacy, dest, dirs_exist_ok=True)


def apply(old: Path, home: Path, dry_run: bool = False, cfg=CONFIG) -> Report:
    """Move the data root ``old`` into the Omni folder ``home``. Whatever cannot move yet is left in place and
    reported; running the same command again moves it (a running Garry's Mod keeps the addon's files open)."""
    rep = Report()
    plan = plan_legacy(old, home)
    if dry_run:
        rep.moved = plan
        return rep
    locked = any(m.note == "addon" for m in plan) and gmod_running()
    for m in plan:
        if locked and m.note == "addon":                   # Garry's Mod keeps the addon's files open: moved by a later run
            rep.skipped.append(Move(m.src, m.dst, "Garry's Mod est ouvert : relance la migration quand il est fermé"))
            continue
        _move(m.src, m.dst, rep, m.note)
    for sub in ("addons", "audio", "blend", "gma", "exports"):          # emptied folders of the old layout
        d = old / "workspace" / sub
        for p in sorted(d.rglob("*"), reverse=True) if d.is_dir() else []:
            if p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass
    relink_addons([m for m in plan if m.dst.exists()], rep, cfg)
    adopt_legacy_tools(old, home)
    marker = home / "workspace" / "state" / "migrated.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"from": str(old), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": rep.summary(),
                                  "left": [str(m.src) for m in rep.skipped + rep.errors]}, indent=1), encoding="utf-8")
    return rep


def legacy_root() -> Path | None:
    """The data root of an earlier installed version (``%LOCALAPPDATA%\\omni`` with a ``workspace`` inside) that
    the current Omni folder does not replace yet."""
    old = bootstrap_dir()
    if (old / "workspace").is_dir() and not (old / "workspace" / "config").exists() and CONFIG.home != old:
        return old
    return None


def migrate_installed() -> Report | None:
    """At start: bring the data of an earlier installed version into the Omni folder (once)."""
    old = legacy_root()
    if old is None or (CONFIG.home / "workspace" / "state" / "migrated.json").exists():
        return None
    return apply(old, CONFIG.home)


def finish_pending() -> Report | None:
    """At start: carry out the move of the Omni folder that was asked for while omni was running."""
    from .config import cancel_home_request, pending_home
    target = pending_home()
    if target is None:
        return None
    try:
        rep = relocate(target)
    except (OSError, RuntimeError):
        cancel_home_request()                   # not retried on every start; the reason is in the log
        raise
    CONFIG.workspace = target / "workspace"
    return rep


def relocate(new_home: Path) -> Report:
    """Move the Omni folder (workspace and exports) to ``new_home`` and remember it."""
    cur = CONFIG.home
    if new_home.resolve() == cur.resolve():
        return Report()
    if new_home.exists() and any(new_home.iterdir()):
        raise RuntimeError(f"{new_home} n’est pas vide")
    rep = Report()
    plan = [Move(cur / "workspace", new_home / "workspace")]
    if CONFIG.exports == cur / "exports":                 # an exports folder set apart stays where the user put it
        plan.append(Move(CONFIG.exports, new_home / "exports"))
    addons = [sub.name for sub in sorted(CONFIG.exports.glob("*")) if (sub / "garrysmod-addon").is_dir()] if CONFIG.exports.is_dir() else []
    if addons and gmod_running():
        raise RuntimeError("Garry's Mod est ouvert : ferme-le avant de déplacer les addons.")
    for m in plan:
        _move(m.src, m.dst, rep)
    store_home(new_home)
    if len(plan) == 2:
        relink_addons([Move(Path(f"omni_{sid}"), new_home / "exports" / sid / "garrysmod-addon", "addon") for sid in addons], rep)
    return rep
