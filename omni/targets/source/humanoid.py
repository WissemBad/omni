"""Recognise the humanoid bones of any skeleton and give them the canonical names the playermodel builder knows.

``playermodel.valve_name_for`` maps one naming (``root``, ``spine_02``, ``L_shoulder``, ``L_index_1``...) onto
ValveBiped. Games name their bones in many ways: Unreal's mannequin (``upperarm_l``), Mixamo
(``mixamorig:LeftArm``), 3ds Max Biped (``Bip01 L UpperArm``) and CAT (``Dwarf_LUpperarm``, ``Base-HumanLDigit11``),
Rigify (``upper_arm.L``)... ``canonical_names`` returns, for every bone, its canonical name when it plays a
humanoid role, else its own name (kept unique). Side comes from the name, role from keywords, and the chains
(spine, fingers) from the hierarchy.
"""
from __future__ import annotations

import os
import re

# role keyword patterns, tried in order on the side-stripped, lower-case name
_ROLES = [
    ("platform", r"platform|ik_|_ik|ikfoot|ikhand|pole|target|twist|roll|helper|corrective|socket|attach|weapon|prop"),
    ("finger", r"thumb|index|middle|ring|pinky|little|digit|finger"),
    ("toe", r"toe|ball"),
    ("foot", r"foot|ankle"),
    ("calf", r"calf|shin|lowerleg|knee|^leg$|leg_?low"),
    ("thigh", r"thigh|upleg|upperleg|femur|^leg_?up|^hip$"),
    ("hand", r"hand|wrist|palm"),
    ("forearm", r"forearm|lowerarm|elbow|^arm_?low"),
    ("upperarm", r"upperarm|uparm|^arm$|^arm_?up|^upper_?arm|^shoulder_?arm"),
    ("clavicle", r"clavicle|collar|^shoulder$"),
    ("head", r"^head$|^head_?\d*$|headtop"),
    ("neck", r"neck"),
    ("spine", r"spine|chest|ribcage|torso|^back|abdomen|waist"),
    ("pelvis", r"pelvis|^hips?$|^root_?hips|^bip01$"),
]
_FINGERS = {"thumb": "thumb", "index": "index", "middle": "middle", "ring": "ring", "pinky": "little",
            "little": "little"}
_DIGIT_ORDER = ["thumb", "index", "middle", "ring", "little"]


def _common_prefix(names: list[str]) -> str:
    """Prefix shared by the bone names ("Dwarf_", "Base-Human", "mixamorig:", "Bip01 "), stripped before matching."""
    if len(names) < 3:
        return ""
    return os.path.commonprefix(names)


def _side(raw: str) -> tuple[str, str]:
    """(side 'L'/'R'/'', name without the side marker)."""
    for side, word in (("L", "left"), ("R", "right")):
        # "LeftArm", "left_arm", "arm_left", "ArmLeft" (not "BeltTieLeft01": a side word inside a name is kept)
        m = re.search(rf"^{word}|{word}$", raw, re.I)
        if m:
            return side, (raw[:m.start()] + raw[m.end():]).strip("_ .:-")
    for side in ("L", "R"):
        for p in (rf"^{side}(?=[A-Z0-9_\s.])", rf"(?:^|[_\s.:-]){side}(?=[A-Z])", rf"[_\s.]{side}[_\s.]",
                  rf"[_\s.]{side}$", rf"[_\s.]{side.lower()}$", rf"(?<=[a-z]){side}(?=[A-Z][a-z])",
                  rf"(?<=[a-z0-9]){side}$"):
            m = re.search(p, raw)
            if m:
                return side, (raw[:m.start()] + raw[m.end():]).strip("_ .:-")
    return "", raw


def _role(core: str) -> str:
    c = re.sub(r"[^a-z0-9_]", "", core.lower().replace(" ", "_"))
    for role, pat in _ROLES:
        if re.search(pat, c):
            return role
    return ""


def canonical_names(names: list[str], parents: list[int], positions=None) -> list[str]:
    """Canonical (Glacier-style) name of every humanoid bone; the others keep their name. ``positions``
    (N,3) bind-pose joint positions, Z up, are used to tell the side when the name does not."""
    n = len(names)
    pre = _common_prefix(names)
    info = []
    for i, nm in enumerate(names):
        raw = (nm[len(pre):] if pre and nm.startswith(pre) else nm).strip("_ .:-")
        end = raw.lower().endswith("_end") or raw.lower().endswith("nub")
        base = re.sub(r"(_end|nub)$", "", raw, flags=re.I)
        side, core = _side(base)
        role = "end" if end else _role(core)
        info.append([side, core, role])
    children = [[] for _ in range(n)]
    for i, p in enumerate(parents):
        if 0 <= p < n:
            children[p].append(i)

    def under(i: int, role: str) -> bool:
        p = parents[i]
        while p >= 0:
            if info[p][2] == role:
                return True
            p = parents[p]
        return False

    # side from position when the name has none (limbs only), assuming the character faces +Y: left is -X
    if positions is not None:
        for i in range(n):
            if not info[i][0] and info[i][2] in ("clavicle", "upperarm", "forearm", "hand", "thigh", "calf", "foot", "toe", "finger"):
                x = float(positions[i][0])
                if abs(x) > 1e-4:
                    info[i][0] = "L" if x < 0 else "R"

    out = list(names)
    used: set[str] = set()

    def give(i: int, canon: str) -> None:
        if canon not in used:
            out[i] = canon
            used.add(canon)

    # pelvis: the first bone with that role (top-most), else the parent of both thighs
    pelvis = next((i for i in range(n) if info[i][2] == "pelvis"), -1)
    if pelvis < 0:
        thighs = [i for i in range(n) if info[i][2] == "thigh"]
        if thighs:
            pelvis = parents[thighs[0]]
    if pelvis >= 0:
        give(pelvis, "root")
    # limbs
    simple = {"clavicle": "clavicle", "upperarm": "shoulder", "forearm": "elbow", "hand": "wrist",
              "thigh": "femur", "calf": "knee", "foot": "ankle"}
    for i in range(n):
        side, core, role = info[i]
        if role in simple and side:
            if role == "upperarm" and under(i, "upperarm"):
                continue
            give(i, f"{side}_{simple[role]}")
        elif role == "toe" and side and not under(i, "toe"):
            give(i, f"{side}_ball")
        elif role == "finger" and side and under(i, "foot"):
            if not under(i, "finger"):
                give(i, f"{side}_ball")              # CAT toes are "digits" under the foot
    # fingers: name keyword, or CAT/Biped digit numbers (Digit<finger><segment>), else order under the hand
    for i in range(n):
        side, core, role = info[i]
        if role != "finger" or not side or under(i, "foot"):
            continue
        low = core.lower()
        finger, seg = None, None
        for k, v in _FINGERS.items():
            if k in low:
                finger = v
                break
        digits = re.findall(r"\d", low)
        if finger is None and "digit" in low and digits:
            finger = _DIGIT_ORDER[min(int(digits[0]), 5) - 1] if digits[0] != "0" else "thumb"
            seg = int(digits[1]) if len(digits) > 1 else None
        if finger is None:
            continue
        if seg is None:
            # segment = depth in its own finger chain
            seg, p = 1, parents[i]
            while p >= 0 and info[p][2] == "finger":
                seg, p = seg + 1, parents[p]
        if finger == "thumb":
            give(i, f"{side}_thumb_{seg - 1}")
        else:
            give(i, f"{side}_{finger}_{seg}")
    # spine: the chain from the pelvis up to the bone carrying the clavicles; three evenly spread joints
    clav = next((i for i in range(n) if out[i] == "L_clavicle"), -1)
    chain = []
    p = parents[clav] if clav >= 0 else -1
    while p >= 0 and p != pelvis:
        chain.append(p)
        p = parents[p]
    chain.reverse()
    if not chain:
        chain = [i for i in range(n) if info[i][2] == "spine"]
    if chain:
        picks = sorted({round(k * (len(chain) - 1) / 2) for k in range(3)})
        for k, idx in enumerate(picks):
            give(chain[idx], ["spine_02", "spine_03", "spine_04"][k + (3 - len(picks))])
    # neck / head: first neck bone above the spine, then the head
    neck = next((i for i in range(n) if info[i][2] == "neck"), -1)
    if neck >= 0:
        give(neck, "neck_01")
    head = next((i for i in range(n) if info[i][2] == "head"), -1)
    if head >= 0:
        give(head, "head")
    # eyes (eye position of the player model)
    for i in range(n):
        side, core, role = info[i]
        if side and re.fullmatch(r"eye\d*", core.lower() or ""):
            give(i, f"{side}_eye")
    # "_end" markers follow their bone's new name (tip direction of fingers and toes)
    for i in range(n):
        if info[i][2] == "end" and 0 <= parents[i] < n and out[parents[i]] != names[parents[i]]:
            give(i, out[parents[i]] + "_end")
    # keep the other names unique against the canonical ones
    given = {i for i in range(n) if out[i] != names[i]}
    seen: set[str] = set(out[i] for i in given)
    for i in range(n):
        if i in given:
            continue
        if out[i] in seen:
            out[i] = f"{out[i]}_{i}"
        seen.add(out[i])
    return out


def is_humanoid(canon: list[str]) -> bool:
    need = {"root", "L_femur", "R_femur", "L_shoulder", "R_shoulder", "head"}
    return need <= set(canon)
