"""Optional step: build one .blend per model with every material already wired to its textures.

Python side decodes the textures to PNG and writes a job file; ONE Blender process (background) then
builds all .blend files of the batch, so there is no per-asset Blender start-up cost.
Material names are identical to the VMT names of the Source export.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from ...core.config import CONFIG
from ...core.windows import NOWINDOW
from ...preview.glb import export_glb
from .. import shading as sh


def _save(a: np.ndarray, out: Path) -> str:
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.ascontiguousarray(a)).save(out)
    return str(out)


def _grey(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _maps(mat, cache, tex_dir: Path, size: int) -> dict:
    """{role: png} of the material as every target reads it (see targets/shading.py): base (albedo, alpha only when
    it is an opacity), normal (green up), rough / metal / ao (greyscale), emissive."""
    lk = sh.look(mat, cache)
    tag = f"{mat.key}_{size}"
    roles = {}
    a = lk.albedo(cache, size)
    if a is not None:
        roles["base"] = _save(a, tex_dir / f"{tag}_base.png")
    nref = sh.first(mat, "normal")
    n = sh.normal(cache.get(nref.key), size) if nref is not None else None
    if n is not None:
        roles["normal"] = _save(n[..., :3], tex_dir / f"{tag}_normal.png")
    surf = sh.surface(mat, cache, size, lk)
    if surf is not None:
        roles["rough"] = _save(_grey(np.clip(surf.rough, 0.0, 1.0)), tex_dir / f"{tag}_rough.png")
        roles["metal"] = _save(_grey(surf.metal), tex_dir / f"{tag}_metal.png")
        if surf.ao is not None:
            roles["ao"] = _save(_grey(surf.ao), tex_dir / f"{tag}_ao.png")
    e = sh.emissive(mat, cache, size)
    if e is not None:
        roles["emissive"] = _save(e[..., :3], tex_dir / f"{tag}_emissive.png")
    return roles, lk


def export_blends(source, keys: list[str], out_dir: Path | None = None, tex_size: int = 2048,
                  blender: Path | None = None, progress=None, on_blender=None) -> list[Path]:
    """``progress(done, total)`` follows the preparation of the models; ``on_blender()`` is called when Blender,
    which then writes every .blend in one run, starts."""
    blender = blender or CONFIG.blender_exe()
    if blender is None:
        raise RuntimeError("Blender est introuvable : renseigne son chemin dans Réglages (ou installe-le), "
                           "ou décoche « Produire aussi un .blend ».")
    out_dir = out_dir or CONFIG.blend_dir(source.id)
    jobs = []
    cache = sh.TextureCache(source)
    for i, k in enumerate(keys):
        if progress:
            progress(i, len(keys))
        model, mats = source.load_model(int(k, 16))
        rel = model.name
        glb = out_dir / "_glb" / f"{k}.glb"
        export_glb(source, model, mats, glb, tex_size=16, embed_textures=False)
        materials = {}
        for mat in mats.values():
            roles, lk = _maps(mat, cache, out_dir / "textures", tex_size)
            flags = sorted(mat.flags | ({"alpha_test"} if lk.alpha_test else set()) | ({"translucent"} if lk.translucent else set()))
            materials[mat.name] = {"roles": roles, "flags": flags, "drop": lk.drop}
        jobs.append({"glb": str(glb), "blend": str(out_dir / f"{rel}.blend"), "name": rel, "materials": materials})
    jf = out_dir / "jobs.json"
    jf.write_text(json.dumps(jobs), encoding="utf-8")
    if progress:
        progress(len(keys), len(keys))
    if on_blender:
        on_blender()
    script = Path(__file__).with_name("blender_script.py")
    r = subprocess.run([str(blender), "-b", "--factory-startup", "--python", str(script), "--", str(jf)],
                       capture_output=True, text=True, errors="replace", creationflags=NOWINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr)[-2000:])
    return [Path(j["blend"]) for j in jobs]
