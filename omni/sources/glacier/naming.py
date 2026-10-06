"""Readable identity of an outfit family: where it belongs, what it is, and what the game itself calls it.

A family is named ``<kind>_<mission>_<role>_<title...>_<body>`` (``outfit_bluebell_civ_greenway_elevator_male_reg``).
Two spellings break the plain reading: James Bond's outfits put ``hero_bond`` anywhere after the mission
(``..._ivy_nojacket_hero_bond_...``) and rewards are prefixed ``reward_`` (``outfit_reward_dahlia_hero_bond``).
The game also keeps an enumeration of Bond's outfit variations (``bond_outfit_variations``, an ENUM resource):
a family whose normalised name equals a member is tagged with that member and its index.
"""
from __future__ import annotations

import re

_PSEUDO_MISSIONS = {"reward", "global", "hero"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def known_missions(families: list[str], bodies: dict[str, str]) -> set[str]:
    """Mission codenames: the first word after the kind in at least three families."""
    count: dict[str, int] = {}
    for f in families:
        toks = _tokens(f, bodies.get(f, ""))
        if len(toks) > 1 and toks[1] not in _PSEUDO_MISSIONS:
            count[toks[1]] = count.get(toks[1], 0) + 1
    return {m for m, n in count.items() if n >= 3}


def _tokens(family: str, body: str) -> list[str]:
    head = family[: family.rfind(body)] if body and body in family else family
    return [t for t in head.strip("_").split("_") if t]


def split_family(family: str, body: str, missions: set[str] | frozenset[str] = frozenset()) -> dict:
    """``{kind, mission, role, title, reward, bond}`` of a family name."""
    toks = _tokens(family, body)
    kind = toks[0] if toks else ""
    rest = toks[1:]
    reward = False
    if rest and rest[0] == "reward":
        reward = True
        rest = rest[1:]
        if not (rest and rest[0] in missions):
            rest = ["reward", *rest]      # a reward of no mission: kept under a mission of its own
    mission = rest[0] if rest else ""
    rest = rest[1:]
    bond = False
    for i in range(len(rest) - 1):
        if rest[i] == "hero" and rest[i + 1] == "bond":
            bond = True
            rest = ["hero", "bond", *rest[:i], *rest[i + 2:]]
            break
    if bond:
        role, title = "hero", " ".join(rest[1:])
    else:
        role = rest[0] if len(rest) > 1 else ""
        title = " ".join(rest[1:] if role else rest)
    return {"kind": kind, "mission": mission, "role": role, "title": title.strip() or mission or family,
            "reward": reward or "reward" in toks, "bond": bond}


def bond_variations(enums: dict[str, dict]) -> dict[str, tuple[int, str]]:
    """normalised variation name -> (index, member) of the game's ``bond_outfit_variations``."""
    out: dict[str, tuple[int, str]] = {}
    for member, value in enums.get("bond_outfit_variations", {}).get("members", ()):
        m = re.match(r"bond_v?\d+_(.*)$", member, re.I)
        if m:
            out.setdefault(_norm(re.sub(r"^reward_", "", m.group(1), flags=re.I)), (value, member))
    return out


def variation_of(family: str, body: str, variations: dict[str, tuple[int, str]]) -> tuple[int, str] | None:
    """The enumeration member that names a Bond outfit family, when its name equals one exactly."""
    toks = [t for t in _tokens(family, body)[1:] if t not in ("hero", "bond", "reward")]
    return variations.get(_norm("".join(toks)))
