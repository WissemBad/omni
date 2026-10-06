"""Which body slot an outfit part fills, read from the game's own catalogue.

An outfit part is an instance of a part template that lives in a catalogue folder of the game, and that folder is the
game's classification: ``.../parts_average/apparel/upperbody_l01.template`` (shirts), ``upperbody_l02`` (what goes over
them), ``lowerbody``, ``footwear``, ``handwear``, ``neckwear``, ``fullbody``, ``hair``, ``beard``, ``heads``,
``body/body_parts`` (the skin of arms, torso and legs), ``attachables/helmets|hats|glasses|earwear``...  Only the parts
of an outfit-specific kit (``.../kits/<mission>/kit_*``, ``..._kit_<name>``) are not filed by the catalogue: their
first word names the garment (``jacket_kit_arrowhead``, ``gloves_kit_x``), which is read the same way.
"""
from __future__ import annotations

import re

# (key, bodygroup name in GMod), in the order the bodygroups are listed
SLOTS = [
    ("head", "Head"), ("hair", "Hair"), ("beard", "Beard"), ("headwear", "Headwear"), ("eyewear", "Eyewear"),
    ("earwear", "Earwear"), ("neckwear", "Neckwear"), ("body_torso", "Torso skin"), ("top1", "Shirt"),
    ("top2", "Outerwear"), ("fullbody", "Full body"), ("vest", "Vest"), ("back", "Back"), ("body_arms", "Arms"),
    ("hands", "Gloves"), ("waist", "Belt"), ("legs", "Pants"), ("body_legs", "Legs skin"), ("feet", "Shoes"),
    ("body_hair", "Body hair"), ("accessories", "Accessories"),
]
INDEX = {k: i for i, (k, _n) in enumerate(SLOTS)}

# folder (last two path segments of the part template) -> slot
_FOLDERS = [
    (r"/heads/|/head/", "head"), (r"/hair/", "hair"), (r"/beard/", "beard"), (r"body_hair", "body_hair"),
    (r"/apparel/footwear", "feet"), (r"/apparel/lowerbody", "legs"), (r"/apparel/upperbody_l01", "top1"),
    (r"/apparel/upperbody_l02", "top2"), (r"/apparel/upperbody", "top2"), (r"/apparel/neckwear", "neckwear"),
    (r"/apparel/handwear", "hands"), (r"/apparel/headwear", "headwear"), (r"/apparel/earwear", "earwear"),
    (r"/apparel/fullbody", "fullbody"), (r"/apparel/waistwear", "waist"), (r"/apparel/rings", "accessories"),
    (r"/apparel/suit_accessories", "accessories"), (r"/attachables/(helmets|hats)", "headwear"),
    (r"/attachables/glasses", "eyewear"), (r"/attachables/earwear", "earwear"),
]

# first word of a garment name -> slot (kits, attachables, anything the catalogue does not file)
_WORDS = {
    "head": "head", "hair": "hair", "fhair": "beard", "beard": "beard", "moustache": "beard", "mustache": "beard",
    "beanie": "headwear", "balaclava": "headwear", "cap": "headwear", "hat": "headwear", "helmet": "headwear",
    "headset": "headwear", "headwear": "headwear", "headwrap": "headwear", "beret": "headwear", "bandana": "headwear",
    "mask": "headwear", "hood": "headwear", "headband": "headwear", "turban": "headwear", "scarf": "neckwear",
    "glasses": "eyewear", "goggles": "eyewear", "earpiece": "earwear", "earrings": "earwear", "earring": "earwear",
    "tie": "neckwear", "snood": "neckwear", "durag": "headwear",
    "arms": "body_arms", "arm": "body_arms", "legs": "body_legs", "leg": "body_legs", "legguards": "legs", "bowtie": "neckwear", "necklace": "neckwear", "collar": "neckwear",
    "shirt": "top1", "tshirt": "top1", "tanktop": "top1", "top": "top1", "blouse": "top1", "polo": "top1",
    "sweater": "top1", "turtleneck": "top1", "raglan": "top1", "undershirt": "top1", "bra": "top1", "bikini": "top1",
    "jacket": "top2", "coat": "top2", "blazer": "top2", "hoodie": "top2", "parka": "top2", "cardigan": "top2",
    "labcoat": "top2", "trenchcoat": "top2", "apron": "top2", "uniform": "top2", "torso": "top2", "waistcoat": "top2",
    "vest": "vest", "bodyarmor": "vest", "bodyarmour": "vest", "armor": "vest", "armour": "vest", "chestrig": "vest",
    "harness": "vest", "plate": "vest",
    "dress": "fullbody", "jumpsuit": "fullbody", "flightsuit": "fullbody", "divingsuit": "fullbody", "suit": "fullbody",
    "tunic": "fullbody", "kimono": "fullbody", "overalls": "fullbody", "robe": "fullbody", "costume": "fullbody",
    "backpack": "back", "bckpck": "back", "bag": "back", "bags": "back", "pack": "back", "quiver": "back",
    "gloves": "hands", "glove": "hands", "gauntlets": "hands", "rings": "accessories", "ring": "accessories",
    "watch": "accessories", "bracelet": "accessories", "belt": "waist", "holster": "waist", "pouch": "waist",
    "pants": "legs", "jeans": "legs", "shorts": "legs", "skirt": "legs", "trousers": "legs", "sweatpants": "legs",
    "leggings": "legs", "tights": "legs", "stockings": "legs", "cargo": "legs",
    "boots": "feet", "boot": "feet", "shoes": "feet", "shoe": "feet", "sneakers": "feet", "sneaker": "feet",
    "trainers": "feet", "sandals": "feet", "loafers": "feet", "socks": "feet", "sock": "feet",
}
_BODY_WORDS = {"arms": "body_arms", "arm": "body_arms", "legs": "body_legs", "leg": "body_legs", "torso": "body_torso"}


def _words(label: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", label.lower()) if w]


def slot_of(template: str, label: str) -> str:
    """The slot key of a part from its catalogue folder (``template``: the part template's path) and its name."""
    low = template.lower().replace("\\", "/")
    words = _words(label)
    if "/body/body_parts" in low or "/body_parts" in low:
        return _BODY_WORDS.get(words[0], "body_torso") if words else "body_torso"
    for rx, slot in _FOLDERS:
        if re.search(rx, low):
            return slot
    # outfit-specific kits and anything else: the first word that names a garment (``jacket_kit_x``, ``kit_x_pants``)
    for w in words:
        if w in _WORDS:
            return _WORDS[w]
    for w in words:
        for key, slot in _WORDS.items():
            if len(key) > 3 and w.startswith(key):
                return slot
    return "accessories"


def slot_index(template: str, label: str) -> int:
    return INDEX[slot_of(template, label)]
