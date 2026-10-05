"""Game extraction (native rpkg extractor + core/setup.py) on synthetic packages, and on real resources when present."""
import random
from pathlib import Path

import pytest

from omni.native import N
from omni.sources.glacier.meta import parse_meta
from rpkg_builder import Res, build

pytestmark = pytest.mark.skipif(N is None or not hasattr(N, "rpkg_extract"), reason="native module without rpkg")


def _sample():
    rnd = random.Random(3)
    return [
        Res(0x0100_0000_0000_0010, "PRIM", bytes(rnd.randrange(256) for _ in range(3000)) * 3, [(0x0100_0000_0000_0011, 0x5F)], True, True),
        Res(0x0100_0000_0000_0011, "TEXT", b"texture bytes " * 50, [], False, False),
        Res(0x0100_0000_0000_0012, "GFXV", b"ui", [], False, False),
    ]


def test_info_and_filtered_extraction(tmp_path):
    pkg = tmp_path / "Runtime" / "chunk0.rpkg"
    pkg.parent.mkdir()
    res = _sample()
    pkg.write_bytes(build(res))
    info = N.rpkg_info(str(pkg))
    assert info["files"] == 3 and info["types"]["PRIM"] == 1 and info["type_bytes"]["TEXT"] == len(res[1].data)
    out = tmp_path / "Sorted"
    st = N.rpkg_extract([str(pkg)], str(out), ["PRIM", "TEXT"], 2)
    assert st["written"] == 2 and not st["errors"]
    assert (out / "chunk0/PRIM/0100000000000010.PRIM").read_bytes() == res[0].data
    assert not (out / "chunk0/GFXV").exists()
    # the .meta is what the sources read
    m = parse_meta((out / "chunk0/PRIM/0100000000000010.PRIM.meta").read_bytes())
    assert m.resource_id == 0x0100_0000_0000_0010 and m.refs == [(0x0100_0000_0000_0011, 0x5F)]


def test_setup_status_and_game_detection(tmp_path, monkeypatch):
    from omni.core import setup
    game = tmp_path / "007 First Light"
    (game / "Runtime").mkdir(parents=True)
    (game / "Runtime" / "chunk0.rpkg").write_bytes(build(_sample()))
    folder, pkgs = setup.game_packages(game)
    assert folder == game / "Runtime" and [p.name for p in pkgs] == ["chunk0.rpkg"]
    assert setup.game_packages(tmp_path / "nothing") is None
    assert setup.estimate(pkgs)["files"] >= 2          # PRIM + TEXT of the needed types


def test_round_trip_on_real_resources(tmp_path):
    """Pack real extracted resources into a package, extract it again: bytes and references are identical."""
    from omni.core.config import CONFIG
    base = CONFIG.assets_sorted / "chunk0"
    prims = sorted((base / "PRIM").glob("*.PRIM"))[:40] if (base / "PRIM").exists() else []
    if not prims:
        pytest.skip("game assets not extracted")
    res = []
    for i, f in enumerate(prims):
        m = parse_meta(Path(str(f) + ".meta").read_bytes())
        res.append(Res(m.resource_id, "PRIM", f.read_bytes(), m.refs, scramble=i % 2 == 0, compress=i % 3 != 0))
    pkg = tmp_path / "chunk0.rpkg"
    pkg.write_bytes(build(res))
    out = tmp_path / "Sorted"
    st = N.rpkg_extract([str(pkg)], str(out), [], 4)
    assert st["written"] == len(res) and not st["errors"]
    for f, r in zip(prims, res):
        assert (out / "chunk0/PRIM" / f.name).read_bytes() == f.read_bytes()
        assert parse_meta((out / "chunk0/PRIM" / (f.name + ".meta")).read_bytes()).refs == r.refs
