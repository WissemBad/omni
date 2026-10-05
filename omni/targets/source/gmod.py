"""Garry's Mod next to omni: is it running (file locks), and open a converted model in it.

Opening: omni writes ``garrysmod/data/omni_spawn.txt`` (``<kind>\\t<model path>``) and starts GMod through Steam on a
map; the addon's ``omni_spawn.lua`` reads the file when the player joins and spawns the prop in front of them (or puts
the playermodel on them). In a game that is already running the same script answers to the console command
``omni_spawn`` (the addon's Lua is loaded when a map starts, so change map once after the first deploy).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ...core.config import CONFIG, Config
from ...core.windows import NOWINDOW

PROCESSES = ("gmod.exe", "hl2.exe")
STEAM_APP = 4000
MAP = "gm_construct"

SPAWN_LUA = '''-- Written by omni. Spawns the model chosen in omni (garrysmod/data/omni_spawn.txt: "<kind><TAB><model path>").
if not SERVER then return end

local function chosen()
    local raw = file.Read("omni_spawn.txt", "DATA")
    if not raw or raw == "" then return end
    local kind, mdl = string.match(raw, "^(%w+)\\t(.+)$")
    if not mdl then return end
    return kind, string.Trim(mdl)
end

local function spawn(ply)
    if not IsValid(ply) then return end
    local kind, mdl = chosen()
    if not mdl then return end
    if not util.IsValidModel(mdl) then
        ply:ChatPrint("omni: model not found: " .. mdl)
        return
    end
    util.PrecacheModel(mdl)
    if kind == "player" then
        ply:SetModel(mdl)
        ply:ChatPrint("omni: playermodel " .. mdl)
        return
    end
    local ent = ents.Create("prop_physics")
    if not IsValid(ent) then return end
    ent:SetModel(mdl)
    ent:SetPos(ply:EyePos() + ply:GetAimVector() * 110)
    ent:Spawn()
    ent:Activate()
    ply:ChatPrint("omni: " .. mdl)
end

hook.Add("PlayerInitialSpawn", "omni_spawn", function(ply)
    timer.Simple(3, function() spawn(ply) end)
end)
concommand.Add("omni_spawn", function(ply) spawn(ply) end)
'''


def running() -> bool:
    """True when Garry's Mod is running (it locks the addon's files: conversions fail while it is open)."""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                             creationflags=NOWINDOW).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(f'"{p}"' in out for p in PROCESSES)


def spawn_lua_path(addon: Path) -> Path:
    return addon / "lua" / "autorun" / "server" / "omni_spawn.lua"


def ensure_spawn_script(addon: Path) -> Path:
    """The Lua script is part of the addon (written once, refreshed when its text changes)."""
    p = spawn_lua_path(addon)
    if not p.exists() or p.read_text(encoding="utf-8") != SPAWN_LUA:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SPAWN_LUA, encoding="utf-8", newline="\n")
    return p


def launch(source_id: str, model: str, kind: str = "prop", cfg: Config = CONFIG) -> dict:
    """Start GMod on a map with ``model`` ready to spawn. ``model`` is ``models/omni/...mdl`` (as the addon names it)."""
    if kind not in ("prop", "player"):
        raise ValueError("kind must be prop or player")
    addon = cfg.addon_dir(source_id)
    if not model.startswith("models/") or not model.endswith(".mdl") or not (addon / model).is_file():
        raise FileNotFoundError(f"modèle introuvable dans l'addon : {model}")
    data = cfg.gmod / "garrysmod" / "data"
    if not (cfg.gmod / "garrysmod").is_dir():
        raise FileNotFoundError("Garry's Mod est introuvable : renseigne son dossier dans les réglages")
    ensure_spawn_script(addon)
    data.mkdir(parents=True, exist_ok=True)
    (data / "omni_spawn.txt").write_text(f"{kind}\t{model}", encoding="utf-8")
    already = running()
    if not already:
        os.startfile(f"steam://run/{STEAM_APP}//+map%20{MAP}")           # noqa: S606 - the Steam protocol handler
    return {"running": already, "map": MAP,
            "hint": ("GMod est déjà ouvert : change de map puis tape « omni_spawn » dans la console." if already
                     else "GMod démarre sur gm_construct : le modèle apparaît devant toi.")}
