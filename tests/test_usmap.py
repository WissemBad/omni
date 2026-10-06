"""Community mappings (.usmap) as the fallback of the executable's reflection data: native reader, archive pick,
and the fallback in ``sources.unreal.game.mappings``."""
import struct

import pytest

from omni.native import N

pytestmark = pytest.mark.skipif(N is None or not hasattr(N, "unreal_usmap"), reason="native module without usmap")


def usmap(version: int = 4) -> bytes:
    names = ["EColor", "EColor::Red", "EColor::Blue", "Thing", "Base", "Count", "Tags", "Color", "Lookup", "Vector"]
    ix = {n: i for i, n in enumerate(names)}
    body = struct.pack("<I", len(names)) + b"".join(struct.pack("<H", len(n)) + n.encode() for n in names)
    body += struct.pack("<I", 1) + struct.pack("<iH", ix["EColor"], 2)
    body += struct.pack("<Qi", 0, ix["EColor::Red"]) + struct.pack("<Qi", 5, ix["EColor::Blue"])
    body += struct.pack("<I", 1) + struct.pack("<iiHH", ix["Thing"], ix["Base"], 6, 4)
    body += struct.pack("<HBi", 0, 1, ix["Count"]) + bytes([2])                                   # Int
    body += struct.pack("<HBi", 1, 2, ix["Tags"]) + bytes([8, 5])                                 # Name[2] array
    body += struct.pack("<HBi", 3, 1, ix["Color"]) + bytes([26, 0]) + struct.pack("<i", ix["EColor"])
    body += struct.pack("<HBi", 5, 1, ix["Lookup"]) + bytes([24, 5, 9]) + struct.pack("<i", ix["Vector"])
    head = struct.pack("<HB", 0x30C4, version) + struct.pack("<I", 0) + struct.pack("<BII", 0, len(body), len(body))
    return head + body


def test_native_reader(tmp_path):
    p = tmp_path / "Mappings.usmap"
    p.write_bytes(usmap())
    text, ns, ne = N.unreal_usmap(str(p))
    assert (ns, ne) == (1, 1)
    lines = text.splitlines()
    assert lines[0] == "omni-mappings 1" and "S Thing Base" in lines
    assert "P 2 Tags Array<Name>" in lines                       # array dims kept
    assert any(line.startswith("P 1 _unused4 ") for line in lines)  # the hole keeps its index
    assert "E EColor Red=0 Blue=5" in lines
    p.write_bytes(b"\xc4\x30\x09" + b"\0" * 32)
    with pytest.raises(ValueError):
        N.unreal_usmap(str(p))


def test_archive_pick():
    from omni.sources.unreal.game import archive_pick
    paths = ["Abiotic Factor/Mappings.usmap", "Dead by Daylight/7.7.0 PTB/Mappings.usmap", "Dead by Daylight/9.3.2/Mappings.usmap",
             "Dead by Daylight/10.0.0 PTB/Mappings.usmap", "Dead by Daylight/7.10.1/Mappings.usmap", "README.md"]
    assert archive_pick(paths, ["Abiotic Factor"]) == "Abiotic Factor/Mappings.usmap"
    assert archive_pick(paths, ["DeadByDaylight", ""]) == "Dead by Daylight/9.3.2/Mappings.usmap"
    assert archive_pick(paths, ["Bronzebeard's Tavern"]) is None


def test_mappings_fall_back_to_a_local_usmap(tmp_path, monkeypatch):
    from omni.core.config import CONFIG
    from omni.sources.unreal import game
    monkeypatch.setattr(CONFIG, "workspace", tmp_path / "ws")
    exe = tmp_path / "Game-Win64-Shipping.exe"
    exe.write_bytes(b"MZ not a real executable")
    info = {"id": "ue-test", "exe": str(exe), "root": str(tmp_path), "title": "Not In The Archive"}
    (tmp_path / "Mappings.usmap").write_bytes(usmap())
    text = game.mappings(info)
    assert "S Thing Base" in text
    assert game.mappings(info) == text                            # cached
