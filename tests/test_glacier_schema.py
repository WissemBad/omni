"""Class schemas (CPPT), enums, localised texts and outfit names of the Glacier source."""
import struct

import pytest

from omni.native import AVAILABLE, N
from omni.sources.glacier import naming
from omni.sources.glacier.outfit import EXPECTED
from omni.sources.glacier.schema import SchemaBook, canonical, class_name, parse_keys

native = pytest.mark.skipif(not AVAILABLE or not hasattr(N, "class_schema"), reason="native module without schemas")


def bin1(data: bytes, types: list[str]) -> bytes:
    seg = struct.pack("<I", len(types)) + b"\0" * 4 * len(types) + struct.pack("<I", len(types))
    for i, t in enumerate(types):
        seg += struct.pack("<iiI", i, -1, len(t) + 1) + t.encode() + b"\0"
        seg += b"\0" * (-len(seg) % 4)
    return b"BIN1\0\x08\x01\0" + struct.pack(">I", len(data)) + b"\0" * 4 + data + struct.pack("<II", 0x3989BF9F, len(seg)) + seg


def cppt(props: list[tuple[int, int, int]], types: list[str]) -> bytes:
    begin = 40
    data = bytearray(begin) + b"".join(struct.pack("<IIqq", c, 0, t, p) for c, t, p in props)
    struct.pack_into("<qqq", data, 8, begin, begin + 24 * len(props), begin + 24 * len(props))
    return bin1(bytes(data), types)


@native
def test_class_schema_lists_property_ids_and_types():
    raw = cppt([(0xDC933FAB, 1, -1), (0x1234, 0, 100)], ["float32", "TArray<ZEntityReference>"])
    assert N.class_schema(raw) == [(0xDC933FAB, "TArray<ZEntityReference>", False), (0x1234, "float32", True)]
    with pytest.raises(ValueError):
        N.class_schema(cppt([(1, 5, -1)], ["float32"]))          # a type outside the table
    with pytest.raises(ValueError):
        N.class_schema(b"not a resource")


@native
def test_blueprint_class_is_the_only_type():
    assert N.blueprint_class(bin1(b"\0" * 16, ["zbodypartentity"])) == "zbodypartentity"
    with pytest.raises(ValueError):
        N.blueprint_class(bin1(b"\0" * 16, []))


def xtea(v, key, rounds=32):
    v0, v1, s, m = v[0], v[1], 0, 0xFFFFFFFF
    for _ in range(rounds):
        v0 = (v0 + ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (s + key[s & 3]))) & m
        s = (s + 0x9E3779B9) & m
        v1 = (v1 + ((((v0 << 4) ^ (v0 >> 5)) + v0) ^ (s + key[(s >> 11) & 3]))) & m
    return v0, v1


def locr(languages: int, entries: list[tuple[int, str]], key) -> bytes:
    def enc(text: str) -> bytes:
        b = text.encode() + b"\0"
        b += b"\0" * (-len(b) % 8)
        return b"".join(struct.pack("<II", *xtea(struct.unpack_from("<II", b, o), key)) for o in range(0, len(b), 8))

    table = 1 + 4 * languages
    body = b""
    out = b"\0"
    for _ in range(languages):
        out += struct.pack("<I", table + len(body))
        body += struct.pack("<I", len(entries))
        for i, t in entries:
            e = enc(t)
            body += struct.pack("<II", i, len(e)) + e + b"\0"
    return out + body


KEY = [0x30F95282, 0x1F48C419, 0x295F8548, 0x2A78366D]


@native
def test_texts_read_only_with_the_right_key():
    raw = locr(3, [(0xEF6F0ACC, "Smoking blanc"), (7, "Costume de plongée")], KEY)
    assert N.locr_structure(raw) == [2, 2, 2]                       # the structure never needs a key
    assert N.locr_read(raw, [[1, 2, 3, 4]]) is None                  # a wrong key reads nothing
    got = N.locr_read(raw, [[1, 2, 3, 4], KEY])
    assert got[2] == [(0xEF6F0ACC, "Smoking blanc"), (7, "Costume de plongée")]
    with pytest.raises(ValueError):
        N.locr_structure(b"\0\x03\0\0\0")


def test_keys_are_parsed_and_checked():
    assert parse_keys("82 52 f9 30 19 c4 48 1f 48 85 5f 29 6d 36 78 2a") == [KEY]
    assert parse_keys("00112233445566778899aabbccddeeff, nonsense, 12") == [[0x33221100, 0x77665544, 0xBBAA9988, 0xFFEEDDCC]]
    assert parse_keys("") == [] and parse_keys(None) == []


def test_class_names_and_aliases():
    assert class_name("[modules:/ZBodyPartEntity.class].entitytype") == "zbodypartentity"
    assert class_name("[modules:/zlinkedentity.class](materials).entitytype") == "zlinkedentity(materials)"
    assert class_name("[modules:/zbodypartentity.class].entityblueprint") == "zbodypartentity"
    assert class_name("something else") == "something else"
    assert canonical("TArray<SEntityTemplateReference>") == "TArray<ZEntityReference>"


class FakeBook(SchemaBook):
    def __init__(self, classes):
        self._classes = classes
        self._types = None


def test_properties_are_checked_against_the_class_schemas():
    book = FakeBook({"zbodypartentity": [(0xF7C7EF8D, "ZRuntimeResourceID", True)],
                     "zother": [(0xAA, "float32", True), (0xAA, "int32", True)]})
    assert book.check(0xF7C7EF8D, "ZRuntimeResourceID") == "ok"
    assert book.check(0xF7C7EF8D, "SMatrix43") == "mismatch"
    assert book.check(0xAA, "int32") == "ok"                         # some class gives it that type
    assert book.check(0xBB, "SColorRGB") == "unknown"                # material parameters are not class properties
    assert book.check(0xF7C7EF8D, "?7") == "ok"                      # a type the template could not name
    assert book.verify({"mesh": ("zbodypartentity", 0xF7C7EF8D, "ZRuntimeResourceID")}) == []
    assert "no property" in book.verify({"mesh": ("zbodypartentity", 1, "ZRuntimeResourceID")})[0]
    assert "types" in book.verify({"mesh": ("zbodypartentity", 0xF7C7EF8D, "float32")})[0]


def test_outfit_family_names_are_read_into_mission_role_title():
    bodies = {}
    fams = ["outfit_bluebell_civ_greenway_male_reg", "outfit_bluebell_civ_isola_fem_reg", "outfit_bluebell_brute_x_male_reg"]
    for f in fams:
        bodies[f] = "male_reg" if f.endswith("male_reg") and "fem" not in f else "fem_reg"
    missions = naming.known_missions(fams, bodies)
    assert missions == {"bluebell"}
    s = naming.split_family("outfit_bluebell_civ_greenway_elevator_male_reg", "male_reg", missions)
    assert (s["kind"], s["mission"], s["role"], s["title"], s["reward"]) == ("outfit", "bluebell", "civ", "greenway elevator", False)
    # James Bond's outfits say hero_bond wherever it falls; the title keeps the rest
    s = naming.split_family("outfit_ivy_nojacket_wet_hero_bond_male_reg", "male_reg", missions)
    assert (s["mission"], s["role"], s["title"], s["bond"]) == ("ivy", "hero", "bond nojacket wet", True)
    s = naming.split_family("outfit_clover_hero_bond_male_reg", "male_reg", missions)
    assert (s["role"], s["title"]) == ("hero", "bond")
    # a reward keeps its mission when it names one, else it is a mission of its own
    s = naming.split_family("outfit_reward_bluebell_hero_bond_male_reg", "male_reg", {"bluebell"})
    assert (s["mission"], s["reward"], s["title"]) == ("bluebell", True, "bond")
    s = naming.split_family("outfit_reward_golden_anchor_hero_bond_male_reg", "male_reg", {"bluebell"})
    assert (s["mission"], s["reward"], s["title"]) == ("reward", True, "bond golden anchor")
    assert naming.split_family("", "")["title"] == ""


def test_bond_variations_match_a_family_only_when_the_names_are_equal():
    enums = {"bond_outfit_variations": {"members": [("bond_00_Pepper", 0), ("bond_v99_gildedsuit", 99), ("bond_v75_REWARD_tux_purple", 75), ("undefined", 18)]}}
    v = naming.bond_variations(enums)
    assert set(v) == {"pepper", "gildedsuit", "tuxpurple"}
    assert naming.variation_of("outfit_pepper_hero_bond_male_reg", "male_reg", v) == (0, "bond_00_Pepper")
    assert naming.variation_of("outfit_reward_hero_bond_tux_purple_male_reg", "male_reg", v) == (75, "bond_v75_REWARD_tux_purple")
    assert naming.variation_of("outfit_pepper_hero_bond_dirty_male_reg", "male_reg", v) is None


def test_the_properties_read_by_id_have_names_and_types_in_expected():
    # four properties, each with the class that owns it: the table the schemas are checked against
    assert {k: v[1:] for k, v in EXPECTED.items()}["part mesh"] == (0xF7C7EF8D, "ZRuntimeResourceID")
    assert len(EXPECTED) == 4


def test_game_schemas_agree_with_the_ids_read_by_hand():
    from omni.sources.registry import get_source
    try:
        src = get_source("007fl")
    except KeyError:
        pytest.skip("no 007 First Light")
    if not src.archive.index("CPPT"):
        pytest.skip("no extracted CPPT resources")
    book = SchemaBook(src)
    assert book.classes, "class schemas not read"
    assert book.verify(EXPECTED) == []
