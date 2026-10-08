"""Extract only the models named in a text file, into an addon of their own.

The usual workflow: export everything, sort through it, note the ``.mdl`` of the models worth keeping (one per line)
and have omni extract just those, with their textures, into a small addon instead of a 50 GB one.

A line is a path as Garry's Mod knows it (``models/<namespace>/<game>/props/chair.mdl``). It is traced back to what it
was made from without needing the export: the game is the folder after the namespace, the rest is the reverse of the
naming the conversion uses (the prop catalog for props, the outfit family for playermodels). The models are then
converted again, with the current settings, into the folder the user chose.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import settings
from .core.naming import slug
from .native import N


@dataclass
class Entry:
    model: str                 # models/....mdl as read from the list
    source: str = ""           # game id
    kind: str = ""             # prop | character
    key: str = ""              # prop key, or character (outfit family) id
    title: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def parse(text: str) -> list[str]:
    """The models of a text file, normalised (``models/<path>.mdl``, lower case), once each, in order."""
    return N.parse_model_list(text)


def _character_names(src) -> dict[str, dict]:
    """``slug`` of the model file name -> character, the way the playermodel builders name their files."""
    out = {}
    for c in src.characters():
        if hasattr(src, "build_character"):
            name = c["variants"][0]["name"] if c.get("variants") else c["id"]
        else:
            name = c["id"].replace("outfit_", "")
        out.setdefault(slug(name, 40), c)
    return out


def resolve(lines: list[str], *, ids: list[str], source_of, catalog_of, default: str = "") -> list[Entry]:
    """Trace every model path back to its source. ``source_of(id)`` and ``catalog_of(id)`` give a game's source and
    prop catalog (built when empty); ``default`` is tried first when the path names no game."""
    wanted = [i for i in ids if "props" in getattr(source_of(i), "capabilities", ()) or "characters" in getattr(source_of(i), "capabilities", ())]
    chars: dict[str, dict] = {}
    cats: dict[str, object] = {}

    def character(sid: str, inner: str):
        if sid not in chars:
            src = source_of(sid)
            chars[sid] = _character_names(src) if "characters" in getattr(src, "capabilities", ()) else {}
        return chars[sid].get(inner[3:])

    def prop(sid: str, inner: str):
        if "props" not in getattr(source_of(sid), "capabilities", ()):
            return None
        if sid not in cats:
            cat = catalog_of(sid)
            if not cat.count():
                cat.build()
            cats[sid] = cat
        from .targets.source.build import prop_of_path
        return prop_of_path(cats[sid], sid, inner)

    def find(sid: str, inner: str) -> Entry | None:
        if inner.startswith("pm/"):
            c = character(sid, inner)
            return Entry("", sid, "character", c["id"], c.get("title", c["id"])) if c else None
        r = prop(sid, inner)
        return Entry("", sid, "prop", r["key"], r["rel"].rsplit("/", 1)[-1]) if r else None

    out: list[Entry] = []
    order = ([default] if default in wanted else []) + [i for i in wanted if i != default]
    for model in lines:
        segs = model[:-4].split("/")
        if segs and segs[0] == "models":
            segs = segs[1:]
        found = None
        named = [i for i, s in enumerate(segs) if s in wanted]
        if named:                                    # models/<namespace>/<game>/<inner>
            sid = segs[named[0]]
            found = find(sid, "/".join(segs[named[0] + 1:]))
        else:                                        # no game in the path: every namespace depth, every game
            for sid in order:
                for j in range(len(segs)):
                    found = find(sid, "/".join(segs[j:]))
                    if found:
                        break
                if found:
                    break
        if found is None:
            out.append(Entry(model, error="modèle introuvable dans les jeux de la bibliothèque"))
        else:
            found.model = model
            out.append(found)
    return out


def summary(entries: list[Entry]) -> dict:
    per_game: dict[str, dict] = {}
    for e in entries:
        if e.ok:
            g = per_game.setdefault(e.source, {"props": 0, "characters": 0})
            g["props" if e.kind == "prop" else "characters"] += 1
    return {"total": len(entries), "found": sum(e.ok for e in entries), "missing": sum(not e.ok for e in entries),
            "games": per_game}


def run(entries: list[Entry], out: Path, *, titles: dict[str, str] | None = None, on_result=None, cancel=None,
        on_plan=None, say=print, workers: int | None = None) -> dict:
    """Convert the resolved entries into the addon folder ``out`` with the current settings."""
    from .pipeline import clamp_workers, run_batch, run_pm_batch
    from .targets.source.deploy import write_addon_json
    st = settings.load()
    settings.apply(st)
    out.mkdir(parents=True, exist_ok=True)
    stats: dict = {"props": {}, "characters": {}}
    games = sorted({e.source for e in entries if e.ok})
    for sid in games:
        if cancel is not None and cancel.is_set():
            break
        keys = [e.key for e in entries if e.ok and e.source == sid and e.kind == "prop"]
        fams = [e.key for e in entries if e.ok and e.source == sid and e.kind == "character"]
        if keys:
            say(f"{sid} : {len(keys)} prop(s)")
            stats["props"][sid] = run_batch(
                sid, keys, clamp_workers(workers or st["props"]["workers"]), st["props"]["physics"], on_result=on_result,
                collision=st["props"]["collision"], lossless_normals=st["textures"]["lossless_normals"],
                tex_quality=st["textures"]["quality"], lods=st["props"]["lods"], cancel=cancel, on_plan=on_plan, addon=str(out))
        if fams and not (cancel is not None and cancel.is_set()):
            say(f"{sid} : {len(fams)} playermodel(s)")
            from .ui.routes_models import PM_WORKERS
            stats["characters"][sid] = run_pm_batch(
                sid, fams, min(clamp_workers(st["props"]["workers"]), PM_WORKERS),
                {"max_tris": st["characters"]["max_tris"], "tex_quality": st["textures"]["quality"],
                 "lossless_normals": st["textures"]["lossless_normals"], "addon": str(out)},
                on_result=on_result, cancel=cancel)
    title = " + ".join((titles or {}).get(g, g) for g in games) or "sélection"
    write_addon_json(out, games[0] if games else "omni", f"{title} (sélection)")
    stats["folder"] = str(out)
    return stats



