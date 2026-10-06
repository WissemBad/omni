"""Glacier 2 source adapter (007 First Light profile).

Everything here is read-only on the extracted ``Assets/Sorted`` tree.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from ...core.config import CONFIG, Config
from ...core.ir import Material, Model, SubMesh, TextureData, TextureRef
from ...core.naming import Names, parse_ioi, slug
from ...native import N
from ..base import Source
from . import roles
from .archive import Archive
from .mati import parse_mati
from .prim import parse_prim
from .texture import decode_mips


@dataclass
class AssetInfo:
    key: str                 # 16-hex hash
    name: str                # IOI name ('' if unknown)
    dirs: list[str]
    container: str
    leaf: str
    size: int
    kind: str = "PRIM"

    @property
    def rel_path(self) -> str:
        """Game-safe relative model path (no extension)."""
        if not self.name:
            return "unnamed/" + self.key.lower()
        parts = [slug(p, 32) for p in self.dirs[-3:]]
        if self.container:
            parts.append(slug(self.container, 40))
        parts.append(slug(self.leaf, 40))
        return "/".join(parts)


_HINT_ROLE = {"ascolormap": "base", "asnormalmap": "normal", "asheightmap": "height", "asmask": "mask",
              "asspecularmap": "spec", "asroughnessmap": "srm", "asemissivemap": "emissive", "ascompoundnormal": "normal"}
_LEAF_ROLE = [("normal", "normal"), ("diffuse", "base"), ("albedo", "base"), ("basecolor", "base"), ("color", "base"),
              ("srm", "srm"), ("spec", "spec"), ("rough", "srm"), ("emissive", "emissive"), ("mask", "mask"),
              ("height", "height"), ("ao", "ao"), ("alpha", "alpha"), ("detail", "detail_normal")]


class GlacierSource(Source):
    id = "007fl"
    title = "007 First Light"
    capabilities = ("props", "characters", "textures", "sounds")
    description = "Glacier 2 (IO Interactive)"
    # format variant of the game (Hitman 3 subclasses override these)
    _parse_prim = staticmethod(parse_prim)
    _parse_mati = staticmethod(parse_mati)

    @staticmethod
    def ioi(path: str) -> int:
        from ...core.naming import ioi_hash
        return ioi_hash(path)

    def __init__(self, config: Config = CONFIG):
        self.config = config
        self.archive = Archive(config.assets_sorted, config.cache)
        if not self.archive.chunks and config.game:
            # no extracted tree but the game is installed: its packages are read in place
            from ...core.setup import game_packages
            from .store import StoreArchive
            found = game_packages(config.game)
            if found:
                self.archive = StoreArchive(found[1])
        self.names = Names(config.hash_list, config.names_db)
        self._shared: frozenset[str] | None = None

    # ---- characters ------------------------------------------------------------
    def characters(self) -> list[dict]:
        """Character browser items, one per outfit family (variations = bodygroup/skin presets):
        {id, title, kind, mission, role, body, variants: [{v, key, name}]}. ``mission`` is the level the outfit
        belongs to (template folder), ``role`` the first word after it when the name has one."""
        if getattr(self, "_characters", None) is None:
            import json
            cache = self.config.cache / f"characters_{self.id}.json"
            sig = self.archive._signature("TEMP") + [len(self.archive.index("TEMP")), self._names_sig()]
            try:
                data = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
                if data and data.get("sig") == sig and data.get("version") == 2:
                    self._characters = {c["id"]: c for c in data["items"]}
                    return list(self._characters.values())
            except (OSError, ValueError):
                pass
            from collections import defaultdict
            from .outfit import OutfitResolver, body_type
            fam = defaultdict(list)
            for f, v, h, n in OutfitResolver(self).outfits():
                if body_type(f):
                    fam[f].append({"v": v, "key": "%016X" % h, "name": n})
            from .naming import bond_variations, known_missions, split_family, variation_of
            from .schema import read_enums
            bodies = {f: body_type(f) for f in fam}
            missions = known_missions(list(fam), bodies)
            variations = bond_variations(read_enums(self))
            out = []
            for f, vs in fam.items():
                body = bodies[f]
                s = split_family(f, body, missions)
                item = {"id": f, "title": s["title"], "kind": s["kind"], "mission": s["mission"], "role": s["role"],
                        "body": body, "reward": s["reward"], "variants": sorted(vs, key=lambda x: x["v"])}
                var = variation_of(f, body, variations) if s["bond"] else None
                if var:                                   # the game's own name of this Bond outfit
                    item["variation"] = var[1]
                    item["reward"] = item["reward"] or "reward" in var[1].lower()
                out.append(item)
            self._characters = {c["id"]: c for c in sorted(out, key=lambda c: (c["mission"], c["role"], c["title"]))}
            try:
                if not self._characters:
                    raise OSError("nothing found: not cached, the next call looks again")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"sig": sig, "version": 2, "items": list(self._characters.values())}),
                                 encoding="utf-8")
            except OSError:
                pass
        return list(self._characters.values())

    def character(self, cid: str) -> dict | None:
        self.characters()
        return self._characters.get(cid)

    # ---- catalog ---------------------------------------------------------------
    def list_sounds(self, progress=print, fresh: bool = False):
        """Every sound (SoundRef) with a readable path, see sources/glacier/audio.py. The list (a minute to build:
        every media header is read) is cached on disk until the audio resources change."""
        import pickle
        from .audio import iter_sounds
        sig = [len(self.archive.index(k)) for k in ("WWES", "WWEM", "WWEV", "WBNK", "DLGE", "WSGB", "WSWB")] + [self._names_sig()]
        cache = self.config.cache / f"sounds_{self.id}.pkl"
        if not fresh and cache.exists():
            try:
                with open(cache, "rb") as f:
                    got_sig, version, refs = pickle.load(f)
                if got_sig == sig and version == 3:
                    progress(f"sound list from cache: {len(refs)} references")
                    return refs
            except Exception:  # noqa: BLE001 - stale or corrupt cache: rebuild
                pass
        refs = iter_sounds(self, progress)
        if hasattr(self.archive, "store"):
            from .store import package_refs
            refs = package_refs(refs, self.archive.packages)
        if not refs:
            return refs                          # an empty list is never cached (names or resources not ready yet)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with open(cache, "wb") as f:
                pickle.dump((sig, 3, refs), f, protocol=pickle.HIGHEST_PROTOCOL)
        except OSError:
            pass
        return refs

    def list_models(self, kind: str = "PRIM"):
        for h, p in self.archive.index(kind).items():
            yield self.info(h)

    def info(self, h: int, kind: str = "PRIM") -> AssetInfo:
        p = self.archive.find(kind, h)
        name = self.names.name(h)
        dirs, cont, leaf = parse_ioi(name) if name else ([], "", "")
        return AssetInfo("%016X" % h, name, dirs, cont, leaf, p.stat().st_size if p else 0, kind)

    def _names_sig(self) -> int:
        """Changes when the names database is rebuilt: lists named from it must be rebuilt too."""
        try:
            return self.names.db_path.stat().st_mtime_ns
        except OSError:
            return 0

    def close(self) -> None:
        self.names.close()

    def rel_path(self, info: AssetInfo) -> str:
        """Output path of a model, unique across the game: several resources can share a readable path (truncated
        names, ``^…_dynamic`` variants, same leaf in two folders), those get the end of their hash appended."""
        rel = info.rel_path
        return f"{rel}_{info.key[-6:].lower()}" if rel in self._shared_rels() else rel

    def _shared_rels(self) -> frozenset[str]:
        if self._shared is not None:
            return self._shared
        import json
        import os
        from collections import Counter
        index = self.archive.index("PRIM")
        db = self.names.db_path
        sig = [len(index), db.stat().st_mtime_ns if db.exists() else 0, 1]
        cache = self.config.cache / f"shared_rels_{self.id}.json"
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data["sig"] == sig:
                self._shared = frozenset(data["rels"])
                return self._shared
        except (OSError, ValueError, KeyError):
            pass
        counts = Counter(self.info(h).rel_path for h in index)
        self._shared = frozenset(r for r, n in counts.items() if n > 1)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"sig": sig, "rels": sorted(self._shared)}), encoding="utf-8")
            os.replace(tmp, cache)
        except OSError:
            pass
        return self._shared

    def catalog_rows(self):
        """Light-weight rows for the catalog: sizes and header flags of every PRIM, read in parallel by the Rust
        core (opening 30,000 files one by one took minutes under a real-time antivirus)."""
        items = list(self.archive.index("PRIM").items())
        heads = N.prim_headers([str(p) for _, p in items])
        self.names.prefetch([h for h, _ in items])
        for (h, _p), head in zip(items, heads):
            name = self.names.name(h)
            dirs, cont, leaf = parse_ioi(name) if name else ([], "", "")
            size, flags = head if head else (0, 0)
            info = AssetInfo("%016X" % h, name, dirs, cont, leaf, size, "PRIM")
            yield {
                "key": info.key, "name": info.name, "rel": self.rel_path(info), "size": info.size,
                "cat": "/".join(info.dirs[:2]) if info.dirs else "unnamed",
                "skinned": bool(flags & 0b1000), "linked": bool(flags & 0b100),
            }

    # ---- materials ---------------------------------------------------------------
    def material_name(self, h: int) -> str:
        n = self.names.name(h)
        base = slug(parse_ioi(n)[2], 36) if n else "mat"
        return f"{base}_{h & 0xFFFFFF:06x}"

    def class_slots(self, h: int) -> dict[str, str]:
        """{slot (lower case): what the material class ``h`` calls it}, read once per class from its MATE resource."""
        memo = self.__dict__.setdefault("_class_slots", {})
        if h not in memo:
            p = self.archive.find("MATE", h)
            try:
                memo[h] = {s.lower(): m for s, m in N.mate_slots(p.read_bytes()) if m} if p is not None else {}
            except (OSError, ValueError):
                memo[h] = {}
        return memo[h]

    def load_material(self, h: int) -> Material:
        """The material instance ``h`` (a fresh copy: callers modify it). Parsed once per process: playermodel
        variants and prop skins ask for the same instances thousands of times."""
        memo = self.__dict__.setdefault("_mat_memo", {})
        hit = memo.get(h)
        if hit is None:
            hit = self._load_material(h)
            if len(memo) >= 20000:
                memo.clear()
            memo[h] = hit
        return copy.deepcopy(hit)

    def _load_material(self, h: int) -> Material:
        path = self.archive.find("MATI", h)
        mat = Material(key="%016X" % h, name=self.material_name(h), source_name=self.names.name(h))
        if path is None:
            mat.flags.add("missing")
            return mat
        meta = self.archive.meta(path)
        mt = self._parse_mati(path.read_bytes())
        if not mt.ok or meta is None:
            mat.flags.add("unparsed")
            return mat
        cls = ""
        slots: dict[str, str] = {}
        for rh, _f in meta.refs:
            nm = self.names.name(rh)
            if ".materialclass" in nm:
                cls = nm
                slots = self.class_slots(rh)
                break
        fam = roles.class_family(cls)
        mat.flags.add("class:" + fam)
        legacy: list = []
        for t in mt.textures:
            if t.ref_index >= len(meta.refs):
                continue
            th = meta.refs[t.ref_index][0]
            tname = self.names.name(th)
            tp = self.archive.find("TEXT", th)
            fmt = ""
            if tp is not None:
                try:
                    from .texture import parse_text_header
                    fmt = parse_text_header(tp.read_bytes()[:0x98]).name
                except Exception:
                    pass
            role = roles.resolve(t.name, fam, tname, fmt, slots.get(t.name.lower(), ""))
            if role in ("emissive", "translucency", "mask", "spec") and "/constants/" in tname:
                continue                            # the class's placeholder (black, grey) for a map this material does not set
            if role == "other" and t.name.lower() not in roles.IGNORED and t.name not in mat.unknown_slots:
                mat.unknown_slots.append(t.name)
            if tp is None:
                continue
            mat.textures.append(TextureRef(role, t.name, "%016X" % th))
            legacy.append((mat.textures[-1], roles.resolve(t.name, fam, tname, fmt)))
        if not any(x.role == "base" for x in mat.textures):
            # the class gave every map another meaning: a texture named like a colour map is still the best base colour
            for ref, guess in legacy:
                if guess == "base":
                    ref.role = "base"
                    break
        for p in mt.params:
            mat.params[p.name] = p.values
        # several normal maps (garment normal + tiled weave/detail arrays): the main one keeps the role
        normals = [x for x in mat.textures if x.role == "normal"]
        if len(normals) > 1:
            pref = next((x for x in normals if x.slot.lower() in ("maptex_normal", "maptexture2dnormal_01")), None)
            if pref is None:
                pref = normals[0]
            for x in normals:
                if x is not pref:
                    x.role = "detail_normal"
        if fam in ("hardalpha", "foliage"):
            mat.flags.add("alpha_test")
        if fam in ("glass",) or "translucent" in cls.lower():
            mat.flags.add("translucent")
        if fam == "decal":
            mat.flags.add("decal")
        return mat

    def load_material_variant(self, h: int, params: dict | None = None) -> Material:
        """The material instance ``h`` with parameter values set by an entity (outfit, kit template).
        Its key/name get a suffix derived from the values, so every variant converts to its own VMT."""
        mat = self.load_material(h)
        vals = {k: ([float(x) for x in v] if isinstance(v, (tuple, list)) else [float(v)])
                for k, v in (params or {}).items()
                if not k.startswith(("m_", "0x")) and isinstance(v, (tuple, list, float, int, bool))}
        # material entities expose some parameters as "<name>_Value" (ConstantColorRGB_04_Value)
        vals = {(k[:-6] if k.endswith("_Value") and k[:-6] in mat.params and k not in mat.params else k): v
                for k, v in vals.items()}
        vals = {k: v for k, v in vals.items() if mat.params.get(k) != v}
        if not vals:
            return mat
        import hashlib
        tag = hashlib.sha1(repr(sorted((k, tuple(round(x, 4) for x in v)) for k, v in vals.items())).encode()).hexdigest()[:6]
        mat.params = {**mat.params, **vals}
        mat.key = f"{mat.key}_{tag}"
        mat.name = f"{mat.name}_{tag}"
        return mat

    # ---- textures ---------------------------------------------------------------
    def texd_of(self, h: int) -> bytes | None:
        tp = self.archive.find("TEXT", h)
        meta = self.archive.meta(tp) if tp else None
        for rh, _f in (meta.refs if meta else []):
            dp = self.archive.find("TEXD", rh)
            if dp is not None:
                return dp.read_bytes()
        return None

    def texture_png(self, key: str, channel: str = "rgb", max_dim: int = 1024, normal: bool = False) -> bytes:
        from ...native import N
        from .texture import parse_text_header
        h = int(key, 16)
        tp = self.archive.find("TEXT", h)
        if tp is None:
            raise FileNotFoundError(f"texture {key} not found")
        text = tp.read_bytes()
        hd = parse_text_header(text[:0x98])
        # the TEXT holds the small mips: the TEXD (big ones) is only read when the preview needs them
        in_text = max(hd.width >> hd.first_text_mip, hd.height >> hd.first_text_mip)
        texd = None if in_text >= min(max_dim, max(hd.width, hd.height)) else self.texd_of(h)
        return N.texture_png(text, texd, channel, max_dim, normal)[0]

    def texture_index(self, progress=print) -> dict:
        """Catalog rows of every TEXT + which materials use them (with the slot and its role) + which meshes use
        those materials. Names: the hash list when it has the texture (with its usage hint ``(ascolormap)``),
        else the first material using it and the slot."""
        import struct
        from ...native import N
        from .store import ResPath
        from .texture import FORMATS
        a = self.archive
        text, mati, prim = a.index("TEXT"), a.index("MATI"), a.index("PRIM")

        text_items = list(text.items())
        heads = []
        if text_items and not isinstance(text_items[0][1], ResPath):       # plain files: one parallel Rust pass
            for (h, p), r in zip(text_items, N.texture_headers([str(p) for _h, p in text_items])):
                heads.append((h, *r[:4], r[4]) if r else (h, 0, 0, "?", 0, 0))
        else:
            for h, p in text_items:
                try:
                    d = p.open("rb").read(0x98)
                    w, hh, fmt, mips = struct.unpack_from("<HHHH", d, 0x0C)
                    heads.append((h, w, hh, FORMATS.get(fmt, ("0x%02X" % fmt, 0))[0], mips, p.stat().st_size))
                except (OSError, struct.error):
                    heads.append((h, 0, 0, "?", 0, 0))
        progress(f"{len(heads)} texture headers read")

        mati_hashes = list(mati)
        parsed = []
        for h, data in zip(mati_hashes, a.read_many("MATI", mati_hashes)):
            try:
                parsed.append((h, self._parse_mati(data) if data is not None else None))
            except Exception:  # noqa: BLE001
                parsed.append((h, None))
        mati_refs = a.flagged_refs("MATI", mati_hashes)
        materials, tex_mat = [], []
        for h, m in parsed:
            refs = mati_refs.get(h, [])
            cls = next((self.names.name(rh) for rh, _f in refs if ".materialclass" in self.names.name(rh)), "")
            fam = roles.class_family(cls)
            materials.append({"key": "%016X" % h, "name": self.material_name(h), "cls": fam,
                              "source": self.names.name(h)})
            if m is None or not m.ok:
                continue
            for t in m.textures:
                if t.ref_index >= len(refs):
                    continue
                th = refs[t.ref_index][0]
                if th not in text:
                    continue
                tex_mat.append(("%016X" % th, "%016X" % h, t.name, roles.resolve(t.name, fam, self.names.name(th), "")))
        progress(f"{len(materials)} materials, {len(tex_mat)} texture slots")

        paths = list(prim.items())
        refs_all = a.refs_many([p for _h, p in paths]) if hasattr(a, "refs_many") else             N.meta_refs_many([str(p) for _h, p in paths])
        mat_model = []
        for (h, _p), refs in zip(paths, refs_all):
            for rh in refs or []:
                if rh in mati:
                    mat_model.append(("%016X" % rh, "%016X" % h))
        progress(f"{len(mat_model)} mesh/material links")

        texd_size: dict[int, int] = {}
        text_refs = a.flagged_refs("TEXT", [h for h, _p in text_items])
        for h, p in text_items:
            for rh, _f in text_refs.get(h, []):
                dp = a.find("TEXD", rh)
                if dp is not None:
                    try:
                        texd_size[h] = dp.stat().st_size
                    except OSError:
                        pass
                    break

        first_use: dict[str, tuple[str, str, str]] = {}
        mat_names = {m["key"]: m["name"] for m in materials}
        for tk, mk, slot, role in tex_mat:
            first_use.setdefault(tk, (mat_names.get(mk, mk), slot, role))
        hint_rx = re.compile(r"\]\((as[a-z]+)\)")
        rows = []
        for h, w, hh, fmt, mips, size in heads:
            key = "%016X" % h
            name = self.names.name(h)
            role, folder, title = "", "unnamed", ""
            if name:
                dirs, cont, leaf = parse_ioi(name)
                hint = hint_rx.search(name)
                # the file name is more specific than the usage hint (an emissive map is loaded "as colour")
                role = next((r for k, r in _LEAF_ROLE if k in leaf.lower() and r not in ("base",)), "")
                if not role:
                    role = _HINT_ROLE.get(hint.group(1), "") if hint else ""
                if not role:
                    role = next((r for k, r in _LEAF_ROLE if k in leaf.lower()), "")
                folder = "/".join(dirs[:3]) if dirs else "misc"
                title = f"{cont}/{leaf}" if cont else leaf
            use = first_use.get(key)
            if use and not role:
                role = use[2]
            if not title and use:
                title = f"{use[0]} · {use[1]}"
            rows.append({"key": key, "name": title or key.lower(), "folder": folder, "fmt": fmt, "width": w,
                         "height": hh, "mips": mips, "bytes": size + texd_size.get(h, 0), "role": role or "other",
                         "named": bool(name)})
        return {"textures": rows, "materials": materials, "tex_mat": tex_mat, "mat_model": mat_model}

    def load_texture(self, h: int) -> TextureData | None:
        tp = self.archive.find("TEXT", h)
        if tp is None:
            return None
        meta = self.archive.meta(tp)
        texd = None
        if meta:
            for rh, _f in meta.refs:
                dp = self.archive.find("TEXD", rh)
                if dp is not None:
                    texd = dp.read_bytes()
                    break
        hd, mips = decode_mips(tp.read_bytes(), texd)
        return TextureData("%016X" % h, hd.name, hd.width, hd.height, [(m.width, m.height, m.data) for m in mips])

    # ---- models ------------------------------------------------------------------
    def material_refs(self, h: int) -> list[int]:
        """Full reference list of the PRIM; ``material_id`` indexes it directly (BORG refs included)."""
        path = self.archive.find("PRIM", h)
        meta = self.archive.meta(path) if path else None
        return [rh for rh, _f in (meta.refs if meta else [])]

    def load_model(self, h: int, lod: int = 0) -> tuple[Model, dict[str, Material]]:
        path = self.archive.find("PRIM", h)
        if path is None:
            raise FileNotFoundError("PRIM %016X not found" % h)
        info = self.info(h)
        prim = self._parse_prim(path.read_bytes())
        mrefs = self.material_refs(h)
        materials: dict[str, Material] = {}
        subs: list[SubMesh] = []
        warnings: list[str] = []
        # lod_mask is a bitmask of the LOD levels a mesh belongs to. Take the requested level, or the
        # closest lower one that exists (some objects only have LOD0..1), else the best one available.
        bits = sorted({b for m in prim.meshes if m.lod_mask for b in range(8) if (m.lod_mask >> b) & 1})
        below = [b for b in bits if b <= lod]
        best_lod = max(below) if below else (bits[0] if bits else 0)
        for i, m in enumerate(prim.meshes):
            if m.lod_mask and not (m.lod_mask >> best_lod) & 1:
                continue                      # another LOD of the same object: skip
            if len(m.indices) < 3 or len(m.positions) == 0:
                continue
            if m.material_id < len(mrefs) and (self.names.type_of(mrefs[m.material_id]) == "MATI"
                                               or self.archive.find("MATI", mrefs[m.material_id])):
                mk = "%016X" % mrefs[m.material_id]
                if mk not in materials:
                    materials[mk] = self.load_material(mrefs[m.material_id])
            else:
                mk = ""
                warnings.append(f"mesh {i}: material index {m.material_id} is not a MATI reference ({len(mrefs)} refs)")
            subs.append(SubMesh(
                positions=m.positions, normals=m.normals, uvs=m.uvs, indices=m.indices,
                colors=m.colors, tangents=m.tangents, joints=m.joints, weights=m.weights,
                material_key=mk, lod_mask=m.lod_mask, zbias=m.zbias,
            ))
        model = Model(key=info.key, name=self.rel_path(info), submeshes=subs, skinned=prim.weighted, warnings=warnings,
                      lod=best_lod, lods=bits or [0])
        model.rig = self.load_rig(h) if prim.weighted else None
        return model, materials

    def collision_refs(self, h: int) -> dict:
        """ALOC resources of a mesh, found by name: ``[<prim path>].coll`` (static) and
        ``[<prim path>^<leaf>_dynamic.prim].coll`` (the shape used when the object is simulated)."""
        name = self.names.name(h)
        out = {}
        if not name or "]" not in name:
            return out
        base = name[: name.rfind("]") + 1]                       # "[assembly:/.../leaf.prim]"
        leaf = base[:-1].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for kind, path in (("static", base + ".coll"), ("dynamic", f"{base[:-1]}^{leaf}_dynamic.prim].coll")):
            r = self.ioi(path)
            if self.archive.find("ALOC", r) is not None:
                out[kind] = r
        return out

    def ensure_variants(self) -> None:
        """Build the mesh -> templates index now (the batch workers then only read it)."""
        self.prop_variants(0)
        self._variants.ensure()

    def prop_variants(self, h: int) -> list[dict]:
        """Material variants the game's templates apply to this mesh (see variants.py)."""
        if getattr(self, "_variants", None) is None:
            from ...core.config import CONFIG
            from .variants import PropVariants
            self._variants = PropVariants(self, CONFIG.cache)
        return self._variants.variants(h)

    def load_collision(self, h: int, prefer_dynamic: bool = True) -> dict | None:
        """The game's collision shapes for a mesh (native parser required), or None."""
        from ...native import N
        refs = self.collision_refs(h)
        key = (refs.get("dynamic") or refs.get("static")) if prefer_dynamic else (refs.get("static") or refs.get("dynamic"))
        if key is None:
            return None
        try:
            c = N.parse_collision(self.archive.find("ALOC", key).read_bytes())
        except ValueError:
            return None
        c["resource"] = "%016X" % key
        c["dynamic"] = key == refs.get("dynamic")
        return c if c["shapes"] else None

    def load_rig(self, h: int):
        """The part's own skeleton (every weighted PRIM references a BORG)."""
        from .borg import parse_borg
        path = self.archive.find("PRIM", h)
        meta = self.archive.meta(path) if path else None
        for rh, _f in (meta.refs if meta else []):
            bp = self.archive.find("BORG", rh)
            if bp is not None:
                return parse_borg(bp.read_bytes())
        return None
