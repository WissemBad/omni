"""Known game sources. A source is added here and nowhere else: the CLI, the batch workers and the web UI all
resolve it by id. Sources are imported lazily (a source may need heavy dependencies) and instantiated once.

A source class exposes (see sources/glacier/adapter.py for the reference implementation):
    id, title                 identity
    capabilities              subset of ("props", "characters", "sounds"): which workbenches the UI offers
    catalog_rows()            props catalog           -> core.catalog.Catalog
    load_model / load_material / load_texture         -> neutral IR (core.ir)
    characters()              character browser items -> see GlacierSource.characters
    list_sounds()             SoundRef list           -> targets.audio.export
"""
from __future__ import annotations

import importlib

SOURCES: dict[str, tuple[str, str]] = {
    "007fl": ("omni.sources.glacier.adapter", "GlacierSource"),
}
ALIASES = {"glacier": "007fl"}

_instances: dict[str, object] = {}


def resolve(source_id: str) -> str:
    sid = ALIASES.get(source_id, source_id)
    if sid not in SOURCES:
        raise KeyError(f"unknown source '{source_id}' (known: {', '.join(SOURCES)})")
    return sid


def source_class(source_id: str):
    module, cls = SOURCES[resolve(source_id)]
    return getattr(importlib.import_module(module), cls)


def get_source(source_id: str):
    sid = resolve(source_id)
    if sid not in _instances:
        _instances[sid] = source_class(sid)()
    return _instances[sid]


def reset() -> None:
    """Forget the instances: a source is rebuilt (new assets folder, new names) at its next use."""
    _instances.clear()


def source_ids() -> list[str]:
    return list(SOURCES)
