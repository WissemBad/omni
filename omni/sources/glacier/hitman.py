"""HITMAN World of Assassination as an omni source: the Glacier source reading the game's packages in place.

Differences with 007 First Light (see hm3.py for the formats): resources come from ``StoreArchive`` (no
extraction), names from glacier-modding/Hitman-Hashes, resource ids have a 0x00 top byte, PRIM / MATI / TEXT
decoders of the HM3 variant. Characters are the outfits of ``characters/assets`` (every weighted part of one
``.wl2`` geometry file), built as playermodels by the generic builder. Prop colour variants (templates) and outfit
families are 007-specific and not offered.

Written from the formats documented by the open-source RPKG-Tool; not verified on the game yet (no copy here).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from ...core.config import CONFIG, Config
from ...core.ir import Material
from ...core.naming import Names, parse_ioi, slug
from . import hm3
from .adapter import GlacierSource
from .store import StoreArchive, packages_of

NAMES_URL = "https://github.com/glacier-modding/Hitman-Hashes/releases/latest/download/latest-hashes.7z"
_CHARACTER_ROOTS = ("hero", "individuals", "workers", "civilians", "guards", "crowd", "elusivetargets", "evergreen")


def runtime_of(info: dict) -> Path:
    from ...core.setup import game_packages
    found = game_packages(info["root"])
    if not found:
        raise FileNotFoundError(f"aucun chunk*.rpkg dans {info['root']}")
    return found[0]


def names_dir(game_id: str) -> Path:
    d = CONFIG.game_dir(game_id) / "names"
    d.mkdir(parents=True, exist_ok=True)
    return d


class HitmanSource(GlacierSource):
    title = "HITMAN World of Assassination"
    description = "Glacier 2 (IO Interactive) · HITMAN 3"
    capabilities = ("props", "characters", "textures", "sounds")
    _parse_prim = staticmethod(hm3.parse_prim)
    _parse_mati = staticmethod(hm3.parse_mati)

    @staticmethod
    def ioi(path: str) -> int:
        v = int.from_bytes(hashlib.md5(path.lower().encode()).digest()[:8], "big")
        return v & 0x00FFFFFFFFFFFFFF

    def __init__(self, info: dict, config: Config = CONFIG):
        self.game = dict(info)
        self.id = info["id"]
        self.title = info.get("title") or self.title
        self.config = config
        self.runtime = runtime_of(info)
        self.packages = packages_of(self.runtime)
        self.archive = StoreArchive(self.packages, text_hook=hm3.bond_text)
        d = names_dir(self.id)
        self.names = Names(d / "hash_list.txt", d / "names.sqlite")
        self._shared = None
        self._characters = None

    # ---- materials: class, blend mode and culling come from the HM3 MATI itself ----------------------------
    def _load_material(self, h: int) -> Material:
        mat = super()._load_material(h)
        path = self.archive.find("MATI", h)
        if path is None:
            return mat
        mt = hm3.parse_mati(path.read_bytes())
        if not mt.ok:
            return mat
        from . import roles
        fam = roles.class_family(getattr(mt, "cls", ""))
        mat.flags = {f for f in mat.flags if not f.startswith("class:")} | {"class:" + fam}
        blend = getattr(mt, "blend", "").lower()
        if getattr(mt, "alpha_test", False):
            mat.flags.add("alpha_test")
        if any(k in blend for k in ("trans", "add", "alphablend")):
            mat.flags.add("translucent")
        if "two" in getattr(mt, "cull", "").lower() or "none" in getattr(mt, "cull", "").lower():
            mat.flags.add("two_sided")
        return mat

    def load_material_variant(self, h: int, params: dict | None = None) -> Material:
        return self.load_material(h)

    def prop_variants(self, h: int) -> list[dict]:
        return []

    def ensure_variants(self) -> None:
        pass

    # ---- skeleton: humanoid bones renamed to the names the playermodel builder knows ------------------------
    def load_rig(self, h: int):
        rig = super().load_rig(h)
        if rig is None:
            return None
        from ...targets.source.humanoid import canonical_names
        from ...targets.source.playermodel import valve_name_for
        if sum(1 for n in rig.names if valve_name_for(n)) < 10:      # not already 007-style names
            rig.names = canonical_names(rig.names, rig.parents, rig.world()[:, :3, 3])
        return rig

    # ---- characters: one per outfit geometry file ---------------------------------------------------------
    def characters(self) -> list[dict]:
        if self._characters is not None:
            return list(self._characters.values())
        cache = self.config.game_dir(self.id) / "characters.json"
        sig = [len(self.archive.index("PRIM")), self._names_sig(), 1]
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data["sig"] == sig:
                self._characters = {c["id"]: c for c in data["items"]}
                return list(self._characters.values())
        except (OSError, ValueError, KeyError):
            pass
        groups: dict[str, dict] = {}
        for h in self.archive.index("PRIM"):
            n = self.names.name(h)
            if ".weightedprim" not in n or "/characters/assets/" not in n:
                continue
            rel = n.split("/characters/assets/", 1)[1]
            root = rel.split("/", 1)[0]
            if root not in _CHARACTER_ROOTS:
                continue
            dirs, cont, leaf = parse_ioi(n)
            if not cont or re.search(r"(^|_)lod\d", leaf):
                continue
            gid = slug(cont, 60)
            g = groups.setdefault(gid, {"id": gid, "title": cont.replace("_", " "), "kind": root,
                                        "mission": dirs[-2] if len(dirs) > 1 else root, "role": "",
                                        "body": "female" if "female" in n else "male", "variants": []})
            g["variants"].append({"v": len(g["variants"]), "key": "%016X" % h, "name": leaf})
        self._characters = {k: v for k, v in sorted(groups.items())}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"sig": sig, "items": list(self._characters.values())}), encoding="utf-8")
        except OSError:
            pass
        return list(self._characters.values())

    def character(self, cid: str) -> dict | None:
        self.characters()
        return self._characters.get(cid)

    def build_character(self, cid: str, opts, cfg: Config = CONFIG, title: str = ""):
        from ...targets.source import pm_build
        c = self.character(cid)
        if c is None:
            raise KeyError(cid)
        o = copy.copy(opts)
        o.lod = 0
        o.name = slug(cid, 40)
        o.title = title or c["title"]
        o.template = "female" if c.get("body") == "female" else "male"
        return pm_build.build_playermodel(self, [v["key"] for v in c["variants"]], o, cfg)

    def preview_character(self, cid: str, out_glb: Path, opts, cfg: Config, tex_size: int = 512) -> dict:
        from ...targets.source import pm_build
        c = self.character(cid)
        o = copy.copy(opts)
        o.lod = 0
        o.name = slug(cid, 40)
        o.template = "female" if c and c.get("body") == "female" else "male"
        return pm_build.export_character_preview(self, [v["key"] for v in (c or {}).get("variants", [])], o, cfg,
                                                 out_glb, tex_size)
