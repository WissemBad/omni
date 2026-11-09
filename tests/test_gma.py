"""Garry's Mod archives (.gma) and the viewer without a game (/api/view/output)."""
import struct
import zlib

import pytest

from omni.targets.source import gma


def make_gma(files: dict[str, bytes], name: str = "Test addon", version: int = 3) -> bytes:
    """An archive as gmad writes it (version 3)."""
    out = bytearray(b"GMAD" + struct.pack("<BQQ", version, 0, 0))
    if version > 1:
        out += b"\0"                                      # no required content
    out += name.encode() + b"\0" + b'{"description": "x"}\0' + b"Author\0" + struct.pack("<i", 1)
    for i, (path, data) in enumerate(files.items(), 1):
        out += struct.pack("<I", i) + path.encode() + b"\0" + struct.pack("<qI", len(data), zlib.crc32(data))
    out += struct.pack("<I", 0)
    for data in files.values():
        out += data
    return bytes(out + struct.pack("<I", 0))


def test_index_and_title(tmp_path):
    f = tmp_path / "a.gma"
    f.write_bytes(make_gma({"models/x/crate.mdl": b"mdl", "lua/autorun/a.lua": b"print(1)"}))
    g = gma.read_index(f)
    assert [(e.path, e.size) for e in g.entries] == [("models/x/crate.mdl", 3), ("lua/autorun/a.lua", 8)]
    assert gma.title(g, "fallback") == "Test addon"


def test_extract_keeps_models_and_materials_only(tmp_path):
    f = tmp_path / "a.gma"
    f.write_bytes(make_gma({"models/x/crate.mdl": b"mdl", "materials/x/crate.vmt": b"vmt", "lua/a.lua": b"lua"}))
    dest = tmp_path / "out"
    assert gma.extract(f, dest) == 2
    assert (dest / "models/x/crate.mdl").read_bytes() == b"mdl"
    assert (dest / "materials/x/crate.vmt").read_bytes() == b"vmt"
    assert not (dest / "lua").exists()


@pytest.mark.parametrize("bad", ["../evil.mdl", "models/../../evil.mdl", "C:/evil.mdl", "/abs.mdl"])
def test_paths_outside_the_archive_are_refused(tmp_path, bad):
    f = tmp_path / "a.gma"
    f.write_bytes(make_gma({bad: b"x"}))
    with pytest.raises(ValueError):
        gma.read_index(f)


def test_truncated_and_foreign_files_are_refused(tmp_path):
    f = tmp_path / "a.gma"
    f.write_bytes(make_gma({"models/a.mdl": b"0123456789"})[:-8])
    with pytest.raises(ValueError):
        gma.read_index(f)
    f.write_bytes(b"PK\3\4 not a gma")
    with pytest.raises(ValueError):
        gma.read_index(f)


def test_a_mdl_opens_its_addon(tmp_path):
    from omni.ui.output import _open_target
    mdl = tmp_path / "addon" / "models" / "props" / "crate.mdl"
    mdl.parent.mkdir(parents=True)
    mdl.write_bytes(b"x")
    root, model, label = _open_target(mdl)
    assert (root, model, label) == ((tmp_path / "addon").resolve(), "models/props/crate.mdl", "addon")
    loose = tmp_path / "loose.mdl"
    loose.write_bytes(b"x")
    assert _open_target(loose)[1:] == ("loose.mdl", tmp_path.name)


def test_view_lists_an_opened_archive_without_any_game(api_client, tmp_path):
    pytest.importorskip("httpx2")
    f = tmp_path / "pack.gma"
    f.write_bytes(make_gma({"models/pack/chair.mdl": b"mdl", "materials/pack/chair.vmt": b"vmt"}, name="Chairs"))
    r = api_client.post(f"/api/view/output/open?path={f}")
    assert r.status_code == 200, r.text
    opened = r.json()
    assert opened["label"] == "Chairs (.gma)" and opened["model"] == ""
    roots = api_client.get("/api/view/output/roots").json()
    assert opened["root"] in {x["id"] for x in roots} and all(x["external"] for x in roots)
    page = api_client.get(f"/api/view/output?root={opened['root']}").json()
    assert [i["path"] for i in page["items"]] == ["models/pack/chair.mdl"]
    assert api_client.post(f"/api/view/output/open?path={tmp_path / 'nope.gma'}").status_code == 404
    assert isinstance(api_client.get("/api/view/output/installed").json(), list)
    api_client.delete(f"/api/view/output/roots/{opened['root']}")


def test_legacy_workshop_items_are_lzma_archives(tmp_path):
    import lzma
    raw = make_gma({"models/old.mdl": b"mdl"}, name="Old")
    legacy = tmp_path / "123_legacy.bin"
    legacy.write_bytes(lzma.compress(raw, format=lzma.FORMAT_ALONE))
    assert gma.is_legacy(legacy)
    out = tmp_path / "old.gma"
    gma.unpack_legacy(legacy, out)
    assert gma.title(gma.read_index(out), "x") == "Old"
