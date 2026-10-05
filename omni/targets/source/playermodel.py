"""Garry's Mod player models from skinned characters ("proportion trick").

The character keeps its OWN skeleton, proportions and skin weights. Its rig is mapped bone by bone onto
ValveBiped (the skeleton of the stock player models, models/player/group01/male_01.mdl by default), so
that GMod's animation set (m_anm.mdl / f_anm.mdl, included at compile time) drives it:

1. fit_pose: the character's skeleton is posed (forward kinematics, bone lengths unchanged) so that its limb
   segments point like the ValveBiped reference pose; the mesh follows by linear-blend skinning with its own
   weights.
2. proportions: a ValveBiped skeleton whose bones keep the reference orientations but sit on the character's
   posed joints. The mesh is bound to it (weights folded onto the ValveBiped bones).
3. QC: the reference skeleton and this proportions skeleton are compiled as a "subtract" delta played
   "predelta autoplay": every stock animation then plays with the character's bone offsets instead of the
   stock ones, i.e. exactly the same motion on the character's own proportions.

The template also provides hitboxes (refitted to the mesh), attachments, IK chains and ragdoll constraints.
Character bones with no ValveBiped equivalent (twists, face, correctives, cloth) give their weights to the
closest mapped ancestor.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.config import CONFIG
from ...core.ir import SubMesh
from .mdlread import MdlInfo, quat_to_mat, read_mdl, world_transforms

MAX_LINKS = 3                      # studiomdl: at most 3 bone weights per vertex
UNITS_PER_METER = 39.37

TEMPLATES = {
    "male": ("models/player/group01/male_01", "models/m_anm.mdl"),
    "female": ("models/player/group01/female_01", "models/f_anm.mdl"),
}

# source (Glacier) bone -> ValveBiped suffix. "L_"/"R_" prefixes are matched on both sides.
# Matched by joint HEIGHT, not by name: Glacier's spine_02/03/04 sit where ValveBiped's Spine/Spine1/Spine2 do
# (spine_01 coincides with the pelvis; Valve's upper-chest Spine4 has no Glacier counterpart and stays unweighted).
_CENTER = {"root": "Pelvis", "spine_02": "Spine", "spine_03": "Spine1", "spine_04": "Spine2",
           "neck_01": "Neck1", "head": "Head1"}
_SIDED = {"clavicle": "Clavicle", "shoulder": "UpperArm", "elbow": "Forearm", "wrist": "Hand",
          "femur": "Thigh", "knee": "Calf", "ankle": "Foot", "ball": "Toe0",
          "thumb_0": "Finger0", "thumb_1": "Finger01", "thumb_2": "Finger02",
          "index_1": "Finger1", "index_2": "Finger11", "index_3": "Finger12",
          "middle_1": "Finger2", "middle_2": "Finger21", "middle_3": "Finger22",
          "ring_1": "Finger3", "ring_2": "Finger31", "ring_3": "Finger32",
          "little_1": "Finger4", "little_2": "Finger41", "little_3": "Finger42"}


def valve_name_for(src_name: str) -> str | None:
    if src_name in _CENTER:
        return "ValveBiped.Bip01_" + _CENTER[src_name]
    m = re.match(r"^([LR])_(.+)$", src_name)
    if m and m.group(2) in _SIDED:
        return f"ValveBiped.Bip01_{m.group(1)}_{_SIDED[m.group(2)]}"
    return None


# ------------------------------------------------------------------------------------ template
@dataclass
class Template:
    kind: str
    info: MdlInfo
    world: list                       # 4x4 bone-to-model matrices (Source units)
    anim_include: str
    solids: list = field(default_factory=list)        # [{index,name,parent,mass,...}]
    constraints: list = field(default_factory=list)   # [{parent,child,xmin,...}]
    edit: dict = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return [b.name for b in self.info.bones]

    def index(self, name: str) -> int:
        return self.names.index(name)


def _vpk():
    import vpk
    return vpk.open(str(CONFIG.gmod / "garrysmod" / "garrysmod_dir.vpk"))


def load_template(kind: str = "male") -> Template:
    stem, anim = TEMPLATES[kind]
    pk = _vpk()
    info = read_mdl(pk[stem + ".mdl"].read())
    t = Template(kind, info, world_transforms(info.bones), anim)
    txt = pk[stem + ".phy"].read()
    txt = txt[txt.find(b"solid {"):].decode("latin-1")
    for tag, body in re.findall(r"(\w+) \{(.*?)\}", txt, re.S):
        kv = dict(re.findall(r'"(\w+)" "([^"]*)"', body))
        if tag == "solid":
            t.solids.append(kv)
        elif tag == "ragdollconstraint":
            t.constraints.append(kv)
        elif tag == "editparams":
            t.edit = kv
    return t


# ------------------------------------------------------------------------------------ math
def _rot_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Minimal rotation taking unit vector a to unit vector b."""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        axis = np.cross(a, [1, 0, 0] if abs(a[0]) < 0.9 else [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _fit_rotation(src: list[np.ndarray], dst: list[np.ndarray]) -> np.ndarray:
    pairs = [(s / np.linalg.norm(s), d / np.linalg.norm(d)) for s, d in zip(src, dst)
             if np.linalg.norm(s) > 1e-6 and np.linalg.norm(d) > 1e-6]
    if not pairs:
        return np.eye(3)
    if len(pairs) == 1:
        return _rot_between(*pairs[0])
    H = sum(np.outer(s, d) for s, d in pairs)
    U, _S, Vt = np.linalg.svd(H)
    R = Vt.T @ np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))]) @ U.T
    return R


# ------------------------------------------------------------------------------------ rig
Z180 = np.diag([-1.0, -1.0, 1.0])       # source characters face +Y, the ValveBiped reference faces -Y


def to_target(W: np.ndarray) -> np.ndarray:
    """Bone matrix from the source mesh frame (metres, faces +Y) to the Source frame (units, faces -Y).
    Bone frames stay orthonormal: the unit change is carried by the translation only."""
    out = np.eye(4)
    out[:3, :3] = Z180 @ W[:3, :3]
    out[:3, 3] = (Z180 @ W[:3, 3]) * UNITS_PER_METER
    return out


@dataclass
class Skeleton:
    """The character's own skeleton, merged over all parts (by bone name), in the Source frame."""
    names: list[str]
    parents: list[int]
    bind: np.ndarray                  # (N,4,4) bind pose
    index: dict = field(default_factory=dict)


def merge_rigs(rigs: list) -> Skeleton:
    W, par = {}, {}
    for rig in rigs:
        world = rig.world()
        for i, nm in enumerate(rig.names):
            if nm not in W:
                W[nm] = to_target(world[i])
                p = rig.parents[i]
                par[nm] = rig.names[p] if p >= 0 else None

    def depth(nm):
        d = 0
        while par[nm] is not None:
            nm, d = par[nm], d + 1
        return d
    names = sorted(W, key=depth)                       # stable: parents always before children
    index = {nm: i for i, nm in enumerate(names)}
    parents = [index[par[nm]] if par[nm] is not None else -1 for nm in names]
    return Skeleton(names, parents, np.array([W[nm] for nm in names]), index)


# segments re-aimed onto the ValveBiped reference pose. Clavicles, spine, neck and head keep the character's
# own posture: they barely differ, and re-aiming them would move the shoulders/jacket.
_ALIGN = re.compile(r"_(UpperArm|Forearm|Hand|Thigh|Calf|Foot|Toe0|Finger\d+)$")


def fit_pose(sk: Skeleton, tpl: Template) -> tuple[np.ndarray, dict]:
    """Pose the character's OWN skeleton (bone lengths untouched) so that every limb segment points the way the
    ValveBiped reference does. Forward kinematics, top-down: each mapped limb bone gets the extra rotation aiming
    its segment (this joint -> child joint) at the reference direction; everything below follows.
    Returns (posed bone matrices (N,4,4), {valve bone index: character bone name})."""
    tn = tpl.names
    Vw = np.array(tpl.world)
    src_of = {}
    for nm in sk.names:
        vn = valve_name_for(nm)
        if vn in tn:
            src_of[tn.index(vn)] = nm
    mapped = {nm: j for j, nm in src_of.items()}
    B = sk.bind
    Binv = np.linalg.inv(B)
    P = np.zeros_like(B)
    for i, nm in enumerate(sk.names):
        p = sk.parents[i]
        P[i] = P[p] @ Binv[p] @ B[i] if p >= 0 else B[i]
        j = mapped.get(nm)
        if j is None or not _ALIGN.search(tn[j]):
            continue
        rel = P[i] @ Binv[i]                                   # bind -> posed for anything rigidly below
        s_dirs, d_dirs = [], []
        for cj, cb in enumerate(tpl.info.bones):
            if cb.parent == j and cj in src_of:
                c = sk.index[src_of[cj]]
                s_dirs.append((rel @ B[c])[:3, 3] - P[i, :3, 3])
                d_dirs.append(Vw[cj, :3, 3] - Vw[j, :3, 3])
        if not s_dirs:                                         # tip bone (finger tip, toe): aim its "_end" marker
            e = sk.index.get(nm + "_end")
            if e is None:
                continue
            s_dirs = [(rel @ B[e])[:3, 3] - P[i, :3, 3]]
            d_dirs = [Vw[j, :3, 0]]                            # ValveBiped bones point along their local +X
        if len(s_dirs) > 1 and not tn[j].endswith("_Hand"):
            s_dirs, d_dirs = [np.mean(s_dirs, 0)], [np.mean(d_dirs, 0)]
        # the hand fits all knuckles at once, which also sets the palm orientation
        P[i, :3, :3] = _fit_rotation(s_dirs, d_dirs) @ P[i, :3, :3]
    return P, src_of


def proportions(tpl: Template, sk: Skeleton, P: np.ndarray, src_of: dict) -> np.ndarray:
    """The ValveBiped skeleton re-proportioned to the character ("proportion trick"): every bone keeps the
    reference ORIENTATION (so the stock animations' rotations mean the same thing) but sits on the character's
    posed joint. Bones without counterpart keep their reference offset from their parent; Spine4 (no Glacier
    equivalent) sits between Spine2 and the neck at the reference ratio."""
    tn = tpl.names
    Vw = np.array(tpl.world)
    S = np.zeros_like(Vw)
    for j, b in enumerate(tpl.info.bones):
        S[j, :3, :3] = Vw[j, :3, :3]
        S[j, 3, 3] = 1.0
        nm = src_of.get(j)
        if nm is not None:
            S[j, :3, 3] = P[sk.index[nm], :3, 3]
        elif tn[j] == "ValveBiped.Bip01_Spine4" and tpl.index("ValveBiped.Bip01_Neck1") in src_of:
            s2, s4, n1 = (tpl.index("ValveBiped.Bip01_" + x) for x in ("Spine2", "Spine4", "Neck1"))
            a = np.linalg.norm(Vw[s4, :3, 3] - Vw[s2, :3, 3])
            c = np.linalg.norm(Vw[n1, :3, 3] - Vw[s4, :3, 3])
            neck = P[sk.index[src_of[n1]], :3, 3]
            S[j, :3, 3] = S[s2, :3, 3] + a / (a + c) * (neck - S[s2, :3, 3])
        else:
            local = np.linalg.inv(Vw[b.parent]) @ Vw[j]
            S[j, :3, 3] = (S[b.parent] @ local)[:3, 3]
    return S


def weight_targets(sk: Skeleton, tpl: Template) -> np.ndarray:
    """Character bone -> ValveBiped bone receiving its skin weights: itself if mapped, else its closest mapped
    ancestor (twists, correctives, face, cloth helpers...)."""
    tn = tpl.names
    direct = []
    for nm in sk.names:
        v = valve_name_for(nm)
        direct.append(tn.index(v) if v in tn else -1)
    out = np.zeros(len(sk.names), np.int64)
    for i in range(len(sk.names)):
        j = i
        while j >= 0 and direct[j] < 0:
            j = sk.parents[j]
        out[i] = direct[j] if j >= 0 else tn.index("ValveBiped.Bip01_Pelvis")
    return out


@dataclass
class PosedPart:
    positions: np.ndarray             # (N,3) Source units, fitted pose
    normals: np.ndarray
    bones: np.ndarray                 # (N,MAX_LINKS) template bone indices
    weights: np.ndarray               # (N,MAX_LINKS)
    uvs: np.ndarray
    indices: np.ndarray
    material_key: str


def skin_submesh(sm: SubMesh, rig, sk: Skeleton, P: np.ndarray, S: np.ndarray, targets: np.ndarray,
                 tpl: Template) -> PosedPart:
    """Linear-blend skinning of the part from its bind pose to the fitted pose (the character's own weights,
    all 4 influences), then its weights are folded onto the ValveBiped bones (top MAX_LINKS)."""
    v = (sm.positions.astype(np.float64) @ Z180.T) * UNITS_PER_METER
    n = sm.normals.astype(np.float64) @ Z180.T
    nv = len(v)
    pelvis = tpl.index("ValveBiped.Bip01_Pelvis")
    if sm.joints is None or sm.weights is None:              # rigid part: no skin, follows the pelvis
        b = np.full((nv, MAX_LINKS), pelvis)
        w = np.zeros((nv, MAX_LINKS))
        w[:, 0] = 1.0
        return PosedPart(v, n, b, w, sm.uvs, sm.indices, sm.material_key)
    local_to_sk = np.array([sk.index[nm] for nm in rig.names])
    joints = local_to_sk[np.clip(sm.joints.astype(np.int64), 0, len(rig.names) - 1)]
    w = sm.weights.astype(np.float64)
    tot = w.sum(1, keepdims=True)
    w = np.where(tot > 1e-6, w / np.maximum(tot, 1e-6), 0.0)
    w[tot[:, 0] <= 1e-6, 0] = 1.0
    K = P @ np.linalg.inv(sk.bind)                           # bind -> posed, per character bone
    from ...native import N
    new_v, new_n = N.lbs(np.ascontiguousarray(v), np.ascontiguousarray(n), np.ascontiguousarray(joints, np.int64),
                         np.ascontiguousarray(w), np.ascontiguousarray(K, np.float64))

    # fold the influences onto ValveBiped bones
    tb = targets[joints]                                     # (N,4)
    tw = w.copy()
    # Glacier has a single upper-chest bone where ValveBiped has Spine2 + Spine4 (which carries the
    # clavicles and the neck): Spine2 influence fades into Spine4 with height, as on the stock models.
    s2, s4 = tpl.index("ValveBiped.Bip01_Spine2"), tpl.index("ValveBiped.Bip01_Spine4")
    z2, z4 = S[s2, 2, 3], S[s4, 2, 3]
    f = np.clip((new_v[:, 2] - z2) / max(z4 - z2, 1e-3), 0.0, 1.0)
    f = (f * f * (3 - 2 * f))[:, None]
    is2 = tb == s2
    tb = np.concatenate([tb, np.where(is2, s4, tb)], 1)
    tw = np.concatenate([np.where(is2, tw * (1 - f), tw), np.where(is2, tw * f, 0.0)], 1)
    out_b, out_w = N.fold_weights(np.ascontiguousarray(tb, np.int64), np.ascontiguousarray(tw, np.float64),
                                  MAX_LINKS, pelvis)
    return PosedPart(new_v, new_n, out_b, out_w, sm.uvs, sm.indices, sm.material_key)


# ------------------------------------------------------------------------------------ SMD / QC
def quat_to_radian_euler(q: np.ndarray) -> tuple[float, float, float]:
    """Inverse of Source's AngleQuaternion(RadianEuler) -> (roll x, pitch y, yaw z)."""
    R = quat_to_mat(q)
    pitch = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    if abs(R[2, 0]) < 0.999999:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(R[1, 0], R[0, 0])
    else:
        roll = math.atan2(-R[1, 2], R[1, 1])
        yaw = 0.0
    return roll, pitch, yaw


def skeleton_block(tpl: Template, world: np.ndarray | None = None) -> str:
    """SMD header (nodes + one skeleton frame) of the ValveBiped hierarchy, posed by ``world`` (bone-to-model
    matrices), or the template's own reference pose when None."""
    W = np.array(tpl.world) if world is None else world
    lines = ["version 1", "nodes"]
    for i, b in enumerate(tpl.info.bones):
        lines.append(f'{i} "{b.name}" {b.parent}')
    lines += ["end", "skeleton", "time 0"]
    for i, b in enumerate(tpl.info.bones):
        local = np.linalg.inv(W[b.parent]) @ W[i] if b.parent >= 0 else W[i]
        r, p, y = quat_to_radian_euler(_mat_to_quat(local[:3, :3]))
        lines.append("%d %.6f %.6f %.6f %.6f %.6f %.6f" % (i, *local[:3, 3], r, p, y))
    lines.append("end")
    return "\n".join(lines) + "\n"


def write_skeleton(path: Path, tpl: Template, world: np.ndarray | None = None) -> None:
    path.write_text(skeleton_block(tpl, world), encoding="ascii")


def write_reference(path: Path, tpl: Template, world: np.ndarray, parts: list[PosedPart],
                    material_names: dict[str, str]) -> int:
    out = [skeleton_block(tpl, world), "triangles\n"]
    count = 0
    from ...native import N
    for pp in parts:
        mat = material_names.get(pp.material_key, "default")
        uv = pp.uvs.astype(np.float64)
        uv[:, 1] = 1.0 - uv[:, 1]
        text, c = N.smd_triangles(
            mat, np.ascontiguousarray(pp.positions, np.float64), np.ascontiguousarray(pp.normals, np.float64), uv,
            np.ascontiguousarray(pp.bones, np.int64), np.ascontiguousarray(pp.weights, np.float64),
            np.ascontiguousarray(pp.indices, np.uint32))
        out.append(text)
        count += c
    out.append("end\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(out), encoding="ascii")
    return count


def _owner(tpl: Template, keep: set[int]) -> dict[int, int]:
    """Bone -> closest ancestor-or-self in ``keep`` (pelvis when none)."""
    owner = {}
    for i, b in enumerate(tpl.info.bones):
        j = i
        while j >= 0 and j not in keep:
            j = tpl.info.bones[j].parent
        owner[i] = j if j >= 0 else tpl.index("ValveBiped.Bip01_Pelvis")
    return owner


def _points_by_owner(parts: list[PosedPart], owner: dict[int, int]) -> dict[int, np.ndarray]:
    pts: dict[int, list[np.ndarray]] = {}
    for pp in parts:
        dom = pp.bones[np.arange(len(pp.bones)), pp.weights.argmax(1)]
        for bone in np.unique(dom):
            pts.setdefault(owner[int(bone)], []).append(pp.positions[dom == bone])
    return {b: np.concatenate(c) for b, c in pts.items()}


def write_physics(path: Path, tpl: Template, world: np.ndarray, parts: list[PosedPart]) -> dict:
    """One convex hull per ragdoll solid, built from the vertices that follow that bone."""
    from .collision import convex_hull
    owner = _owner(tpl, {tpl.index(s["name"]) for s in tpl.solids})
    hulls = {}
    for bone, P in _points_by_owner(parts, owner).items():
        if len(P) > 4000:
            P = P[:: len(P) // 4000 + 1]
        h = convex_hull(P)
        if h:
            hulls[bone] = h
    lines = [skeleton_block(tpl, world), "triangles\n"]
    for bone, (v, f, _vol) in hulls.items():
        # vertices of a physics piece are weighted 100% to the solid's bone
        for tri in f:
            lines.append("phys\n")
            for k in tri:
                lines.append("%d %.5f %.5f %.5f 0 0 1 0 0 1 %d 1.0\n" % (bone, *v[k], bone))
    lines.append("end\n")
    path.write_text("".join(lines), encoding="ascii")
    return {tpl.names[b]: h[2] for b, h in hulls.items()}


def collision_qc(tpl: Template, volumes: dict, total_mass: float = 90.0) -> list[str]:
    out = ['$collisionjoints "phys.smd"', "{", f"\t$mass {total_mass:.1f}", "\t$inertia 10", "\t$damping 0.01",
           "\t$rotdamping 1.5", f'\t$rootbone "{tpl.edit.get("rootname", "valvebiped.bip01_pelvis")}"']
    jm = tpl.edit.get("jointmerge", "")
    if "," in jm:
        a, b = jm.split(",", 1)
        out.append(f'\t$jointmerge "{a}" "{b}"')
    idx_name = {int(s["index"]): s["name"] for s in tpl.solids}
    for c in tpl.constraints:
        child = idx_name[int(c["child"])]
        for ax in "xyz":
            out.append('\t$jointconstrain "%s" %s limit %s %s %s' % (child, ax, c[ax + "min"], c[ax + "max"], c[ax + "friction"]))
    out.append("}")
    return out


def hitbox_qc(tpl: Template, world: np.ndarray, parts: list[PosedPart]) -> list[str]:
    """The template's hitboxes (same bones and groups), each box refitted to the character's own geometry in
    that bone's frame (0.5-99.5 percentile, so a stray vertex does not inflate it). A bone with too little
    geometry keeps the stock box."""
    hb_bones = {h.bone for h in tpl.info.hitboxes}
    pts = _points_by_owner(parts, _owner(tpl, hb_bones))
    out = ['$hboxset "default"']
    for h in tpl.info.hitboxes:
        lo, hi = h.bbmin, h.bbmax
        P = pts.get(h.bone)
        if P is not None and len(P) >= 30:
            L = (np.c_[P, np.ones(len(P))] @ np.linalg.inv(world[h.bone]).T)[:, :3]
            lo, hi = np.percentile(L, 0.5, 0), np.percentile(L, 99.5, 0)
        out.append('$hbox %d "%s" %.3f %.3f %.3f %.3f %.3f %.3f' % (h.group, tpl.names[h.bone], *lo, *hi))
    return out


def attachment_qc(tpl: Template, overrides: dict | None = None) -> list[str]:
    """The template's attachments (weapons, view, effects rely on them); ``overrides`` replaces the offset
    (bone frame) of some of them, e.g. the eyes measured on the character's head."""
    overrides = overrides or {}
    out = []
    for a in tpl.info.attachments:
        bone = tpl.info.bones[a.bone].name
        r, p, y = quat_to_radian_euler(_mat_to_quat(a.local[:, :3]))
        pos = overrides.get(a.name, a.local[:, 3])
        out.append('$attachment "%s" "%s" %.3f %.3f %.3f rotate %.3f %.3f %.3f' % (
            a.name, bone, *pos, math.degrees(r), math.degrees(p), math.degrees(y)))
    return out


def ik_qc(tpl: Template) -> list[str]:
    """IK chains + autoplay locks of the stock model: the included animations carry IK rules (feet planted on
    the ground, hands on weapons) that refer to these chains by index, so they are declared in the same order."""
    out = []
    for name, links, knee in tpl.info.ikchains:
        out.append('$ikchain "%s" "%s" knee %.3f %.3f %.3f' % (name, tpl.names[links[-1]], *knee))
    for chain, pw, qw in tpl.info.iklocks:
        out.append('$ikautoplaylock "%s" %g %g' % (tpl.info.ikchains[chain][0], pw, qw))
    return out


BONE_USED_BY_BONE_MERGE = 0x00040000


def bonemerge_qc(tpl: Template) -> list[str]:
    """Bones flagged for bone-merge on the stock model (weapons and accessories attach to them)."""
    return ['$bonemerge "%s"' % b.name for b in tpl.info.bones if b.flags & BONE_USED_BY_BONE_MERGE]


def _mat_to_quat(R: np.ndarray) -> np.ndarray:
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1) * 2
        return np.array([(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, s / 4])
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(R[i, i] - R[j, j] - R[k, k] + 1) * 2
    q = np.zeros(4)
    q[i] = s / 4
    q[3] = (R[k, j] - R[j, k]) / s
    q[j] = (R[j, i] + R[i, j]) / s
    q[k] = (R[k, i] + R[i, k]) / s
    return q
