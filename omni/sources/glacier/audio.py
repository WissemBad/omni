"""Every sound of a Glacier 2 game (007 First Light), with readable paths and tags.

Where the audio lives:
  WWES  streamed Wwise media, one .wem per resource (dialogue lines).
  WWEM  memory-resident Wwise media (sound effects, music, ambiences), one .wem per resource.
  WWEV  Wwise events: name, media embedded in the event, then references to WWEM (+ an optional prefetch copy
        of their first bytes, skipped: the full WWEM is exported).
  WBNK  sound banks: the Wwise hierarchy (HIRC) and sometimes embedded media (DIDX index + DATA).
  WSGB / WSWB  state / switch groups (their names and the names of their values).

How names are found, best first (the export keeps the best one, the others go to the index as aliases):
  0  the resource's own game path from the hash list ("sound/originals/voices/english(us)/...").
  1  the dialogue (DLGE) that plays a line: voices/<language>/<conversation>/<Wwise label>. A DLGE references
     each line once per language slot; the slot is the low bits of the reference flags (1 = english(us),
     confirmed by the hash list; 0 = the neutral "xx" track of Glacier, a separate recording).
  2  the event (WWEV) that plays a media: events/<event prefix>/<event name>/<Wwise label or media id>.
  3  media embedded in a bank: the bank hierarchy says which events reach it (event ids are FNV-1 hashes of
     the WWEV names) and which switch/state values select it on the way (FNV-1 of the WSGB/WSWB names):
     events/<prefix>/<event>/<state>/.../<label or id>. Bank names are recovered the same way (the bank id in
     its header is the FNV-1 of its name). Media no event reaches: banks/<bank name>/<label or id>.
  9  nothing references it: _unnamed/<type>/<hash>.
The Wwise label is the original file name stored in the media itself (RIFF LIST/labl).
"""
from __future__ import annotations

import json
import re
import struct
from concurrent.futures import ThreadPoolExecutor

from ...core.ir import SoundRef

_LANG = {0: "xx", 1: "english(us)"}


def _clean(s: str) -> str:
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s).strip(" .")
    return s or "_"


def _game_path(name: str) -> str:
    """'[assembly:/_knt/sound/originals/voices/english(us)/a/b.wav].wes' -> 'voices/english(us)/a/b'."""
    p = name.split("](")[0]
    p = re.sub(r"^\[|\]\.[a-z_]+$", "", p)
    p = re.sub(r"^[a-z]+:/", "", p)
    for prefix in ("_knt/sound/originals/", "_knt/sound/", "_pro/sound/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    p = re.sub(r"\.(wav|wem|ogg)$", "", p.split("?")[0])
    return "/".join(_clean(x) for x in p.split("/") if x)


def _conversation(name: str) -> str:
    """dialogue event name -> conversation folder ('ai_dialog/mercmrm04')."""
    p = name.split("](")[0].lstrip("[")
    m = re.search(r"/conversations/(.+?)\.sweetdialog", p)
    if not m:
        return ""
    parts = m.group(1).split("/")
    return "/".join(_clean(x) for x in parts[:-1]) if len(parts) > 1 else ""


_FILELIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{5,}$")


def _label(f, offset: int, size: int) -> str:
    """Wwise label (LIST/labl) of a .wem stored at file[offset:offset+size], reading only the chunk headers.
    labl is a cue label: on dialogue/sfx media it carries the original file name, on music it can be a marker
    ("Marker 2", "EXIT C"); only file-name-like labels are kept."""
    lab = _raw_label(f, offset, size)
    return lab if _FILELIKE.match(lab) and not lab.lower().startswith(("marker", "cue")) else ""


def _speaker(label: str) -> str:
    """'vox_cc_light_elbowdown_lh_civukf08_civukf08_003' -> 'civukf08' (line names end with speaker[_speaker]_NNN)."""
    t = label.lower().split("_")
    if len(t) >= 3 and t[-1].isdigit() and re.match(r"^[a-z]+\d*[a-z]*\d+$", t[-2]):
        return t[-2]
    return "_misc"


def _raw_label(f, offset: int, size: int) -> str:
    f.seek(offset)
    head = f.read(12)
    if head[:4] != b"RIFF":
        return ""
    end = offset + (size if size >= 0 else 8 + struct.unpack_from("<I", head, 4)[0])
    o = offset + 12
    while o + 8 <= end:
        f.seek(o)
        h = f.read(8)
        if len(h) < 8:
            break
        cid, sz = h[:4], struct.unpack_from("<I", h, 4)[0]
        if cid == b"LIST":
            body = f.read(min(sz, 2048))
            i = body.find(b"labl")
            if i >= 0:
                n = struct.unpack_from("<I", body, i + 4)[0]
                return body[i + 12:i + 8 + n].split(b"\0")[0].decode("latin-1").strip()
        o += 8 + sz + (sz & 1)
    return ""


def parse_event(d: bytes):
    """WWEV -> (name, [(media id, offset, size)] embedded, [(meta ref index, media id)] referenced)."""
    n = struct.unpack_from("<I", d, 0)[0]
    name = d[4:4 + n].split(b"\0")[0].decode("latin-1")
    o = 4 + n + 1 + 4 + 4                    # u8, f32 max attenuation, u32
    emb, refs = [], []
    count = struct.unpack_from("<I", d, o)[0]
    o += 4
    for _ in range(count):
        mid, _id2, sz = struct.unpack_from("<III", d, o)
        emb.append((mid, o + 12, sz))
        o += 12 + sz
    count = struct.unpack_from("<I", d, o)[0]
    o += 4
    for _ in range(count):
        ri, mid, _id2, pre = struct.unpack_from("<IIII", d, o)
        refs.append((ri, mid))
        o += 16 + pre                        # prefetch = first bytes of the referenced media: skipped
    return name, emb, refs


def parse_bank(d: bytes):
    """WBNK -> [(media id, offset, size)] of the media embedded in the bank (DIDX + DATA)."""
    o = d.find(b"BKHD")
    didx, data = [], -1
    while 0 <= o and o + 8 <= len(d):
        cid, sz = d[o:o + 4], struct.unpack_from("<I", d, o + 4)[0]
        if cid == b"DIDX":
            didx = [struct.unpack_from("<III", d, o + 8 + 12 * k) for k in range(sz // 12)]
        elif cid == b"DATA":
            data = o + 8
        o += 8 + sz
    return [(mid, data + off, size) for mid, off, size in didx] if data >= 0 else []


def fnv1(name: str) -> int:
    """Wwise short id of a name (FNV-1 32 bits of the lower-cased name)."""
    h = 2166136261
    for c in name.lower().encode("utf-8"):
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= c
    return h


_STRING = re.compile(rb"[A-Za-z][A-Za-z0-9_\- ]{2,63}")


def wwise_names(source) -> dict[int, str]:
    """FNV-1 id -> name of every switch/state group and value (strings of the WSGB/WSWB/WSGT/WSWT resources)."""
    out: dict[int, str] = {}
    for kind in ("WSGB", "WSWB", "WSGT", "WSWT"):
        for _h, p in source.archive.index(kind).items():
            try:
                d = p.read_bytes()
            except OSError:
                continue
            for m in _STRING.finditer(d):
                s = m.group().decode("ascii")
                out.setdefault(fnv1(s), s)
    return out


def bank_names(source, bank_ids: dict[int, int], events: list[str], cache) -> dict[int, str]:
    """Resource hash -> bank name: game path when the hash list has it, else the name whose FNV-1 is the bank id,
    searched among words of event names and mission names, with the usual prefixes (cin_, sm_, env_, mx_, veh_...).
    Cached in ``cache`` (a path) once found."""
    try:
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    except (OSError, ValueError):
        cached = {}
    out: dict[int, str] = {}
    want: dict[int, int] = {}
    for h, bid in bank_ids.items():
        n = source.names.name(h)
        if n:
            out[h] = _game_path(n).split("/")[-1].replace(".wwisesoundbank", "")
        elif str(bid) in cached:
            out[h] = cached[str(bid)]
        else:
            want[bid] = h
    if want:
        words: set[str] = set()
        for ev in events:
            parts = ev.lower().split("_")
            words.update(parts)
            words.update("_".join(parts[:i]) for i in range(1, len(parts) + 1))
        try:
            words.update(c.get("mission", "") for c in source.characters())
        except Exception:  # noqa: BLE001 - characters are optional
            pass
        words.discard("")
        prefixes = ["", "cin_", "sm_", "env_", "mx_", "veh_", "veh_npc_", "amb_", "vo_", "ui_", "sfx_", "fol_",
                    "wpn_", "fa_", "gad_", "npc_", "char_", "mix_", "dlg_", "music_", "level_", "global_"]
        for w in words:
            if not re.search("[a-z]", w):
                continue
            for p in prefixes:
                bid = fnv1(p + w)
                if bid in want and want[bid] not in out:
                    out[want[bid]] = p + w
                    cached[str(bid)] = p + w
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(cached, indent=1), encoding="utf-8")
        except OSError:
            pass
    return out


def iter_sounds(source, progress=print) -> list[SoundRef]:
    from ...core.config import CONFIG
    from ...native import N
    a, names = source.archive, source.names
    wes, wem = a.index("WWES"), a.index("WWEM")
    refs: list[SoundRef] = []

    def label_of(path, offset=0, size=-1):
        with open(path, "rb") as f:
            return _label(f, offset, size)

    # labels of every standalone media (cheap: chunk headers only), in parallel
    with ThreadPoolExecutor(16) as ex:
        labels = dict(zip(list(wes) + list(wem), ex.map(label_of, list(wes.values()) + list(wem.values()))))
    progress(f"labels read: {len(labels)}")

    # 0 - own game path
    for h, p in wes.items():
        n = names.name(h)
        if n:
            gp = _game_path(n)
            lang = gp.split("/")[1] if gp.startswith("voices/") and "/" in gp[7:] else ""
            refs.append(SoundRef(gp, str(p), source_id=f"WWES:{h:016X}", priority=0,
                                 meta={"title": labels.get(h) or gp.split("/")[-1], "genre": "dialogue" if lang else "",
                                       "language": lang, "album": gp.rsplit("/", 1)[0]}))

    # 1 - dialogue lines
    for h, p in a.index("DLGE").items():
        m = a.meta(p)
        if not m:
            continue
        conv = _conversation(names.name(h))
        for r, fl in m.refs:
            if r in wes:
                lang = _LANG.get(fl & 0x7F, f"lang{fl & 0x7F}")
                stem = labels.get(r) or f"{r:016X}"
                spk = _speaker(stem)
                folder = conv or f"_by_speaker/{spk}"
                path = f"voices/{lang}/{folder}/{_clean(stem)}"
                refs.append(SoundRef(path, str(wes[r]), source_id=f"WWES:{r:016X}", priority=1,
                                     meta={"title": stem, "album": conv or "", "artist": "" if spk == "_misc" else spk,
                                           "language": lang, "genre": "dialogue"}))
    progress(f"dialogues scanned: {len(refs)} refs")

    # event names first (their FNV-1 ids are what the bank hierarchy references)
    events: list[tuple[int, str, bytes]] = []
    for h, p in a.index("WWEV").items():
        d = p.read_bytes()
        try:
            ev = parse_event(d)[0]
        except (struct.error, IndexError):
            continue
        events.append((h, ev or names.name(h) or f"{h:016X}", d))
    event_names = [e for _h, e, _d in events]
    ev_by_id = {fnv1(e): e for e in event_names}
    # Wwise hierarchy of the banks: which events (and switch/state values) reach each media id
    banks = [(h, p.read_bytes()) for h, p in a.index("WBNK").items()]
    switch = wwise_names(source)
    links: dict[int, list] = {}
    bank_id: dict[int, int] = {}
    try:
        ids, _media, lk, _sourced, nev = N.bank_links(banks, list(switch))
        bank_id = dict(ids)
        links = dict(lk)
        progress(f"bank hierarchy: {nev} events, {len(links)} media reached")
    except Exception as e:  # noqa: BLE001 - naming falls back to bank/id
        progress(f"bank hierarchy unreadable: {e}")
    # 2 - events: embedded media + referenced WWEM (music under switches gets its state path)
    def states_of(mid: int, ev: str) -> list[str]:
        e = fnv1(ev)
        for eid, path in links.get(mid, []):
            if eid == e:
                return [_clean(switch[k]) for k in path if k in switch]
        return []

    for h, ev, d in events:
        p = a.find("WWEV", h)
        _ev, emb, wrefs = parse_event(d)
        group = _clean(ev.split("_")[0].lower()) if "_" in ev else "misc"
        base = f"events/{group}/{_clean(ev)}"
        m = a.meta(p)
        mrefs = m.refs if m else []
        media = [(_label_bytes(d, off, sz), str(p), off, sz, f"WWEV:{h:016X}#{i}", mid) for i, (mid, off, sz) in enumerate(emb)]
        for ri, mid in wrefs:
            r = mrefs[ri][0] if ri < len(mrefs) else None
            if r in wem:
                media.append((labels.get(r), str(wem[r]), 0, -1, f"WWEM:{r:016X}", mid))
        # unlabelled media take the event's name (numbered when the event plays several)
        for k, (lab, file, off, sz, sid, mid) in enumerate(media):
            states = states_of(mid, ev)
            stem = lab or (f"{mid:08x}" if states else ev if len(media) == 1 else f"{ev}_{k + 1:03d}")
            refs.append(SoundRef("/".join([base, *states, _clean(stem)]), file, off, sz, sid, 2,
                                 meta={"title": lab or " ".join([ev, *states]), "album": ev, "genre": "event"}))
    progress(f"events scanned: {len(refs)} refs")

    # 3 - banks: embedded media, named through the Wwise hierarchy
    bnames = bank_names(source, bank_id, event_names, CONFIG.cache / f"wwise_banks_{source.id}.json") if bank_id else {}
    for h, d in banks:
        bank = _clean(bnames.get(h) or (_game_path(names.name(h)).split("/")[-1] if names.name(h) else f"{h:016X}"))
        for i, (mid, off, sz) in enumerate(parse_bank(d)):
            lab = _label_bytes(d, off, sz)
            stem = lab or f"{mid:08x}"
            hit = next(((e, path) for e, path in links.get(mid, []) if e in ev_by_id), None)
            if hit:
                ev = ev_by_id[hit[0]]
                group = _clean(ev.split("_")[0].lower()) if "_" in ev else "misc"
                states = [_clean(switch[k]) for k in hit[1] if k in switch]
                path = "/".join([f"events/{group}/{_clean(ev)}", *states, _clean(stem)])
                meta = {"title": lab or (f"{ev} {' '.join(states)}".strip()), "album": ev, "genre": "event",
                        "comment": f"bank {bank}"}
                refs.append(SoundRef(path, str(a.find("WBNK", h)), off, sz, f"WBNK:{h:016X}#{i}", 3, meta))
            else:
                refs.append(SoundRef(f"banks/{bank}/{_clean(stem)}", str(a.find("WBNK", h)), off, sz,
                                     f"WBNK:{h:016X}#{i}", 4, {"title": lab or stem, "album": bank, "genre": "bank"}))

    # 9 - anything not reached
    seen = {r.file for r in refs if r.size < 0}
    for kind, idx in (("WWES", wes), ("WWEM", wem)):
        for h, p in idx.items():
            if str(p) not in seen:
                stem = labels.get(h) or f"{h:016X}"
                refs.append(SoundRef(f"_unnamed/{kind.lower()}/{_clean(stem)}", str(p), source_id=f"{kind}:{h:016X}",
                                     priority=9, meta={"title": stem}))
    progress(f"total refs: {len(refs)}")
    return refs


def _label_bytes(d: bytes, off: int, size: int) -> str:
    import io
    return _label(io.BytesIO(d), off, size)
