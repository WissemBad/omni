"""Body slots of outfit parts, read from the game's catalogue folder first and the garment's name second."""
from omni.sources.glacier.slots import SLOTS, slot_of

BODY = "[assembly:/_knt/characters/templates/actors/fem_reg/parts_average/{}.template?/{}.entitytemplate].entitytype"


def part(folder: str, name: str) -> str:
    return slot_of(BODY.format(folder, name), name)


def test_the_catalogue_folder_decides():
    assert part("apparel/upperbody_l01", "shirt_dress_2btnopen_rldslv_tkd") == "top1"
    assert part("apparel/upperbody_l02", "coat_lab_rldslv_open") == "top2"
    assert part("apparel/lowerbody", "pants_dress_wide") == "legs"            # "dress" in the name does not make it a torso
    assert part("apparel/footwear", "shoes_lowheel_pumps") == "feet"
    assert part("body/body_parts", "arms_elbow_hand") == "body_arms"          # the hand is part of the arms skin, not a torso
    assert part("body/body_parts", "legs_noheel_ankle_shoe") == "body_legs"
    assert part("body/body_parts", "torso_neck_waist_front") == "body_torso"
    assert part("heads/heads", "head_young_indian_04") == "head" and part("hair/hair", "hair_updo_bunlow") == "hair"
    assert part("apparel/earwear", "earrings_rhinestone_2row") == "earwear"


def test_kit_parts_are_read_by_their_garment_word():
    kit = "[assembly:/_knt/characters/templates/actors/male_reg/kits/clover/kit_clover_arrowhead.template?/{0}.entitytemplate]"
    for name, slot in {"jacket_kit_arrowhead_mercenary": "top2", "pants_kit_arrowhead_mercenary": "legs",
                       "gloves_kit_arrowhead_mercenary": "hands", "vest_kit_arrowhead_mercenary": "vest",
                       "beanie_kit_arrowhead_mercenary": "headwear", "boots_kit_global_x": "feet",
                       "kit_clover_hostages_pants": "legs", "watch_kit_pepper": "accessories", "mystery_thing": "accessories"}.items():
        assert slot_of(kit.format(name), name) == slot, name


def test_every_slot_has_a_name():
    keys = [k for k, _n in SLOTS]
    assert len(keys) == len(set(keys)) and all(n for _k, n in SLOTS)
