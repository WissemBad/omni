"""Optional step: build one .blend per model with every material already wired to its textures.

Python side decodes the textures to PNG and writes a job file; ONE Blender process (background) then
builds all .blend files of the batch, so there is no per-asset Blender start-up cost.
Material names are identical to the VMT names of the Source export.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from ...core.config import CONFIG
from ...core.windows import NOWINDOW
from ...preview.glb import export_glb
from ...sources.glacier.texture import Mip, to_rgba
from ..source.textures import rebuild_normal



def _dump_texture(source, key: str, role: str, out: Path, size: int) -> bool:
    if out.exists():
        return True
    t = source.load_texture(int(key, 16))
    if t is None:
        return False
    cands = [m for m in t.mips if max(m[0], m[1]) <= size] or [t.mips[-1]]
    w, h, d = cands[0]
    a = to_rgba(t.fmt, Mip(w, h, d))
    if role == "normal" and t.fmt in ("BC5", "BC4", "RG8"):
        a = rebuild_normal(a)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a).save(out)
    return True


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
    for i, k in enumerate(keys):
        if progress:
            progress(i, len(keys))
        model, mats = source.load_model(int(k, 16))
        rel = model.name
        glb = out_dir / "_glb" / f"{k}.glb"
        export_glb(source, model, mats, glb, tex_size=16, embed_textures=False)
        materials = {}
        for mat in mats.values():
            roles = {}
            for ref in mat.textures:
                if ref.role in roles:
                    continue
                png = out_dir / "textures" / f"{ref.key}.png"
                if _dump_texture(source, ref.key, ref.role, png, tex_size):
                    roles[ref.role] = str(png)
            materials[mat.name] = {"roles": roles, "params": mat.params, "flags": sorted(mat.flags)}
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
