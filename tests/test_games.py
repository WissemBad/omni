"""Game identification from a folder, on fake game trees."""
from __future__ import annotations

from omni import games


def touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_glacier_games_are_told_apart(tmp_path):
    bond = tmp_path / "007 First Light"
    touch(bond / "Runtime" / "chunk0.rpkg")
    hm = tmp_path / "HITMAN 3"
    touch(hm / "Runtime" / "chunk0.rpkg")
    touch(hm / "Retail" / "HITMAN3.exe")
    assert games.identify(bond)["id"] == "007fl"
    g = games.identify(hm)
    assert g["id"] == "hitman3" and g["engine"] == "glacier" and g["supported"] is True
    assert set(g["capabilities"]) == {"props", "characters", "textures", "sounds"}


def test_unreal_without_iostore_or_ue5_is_not_supported(tmp_path):
    root = tmp_path / "Old Game"
    touch(root / "OldGame" / "Content" / "Paks" / "OldGame-WindowsNoEditor.pak")
    exe = root / "OldGame" / "Binaries" / "Win64" / "OldGame-Win64-Shipping.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ" + b"++UE4+Release-4.27" + bytes(10))
    g = games.identify(root)
    assert g["engine"] == "unreal" and g["supported"] is False and "UE4" in g["reason"]


def test_unreal_project_is_found_with_its_version(tmp_path):
    root = tmp_path / "Bronzebeards Tavern"
    touch(root / "BronzebeardsTavern" / "Content" / "Paks" / "a-Windows.utoc")
    touch(root / "BronzebeardsTavern" / "Content" / "Paks" / "a-Windows.ucas")
    exe = root / "BronzebeardsTavern" / "Binaries" / "Win64" / "Game-Win64-Shipping.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ" + b"\0" * 100 + b"++UE5+Release-5.1" + b"\0" * 10)
    g = games.identify(root)
    assert g["engine"] == "unreal" and g["version"] == "5.1" and g["iostore"] is True
    assert g["id"] == "ue-bronzebeardstavern" and g["project"] == "BronzebeardsTavern"


def test_unknown_folder_is_refused(tmp_path):
    assert games.identify(tmp_path) is None
    assert games.identify(tmp_path / "missing") is None
    try:
        games.add(tmp_path)
    except ValueError as e:
        assert "reconnu" in str(e)
    else:
        raise AssertionError("an unknown folder was added")


def test_library_add_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(games.CONFIG, "workspace", tmp_path / "ws")
    root = tmp_path / "007 First Light"
    touch(root / "Runtime" / "chunk0.rpkg")
    games.add(root)
    assert [g["id"] for g in games.load()] == ["007fl"]
    assert games.remove("007fl") and games.load() == []
