"""The Glacier source reading packages in place (StoreArchive) gives the same models, materials and textures as
the extracted Assets/Sorted tree: real 007 resources are packed into a package and read both ways."""
from pathlib import Path

import numpy as np
import pytest

from omni.native import N
from rpkg_builder import Res, build


def _sources():
    from omni.core.config import CONFIG
    from omni.sources.glacier.adapter import GlacierSource
    if not (CONFIG.assets_sorted / "chunk0" / "PRIM").is_dir() or not hasattr(N, "GlacierStore"):
        pytest.skip("game assets not extracted")
    return CONFIG, GlacierSource


def test_store_reads_like_the_extracted_tree(tmp_path):
    CONFIG, GlacierSource = _sources()
    from omni.sources.glacier.meta import parse_meta
    from omni.sources.glacier.store import StoreArchive
    ref = GlacierSource()
    prims = [h for h in list(ref.archive.index("PRIM"))[:400] if ref.names.name(h)][:25]
    wanted, res = set(), []

    def add(h, kind, depth=0):
        if (h, kind) in wanted:
            return
        p = ref.archive.find(kind, h)
        if p is None:
            return
        wanted.add((h, kind))
        m = parse_meta(Path(str(p) + ".meta").read_bytes())
        res.append(Res(h, kind, p.read_bytes(), m.refs, scramble=len(res) % 2 == 0, compress=len(res) % 3 == 0))
        if depth < 3:
            for rh, _f in m.refs:
                for k in ("MATI", "MATE", "TEXT", "TEXD", "BORG"):
                    add(rh, k, depth + 1)
    for h in prims:
        add(h, "PRIM")
    rt = tmp_path / "Runtime"
    rt.mkdir()
    (rt / "chunk0.rpkg").write_bytes(build(res))
    direct = GlacierSource()
    direct.archive = StoreArchive([rt / "chunk0.rpkg"])
    for h in prims:
        a, ma = ref.load_model(h)
        b, mb = direct.load_model(h)
        assert len(a.submeshes) == len(b.submeshes)
        for x, y in zip(a.submeshes, b.submeshes):
            assert np.array_equal(x.positions, y.positions) and np.array_equal(x.indices, y.indices)
            assert x.material_key == y.material_key
        assert set(ma) == set(mb)
        for k in ma:
            assert [(t.role, t.key) for t in ma[k].textures] == [(t.role, t.key) for t in mb[k].textures]
    for tex in [h for (h, k) in wanted if k == "TEXT"][:20]:
        ta, tb = ref.load_texture(tex), direct.load_texture(tex)
        assert ta.fmt == tb.fmt and [m[2] for m in ta.mips] == [m[2] for m in tb.mips]
        assert ref.texture_png("%016X" % tex, max_dim=64) == direct.texture_png("%016X" % tex, max_dim=64)
