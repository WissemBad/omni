"""What a game source provides. A new game (Hitman, an Unreal game through glTF...) subclasses ``Source``, fills in
the capabilities it can offer and registers itself in ``registry.py``; the CLI, the batch workers and the whole web
interface then work with it unchanged.

Everything a source returns is either the neutral IR of ``core/ir.py`` (models, materials, textures, sounds) or
plain JSON-able dicts described below. Keys are strings chosen by the source (Glacier uses 16-hex hashes).

Capabilities (``capabilities`` tuple), and what each one requires:

  props        catalog_rows(), load_model(key), load_material(key), load_texture(key);
               optional: prop_variants(key), load_collision(key), material_name(key)
  characters   characters(), character(id) + the playermodel builder of the target (targets/source/pm_outfit.py
               is Glacier-specific today: a new source supplies its own ``build_character`` / ``preview_character``)
  textures     texture_index() (catalog rows + material/model links), load_texture(key), texture_png(...)
  sounds       list_sounds() -> [SoundRef]
"""
from __future__ import annotations

from ..core.ir import Material, Model, TextureData

CAPABILITIES = ("props", "characters", "textures", "sounds")


class Source:
    id: str = ""
    title: str = ""
    capabilities: tuple[str, ...] = ()
    #: short description shown on the home page
    description: str = ""

    # ---- props -------------------------------------------------------------------------------------------
    def catalog_rows(self):
        """Yield {key, name, rel, size, cat, skinned, linked} for every convertible mesh."""
        raise NotImplementedError

    def load_model(self, key: int | str, lod: int = 0) -> tuple[Model, dict[str, Material]]:
        raise NotImplementedError

    def load_material(self, key: int | str) -> Material:
        raise NotImplementedError

    def load_texture(self, key: int | str) -> TextureData | None:
        raise NotImplementedError

    def prop_variants(self, key) -> list[dict]:
        return []

    def load_collision(self, key) -> dict | None:
        return None

    # ---- characters --------------------------------------------------------------------------------------
    def characters(self) -> list[dict]:
        """[{id, title, kind, mission, role, body, variants: [{v, key, name}]}]"""
        return []

    def character(self, cid: str) -> dict | None:
        return next((c for c in self.characters() if c["id"] == cid), None)

    # ---- textures ----------------------------------------------------------------------------------------
    def texture_index(self, progress=print) -> dict:
        """Everything the texture browser needs, built once (the catalog caches it):
        {"textures": [{key, name, folder, fmt, width, height, mips, bytes, role, named}],
         "materials": [{key, name, cls}], "tex_mat": [(texture, material, slot, role)],
         "mat_model": [(material, model key)]}"""
        raise NotImplementedError

    def texture_png(self, key: str, channel: str = "rgb", max_dim: int = 1024, normal: bool = False) -> bytes:
        """PNG preview of a game texture (the core decodes the best mip that fits)."""
        raise NotImplementedError

    # ---- sounds ------------------------------------------------------------------------------------------
    def list_sounds(self, progress=print, fresh: bool = False):
        raise NotImplementedError
