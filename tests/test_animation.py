"""Source animations: the Rust reader and the viewer's retargeting, on Garry's Mod's own animation model when installed."""
import base64

import numpy as np
import pytest

from omni.core.config import CONFIG
from omni.native import AVAILABLE, N

VPK = CONFIG.gmod / "garrysmod" / "garrysmod_dir.vpk"
pytestmark = [pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable"),
              pytest.mark.skipif(not VPK.is_file(), reason="Garry's Mod not installed")]


@pytest.fixture(scope="module")
def anm():
    import vpk
    pak = vpk.open(str(VPK))
    return pak.get_file("models/m_anm.mdl").read(), pak.get_file("models/m_anm.ani").read()


def test_every_sequence_of_m_anm_decodes_to_unit_quaternions(anm):
    mdl, ani = anm
    info = N.mdl_info(mdl)
    assert info["needs_ani"] and len(info["bones"]) > 40
    done = 0
    for s in info["sequences"]:
        if s["anim"] < 0 or s["frames"] < 1:
            continue
        _fps, flags, a = N.mdl_sample(mdl, ani, s["anim"])
        assert np.isfinite(a).all()
        if not flags & 4:                                   # delta animations hold differences, not rotations
            assert abs(np.linalg.norm(a[..., 3:], axis=-1) - 1).max() < 0.05, s["name"]
        done += 1
    assert done > 300
    with pytest.raises(ValueError):
        N.mdl_sample(mdl, None, next(s["anim"] for s in info["sequences"] if s["anim"] > 0))   # the .ani is needed


def test_catalog_lists_playable_sequences_and_an_idle(anm):
    from omni.ui import animation
    mdl, ani = anm
    c = animation.catalog(mdl, ani, own_label="m_anm")
    names = {s["name"] for s in c["sources"][0]["sequences"]}
    assert {"idle_all_01", "walk_all"} <= names and c["default"]["index"] >= 0


def test_sample_is_in_the_viewer_frame_and_movement_moves(anm):
    from omni.ui import animation
    mdl, ani = anm
    c = animation.catalog(mdl, ani, own_label="m_anm")
    index = {s["name"]: s["index"] for s in c["sources"][0]["sequences"]}

    def clip(name):
        d = animation.sample(mdl, ani, None, "self", index[name])
        return d, np.frombuffer(base64.b64decode(d["data"]), "<f4").reshape(d["frames"], d["bones"], 7)
    idle, a = clip("idle_all_01")
    assert a.shape == (idle["frames"], idle["bones"], 7) and 0.3 < a[0, 0, 1] < 1.2        # the pelvis is about a metre up (Y)
    walk, w = clip("walk_all")
    assert walk["frames"] < idle["frames"] and w[:, 18, 3:].std(0).max() > 3 * a[:, 18, 3:].std(0).max()   # the thigh swings
    with pytest.raises(KeyError):
        animation.sample(mdl, ani, None, "self", 99999)
