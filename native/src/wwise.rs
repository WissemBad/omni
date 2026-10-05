//! Wwise sound banks (BKHD/HIRC, bank version 150): who plays which media.
//!
//! Media embedded in banks carry no name. The bank hierarchy does say which Event reaches them:
//!   Event (4) -> Actions (3) -> target object -> containers (switch, random, music...) -> Sound (2) / MusicTrack (11)
//!   -> media id. Event ids are FNV-1 hashes of event names, so the caller can name every reachable media after
//!   its event, plus the switch/state values met on the way (also FNV-1 hashes of their names).
//! Only the stable parts of each object are decoded (ids, the source of a Sound/MusicTrack); a container's
//! children are found as "count + that many known object ids", which is how every container stores them.

use std::collections::{HashMap, HashSet};

pub struct Bank {
    pub bank_id: u32,
    pub objects: Vec<(u8, Vec<u8>)>,
    pub media: Vec<u32>,
}

fn u32le(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

pub fn parse_bank(d: &[u8]) -> Option<Bank> {
    let mut o = d.windows(4).position(|w| w == b"BKHD")?;
    let mut bank = Bank { bank_id: 0, objects: Vec::new(), media: Vec::new() };
    while o + 8 <= d.len() {
        let id = &d[o..o + 4];
        let sz = u32le(d, o + 4)? as usize;
        let body = d.get(o + 8..(o + 8 + sz).min(d.len()))?;
        match id {
            b"BKHD" => bank.bank_id = u32le(body, 4).unwrap_or(0),
            b"DIDX" => {
                for k in 0..body.len() / 12 {
                    bank.media.push(u32le(body, 12 * k)?);
                }
            }
            b"HIRC" => {
                let n = u32le(body, 0)? as usize;
                let mut q = 4usize;
                for _ in 0..n {
                    let t = *body.get(q)?;
                    let l = u32le(body, q + 1)? as usize;
                    let ob = body.get(q + 5..q + 5 + l)?;
                    bank.objects.push((t, ob.to_vec()));
                    q += 5 + l;
                }
            }
            _ => {}
        }
        o += 8 + sz;
    }
    Some(bank)
}

const CONTAINERS: [u8; 7] = [5, 6, 7, 9, 10, 12, 13];
const PLAYABLE: [u8; 9] = [2, 5, 6, 7, 9, 10, 11, 12, 13];

pub struct Links {
    /// media id -> [(event id, switch/state ids met from the event down to the media)]
    pub media: HashMap<u32, Vec<(u32, Vec<u32>)>>,
    /// media referenced by a Sound/MusicTrack object at all (reachable or not)
    pub sourced: HashSet<u32>,
    pub events: usize,
}

pub fn link(banks: &[Bank], known: &HashSet<u32>) -> Links {
    let mut objs: HashMap<u32, (u8, &[u8])> = HashMap::new();
    for b in banks {
        for (t, body) in &b.objects {
            if let Some(id) = u32le(body, 0) {
                objs.entry(id).or_insert((*t, body.as_slice()));
            }
        }
    }
    let playable: HashSet<u32> = objs.iter().filter(|(_, (t, _))| PLAYABLE.contains(t)).map(|(k, _)| *k).collect();

    // children of containers, and the switch/state value that selects each child
    let mut children: HashMap<u32, Vec<(u32, Vec<u32>)>> = HashMap::new();
    for (&id, &(t, b)) in &objs {
        if !CONTAINERS.contains(&t) || b.len() < 12 {
            continue;
        }
        let mut best: Option<(usize, usize)> = None;
        let mut off = 4;
        while off + 8 <= b.len() {
            let n = u32le(b, off).unwrap() as usize;
            if (1..=4000).contains(&n) && off + 4 + 4 * n <= b.len() && best.map(|(_, bn)| n > bn).unwrap_or(true) {
                if (0..n).all(|k| {
                    let x = u32le(b, off + 4 + 4 * k).unwrap();
                    x != id && playable.contains(&x)
                }) {
                    best = Some((off, n));
                }
            }
            off += 1;
        }
        let mut out: Vec<(u32, Vec<u32>)> = Vec::new();
        let mut tree_start = 40usize;
        if let Some((start, n)) = best {
            for k in 0..n {
                let c = u32le(b, start + 4 + 4 * k).unwrap();
                out.push((c, switch_key(b, c, start, n, known).into_iter().collect()));
            }
            tree_start = start + 4 + 4 * n;
        }
        // music switches: the decision tree (key, audio node, weight, probability) can point at nodes that are not
        // in the child list (other banks' segments and switches)
        if t == 12 {
            decision_tree(b, id, tree_start, &playable, known, &mut out);
        }
        if !out.is_empty() {
            children.insert(id, out);
        }
    }
    // dialogue events (15): a decision tree from argument values to audio nodes, the root of their own walk
    let mut dialogue_roots: Vec<(u32, Vec<(u32, Vec<u32>)>)> = Vec::new();
    for (&id, &(t, b)) in &objs {
        if t == 15 {
            let mut out = Vec::new();
            decision_tree(b, id, 8, &playable, known, &mut out);
            dialogue_roots.push((id, out));
        }
    }

    let mut sources: HashMap<u32, Vec<u32>> = HashMap::new();
    let mut sourced = HashSet::new();
    for (&id, &(t, b)) in &objs {
        match t {
            2 => {
                if let Some(m) = u32le(b, 9) {
                    sources.entry(id).or_default().push(m);
                    sourced.insert(m);
                }
            }
            11 => {
                let n = u32le(b, 5).unwrap_or(0) as usize;
                for k in 0..n.min(512) {
                    if let Some(m) = u32le(b, 9 + 14 * k + 5) {
                        sources.entry(id).or_default().push(m);
                        sourced.insert(m);
                    }
                }
            }
            _ => {}
        }
    }

    let mut media: HashMap<u32, Vec<(u32, Vec<u32>)>> = HashMap::new();
    let mut events = 0;
    for (&eid, &(t, b)) in &objs {
        if t != 4 || b.len() < 5 {
            continue;
        }
        events += 1;
        let n = b[4] as usize;
        for k in 0..n {
            let Some(aid) = u32le(b, 5 + 4 * k) else { break };
            let Some(&(at, ab)) = objs.get(&aid) else { continue };
            // only Play / PlayAndContinue actions say what an event plays (Stop, Seek, SetState... target the same nodes)
            let atype = u16::from_le_bytes([*ab.get(4).unwrap_or(&0), *ab.get(5).unwrap_or(&0)]);
            if at != 3 || !matches!(atype & 0xFF00, 0x0400 | 0x0500) {
                continue;
            }
            let Some(target) = u32le(ab, 6) else { continue };
            walk(eid, target, Vec::new(), &children, &sources, &mut media);
        }
    }
    for (eid, nodes) in &dialogue_roots {
        events += 1;
        for (node, keys) in nodes {
            walk(*eid, *node, keys.clone(), &children, &sources, &mut media);
        }
    }
    Links { media, sourced, events }
}

fn walk(
    eid: u32,
    target: u32,
    path0: Vec<u32>,
    children: &HashMap<u32, Vec<(u32, Vec<u32>)>>,
    sources: &HashMap<u32, Vec<u32>>,
    media: &mut HashMap<u32, Vec<(u32, Vec<u32>)>>,
) {
    let mut stack = vec![(target, path0)];
    let mut seen = HashSet::new();
    while let Some((node, path)) = stack.pop() {
        if !seen.insert(node) {
            continue;
        }
        if let Some(ms) = sources.get(&node) {
            for m in ms {
                let e = media.entry(*m).or_default();
                if e.len() < 8 && !e.iter().any(|(x, p)| *x == eid && *p == path) {
                    e.push((eid, path.clone()));
                }
            }
        }
        if let Some(cs) = children.get(&node) {
            for (c, keys) in cs {
                let mut p = path.clone();
                for k in keys {
                    if p.last() != Some(k) {
                        p.push(*k);
                    }
                }
                stack.push((*c, p));
            }
        }
    }
}

/// Leaves of the decision tree stored in a Music Switch or a Dialogue Event: `uTreeDataSize u32, uMode u8`, then
/// 12-byte nodes `key u32, (audio node id u32 | first child u16 + child count u16), weight u16, probability u16`,
/// node 0 being the root. Each leaf comes with the known switch/state values of the keys on its path (key 0 is
/// the "any" branch). The tree is located by trying every offset and keeping the first fully consistent one.
fn decision_tree(b: &[u8], _own: u32, start: usize, playable: &HashSet<u32>, known: &HashSet<u32>, out: &mut Vec<(u32, Vec<u32>)>) {
    let mut best: Option<Vec<(u32, Vec<u32>)>> = None;
    let mut o = start.max(4);
    while o + 5 + 12 <= b.len() {
        let tsize = u32le(b, o).unwrap() as usize;
        if tsize >= 12 && tsize % 12 == 0 && tsize <= b.len() && o + 5 + tsize <= b.len() && b[o + 4] <= 1 {
            if let Some(leaves) = tree_leaves(&b[o + 5..o + 5 + tsize], playable, known) {
                if best.as_ref().map(|x| leaves.len() > x.len()).unwrap_or(true) {
                    best = Some(leaves);
                }
            }
        }
        o += 1;
    }
    for (node, keys) in best.unwrap_or_default() {
        // the tree says which values select the node: it wins over a key-less child-list entry
        if let Some(e) = out.iter_mut().find(|(c, _)| *c == node) {
            if e.1.is_empty() {
                e.1 = keys;
            }
        } else {
            out.push((node, keys));
        }
    }
}

fn tree_leaves(t: &[u8], playable: &HashSet<u32>, known: &HashSet<u32>) -> Option<Vec<(u32, Vec<u32>)>> {
    let n = t.len() / 12;
    let node = |i: usize| (u32le(t, 12 * i).unwrap(), u32le(t, 12 * i + 4).unwrap());
    let mut leaves = Vec::new();
    let mut stack = vec![(0usize, Vec::<u32>::new(), 0usize)];
    let mut visited = 0;
    while let Some((i, keys, depth)) = stack.pop() {
        visited += 1;
        if visited > 4 * n + 4 || depth > 16 {
            return None;
        }
        let (key, f) = node(i);
        let mut keys = keys;
        if i != 0 && key != 0 && known.contains(&key) {
            keys.push(key);
        }
        if playable.contains(&f) {
            leaves.push((f, keys));
            continue;
        }
        let (first, count) = ((f & 0xFFFF) as usize, (f >> 16) as usize);
        if count == 0 || first <= i || first + count > n {
            if f == 0 && i != 0 {
                continue; // empty branch
            }
            return None;
        }
        for c in first..first + count {
            stack.push((c, keys.clone(), depth + 1));
        }
    }
    if leaves.is_empty() {
        None
    } else {
        Some(leaves)
    }
}

/// The known switch/state id that selects `child` in a switch container: it stores `switch id, count,
/// children...` groups after its child list.
fn switch_key(b: &[u8], child: u32, list_start: usize, list_n: usize, known: &HashSet<u32>) -> Option<u32> {
    let needle = child.to_le_bytes();
    let list_end = list_start + 4 + 4 * list_n;
    let mut o = list_end;
    while o + 4 <= b.len() {
        if b[o..o + 4] == needle {
            for j in 0..64usize {
                let p = match o.checked_sub(4 * j) {
                    Some(p) if p >= list_end + 8 => p,
                    _ => break,
                };
                let n = u32le(b, p - 4).unwrap() as usize;
                if n > j && n < 4096 {
                    let k = u32le(b, p - 8).unwrap();
                    if known.contains(&k) {
                        return Some(k);
                    }
                }
            }
        }
        o += 1;
    }
    None
}
