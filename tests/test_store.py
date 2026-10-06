"""Direct reading of Glacier packages (native GlacierStore) on synthetic packages: patch priority, deletions,
compressed and scrambled resources, references."""
import pytest

from omni.native import N
from rpkg_builder import Res, build

pytestmark = pytest.mark.skipif(N is None or not hasattr(N, "GlacierStore"), reason="native module without the store")

A, B, C, D = (0x00_1000_0000_0000_01 + i for i in range(4))


def test_patches_replace_and_delete(tmp_path):
    rt = tmp_path / "Runtime"
    rt.mkdir()
    (rt / "chunk0.rpkg").write_bytes(build([
        Res(A, "PRIM", b"base prim " * 40, [(B, 0x1F)], scramble=True, compress=True),
        Res(B, "TEXT", b"base text"),
        Res(C, "MATI", b"to be removed"),
    ]))
    (rt / "chunk0patch1.rpkg").write_bytes(build([Res(B, "TEXT", b"patched text")], patch=1, unneeded=[C]))
    (rt / "chunk1.rpkg").write_bytes(build([Res(D, "WWEM", b"RIFF....")], chunk=1))
    s = N.GlacierStore([str(p) for p in sorted(rt.glob("*.rpkg"))])
    assert len(s) == 3
    assert s.read(A) == b"base prim " * 40                      # LZ4 + XOR undone
    assert s.read(B) == b"patched text"                         # the patch wins
    assert not s.has(C)                                         # the patch's unneeded list removes it
    assert s.meta(A) == ("PRIM", 400, [(B, 0x1F)])
    assert sorted(s.of_type("TEXT")) == [B] and s.types() == {"PRIM": 1, "TEXT": 1, "WWEM": 1}
    assert s.package_of(B).endswith("chunk0patch1.rpkg")
    with pytest.raises(ValueError):
        s.read(C)


def test_store_archive_matches_the_archive_api(tmp_path):
    from omni.sources.glacier.store import StoreArchive
    rt = tmp_path / "Runtime"
    rt.mkdir()
    (rt / "chunk0.rpkg").write_bytes(build([Res(A, "PRIM", b"p" * 32, [(B, 0x5F)]), Res(B, "MATI", b"m" * 8)]))
    a = StoreArchive([rt / "chunk0.rpkg"])
    p = a.find("PRIM", A)
    assert p is not None and p.read_bytes() == b"p" * 32 and p.stat().st_size == 32
    assert a.find("PRIM", B) is None and a.find("MATI", B).read_bytes() == b"m" * 8
    assert dict(a.index("PRIM").items()).keys() == {A}
    assert a.meta(p).refs == [(B, 0x5F)] and a.meta(p).type == "PRIM"
    with p.open("rb") as f:
        assert f.read(4) == b"pppp"
