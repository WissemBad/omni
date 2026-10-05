//! 007 First Light collision (ALOC, "<prim>].coll"): PhysX cooked shapes.
//!
//! Verified layout: u32 type (bit mask: 1 convex, 2 triangle mesh, 4 primitives, 0x10 bone-attached,
//! 0x80 linked prim), u32 layer, "ID" + u32 5 + "PhysX", then sections:
//!   "CVX\0" u32 count, per shape {u32 material, pos 3f, quat 4f, "NXS\1CVXM" blob}
//!       CVXM blob: u32 version (13), u32 flags, "ICE\1CLHL", u32 version, u32 nbVerts, u32 nbEdges,
//!       u32 nbPolys, u32 nbVertexData, nbVerts x 3f ...
//!   "TRI\0" u32 count, per mesh {u32, u32, "NXS\1MESH" blob}
//!       MESH blob: u32 version (15), u32 midphase, u32 flags (4 = u8 indices, 8 = u16), u32 nbVerts,
//!       u32 nbTris, nbVerts x 3f, nbTris x 3 indices ...
//!   "ICP\0" u32 count, entries "BOX\0" half 3f | "SPH\0" radius | "CAP\0" radius, half height (along X),
//!       then u32 material, u32, pos 3f, quat 4f.
//! Positions are in metres in the prim's frame. Blobs are located by their "NXS\1" magic, so sections
//! whose exact length is not decoded (edges, mass data, midphase trees) never desynchronise the reader.

pub type V3 = [f32; 3];
pub type Q4 = [f32; 4];

#[derive(Debug, Clone)]
pub enum Shape {
    Convex { points: Vec<V3>, pos: V3, quat: Q4, material: u32 },
    Mesh { verts: Vec<V3>, tris: Vec<[u32; 3]> },
    Box { half: V3, pos: V3, quat: Q4, material: u32 },
    Sphere { radius: f32, pos: V3, quat: Q4, material: u32 },
    Capsule { radius: f32, half_height: f32, pos: V3, quat: Q4, material: u32 },
}

#[derive(Debug, Clone, Default)]
pub struct Collision {
    pub kind: u32,
    pub layer: u32,
    pub shapes: Vec<Shape>,
    pub warnings: Vec<String>,
}

struct R<'a>(&'a [u8]);

impl<'a> R<'a> {
    fn u32(&self, o: usize) -> Option<u32> {
        self.0.get(o..o + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }
    fn u16(&self, o: usize) -> Option<u16> {
        self.0.get(o..o + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
    }
    fn f32(&self, o: usize) -> Option<f32> {
        self.u32(o).map(f32::from_bits)
    }
    fn v3(&self, o: usize) -> Option<V3> {
        Some([self.f32(o)?, self.f32(o + 4)?, self.f32(o + 8)?])
    }
    fn q4(&self, o: usize) -> Option<Q4> {
        Some([self.f32(o)?, self.f32(o + 4)?, self.f32(o + 8)?, self.f32(o + 12)?])
    }
    fn tag(&self, o: usize, t: &[u8]) -> bool {
        self.0.get(o..o + t.len()) == Some(t)
    }
}

fn finite(v: &[f32]) -> bool {
    v.iter().all(|x| x.is_finite() && x.abs() < 1.0e5)
}

fn sane_quat(q: Q4) -> Q4 {
    let n = (q.iter().map(|x| x * x).sum::<f32>()).sqrt();
    if finite(&q) && (n - 1.0).abs() < 0.05 { q } else { [0.0, 0.0, 0.0, 1.0] }
}

fn find_all(d: &[u8], pat: &[u8]) -> Vec<usize> {
    let mut out = Vec::new();
    if d.len() < pat.len() {
        return out;
    }
    let mut i = 0;
    while i + pat.len() <= d.len() {
        if &d[i..i + pat.len()] == pat {
            out.push(i);
            i += pat.len();
        } else {
            i += 1;
        }
    }
    out
}

const MAX_VERTS: u32 = 1 << 20;

fn convex(r: &R, nxs: usize) -> Option<Vec<V3>> {
    // "NXS\1CVXM" version flags "ICE\1CLHL" version nbVerts nbEdges nbPolys nbVData verts...
    if !r.tag(nxs + 16, b"ICE\x01CLHL") {
        return None;
    }
    let nv = r.u32(nxs + 28)?;
    if nv == 0 || nv > 255 * 4 {
        return None;
    }
    let base = nxs + 44;
    let mut pts = Vec::with_capacity(nv as usize);
    for i in 0..nv as usize {
        let p = r.v3(base + 12 * i)?;
        if !finite(&p) {
            return None;
        }
        pts.push(p);
    }
    Some(pts)
}

fn mesh(r: &R, nxs: usize) -> Option<(Vec<V3>, Vec<[u32; 3]>)> {
    let flags = r.u32(nxs + 16)?;
    let nv = r.u32(nxs + 20)?;
    let nt = r.u32(nxs + 24)?;
    if nv == 0 || nt == 0 || nv > MAX_VERTS || nt > MAX_VERTS * 2 {
        return None;
    }
    let base = nxs + 28;
    let mut verts = Vec::with_capacity(nv as usize);
    for i in 0..nv as usize {
        let p = r.v3(base + 12 * i)?;
        if !finite(&p) {
            return None;
        }
        verts.push(p);
    }
    let ib = base + 12 * nv as usize;
    let mut tris = Vec::with_capacity(nt as usize);
    for t in 0..nt as usize {
        let mut tri = [0u32; 3];
        for (k, v) in tri.iter_mut().enumerate() {
            let j = 3 * t + k;
            *v = if flags & 4 != 0 {
                *r.0.get(ib + j)? as u32
            } else if flags & 8 != 0 {
                r.u16(ib + 2 * j)? as u32
            } else {
                r.u32(ib + 4 * j)?
            };
            if *v >= nv {
                return None;
            }
        }
        tris.push(tri);
    }
    Some((verts, tris))
}

fn primitives(r: &R, at: usize, out: &mut Vec<Shape>) -> bool {
    let count = match r.u32(at + 4) {
        Some(c) if (1..=4096).contains(&c) => c,
        _ => return false,
    };
    let mut o = at + 8;
    let mut parsed = Vec::new();
    for _ in 0..count {
        let (shape, size) = if r.tag(o, b"BOX\0") {
            let (half, mat, pos, q) = (r.v3(o + 4), r.u32(o + 16), r.v3(o + 24), r.q4(o + 36));
            match (half, mat, pos, q) {
                (Some(half), Some(m), Some(pos), Some(q)) if finite(&half) && finite(&pos) => {
                    (Shape::Box { half, pos, quat: sane_quat(q), material: m }, 52)
                }
                _ => return false,
            }
        } else if r.tag(o, b"SPH\0") {
            match (r.f32(o + 4), r.u32(o + 8), r.v3(o + 16), r.q4(o + 28)) {
                (Some(rad), Some(m), Some(pos), Some(q)) if finite(&[rad]) && finite(&pos) => {
                    (Shape::Sphere { radius: rad, pos, quat: sane_quat(q), material: m }, 44)
                }
                _ => return false,
            }
        } else if r.tag(o, b"CAP\0") {
            match (r.f32(o + 4), r.f32(o + 8), r.u32(o + 12), r.v3(o + 20), r.q4(o + 32)) {
                (Some(rad), Some(hh), Some(m), Some(pos), Some(q)) if finite(&[rad, hh]) && finite(&pos) => {
                    (Shape::Capsule { radius: rad, half_height: hh, pos, quat: sane_quat(q), material: m }, 48)
                }
                _ => return false,
            }
        } else {
            return false;
        };
        parsed.push(shape);
        o += size;
    }
    out.extend(parsed);
    true
}

pub fn parse(d: &[u8]) -> Result<Collision, String> {
    let r = R(d);
    let kind = r.u32(0).ok_or("truncated ALOC")?;
    let layer = r.u32(4).ok_or("truncated ALOC")?;
    if !r.tag(8, b"ID") || !r.tag(14, b"PhysX") {
        return Err("not a PhysX collision resource".into());
    }
    let mut c = Collision { kind, layer, ..Default::default() };
    for nxs in find_all(d, b"NXS\x01") {
        if r.tag(nxs + 4, b"CVXM") {
            let pos = r.v3(nxs.wrapping_sub(28)).filter(|p| finite(p)).unwrap_or([0.0; 3]);
            let quat = sane_quat(r.q4(nxs.wrapping_sub(16)).unwrap_or([0.0, 0.0, 0.0, 1.0]));
            let material = r.u32(nxs.wrapping_sub(32)).unwrap_or(0);
            match convex(&r, nxs) {
                Some(points) => c.shapes.push(Shape::Convex { points, pos, quat, material }),
                None => c.warnings.push(format!("convex at {nxs:#x} not decoded")),
            }
        } else if r.tag(nxs + 4, b"MESH") {
            match mesh(&r, nxs) {
                Some((verts, tris)) => c.shapes.push(Shape::Mesh { verts, tris }),
                None => c.warnings.push(format!("mesh at {nxs:#x} not decoded")),
            }
        }
    }
    for at in find_all(d, b"ICP\0") {
        if primitives(&r, at, &mut c.shapes) {
            break;
        }
    }
    Ok(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_garbage() {
        assert!(parse(b"hello").is_err());
        assert!(parse(&[0u8; 64]).is_err());
    }

    #[test]
    fn primitive_box() {
        let mut d = vec![4u8, 0, 0, 0, 1, 0, 0, 0];
        d.extend_from_slice(b"ID\x05\x00\x00\x00PhysX");
        d.extend_from_slice(b"ICP\0");
        d.extend_from_slice(&1u32.to_le_bytes());
        d.extend_from_slice(b"BOX\0");
        for v in [0.5f32, 0.25, 1.0] {
            d.extend_from_slice(&v.to_le_bytes());
        }
        d.extend_from_slice(&[2, 0, 0, 0, 0, 0, 0, 0]);
        for v in [0.0f32, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0] {
            d.extend_from_slice(&v.to_le_bytes());
        }
        let c = parse(&d).unwrap();
        assert_eq!(c.shapes.len(), 1);
        match &c.shapes[0] {
            Shape::Box { half, pos, .. } => {
                assert_eq!(*half, [0.5, 0.25, 1.0]);
                assert_eq!(*pos, [0.0, 0.0, 1.0]);
            }
            _ => panic!("expected a box"),
        }
    }
}

fn jv(v: &[f32]) -> String {
    let parts: Vec<String> = v.iter().map(|x| if x.is_finite() { format!("{x}") } else { "0".into() }).collect();
    format!("[{}]", parts.join(","))
}

/// The parsed collision as JSON (same layout as the CPython binding's dict), for the WebAssembly build.
pub fn to_json(c: &Collision) -> String {
    let shapes: Vec<String> = c
        .shapes
        .iter()
        .map(|s| match s {
            Shape::Convex { points, pos, quat, material } => format!(
                "{{\"type\":\"convex\",\"points\":[{}],\"pos\":{},\"quat\":{},\"material\":{material}}}",
                points.iter().map(|p| jv(p)).collect::<Vec<_>>().join(","),
                jv(pos),
                jv(quat)
            ),
            Shape::Mesh { verts, tris } => format!(
                "{{\"type\":\"mesh\",\"verts\":[{}],\"tris\":[{}]}}",
                verts.iter().map(|p| jv(p)).collect::<Vec<_>>().join(","),
                tris.iter().map(|t| format!("[{},{},{}]", t[0], t[1], t[2])).collect::<Vec<_>>().join(",")
            ),
            Shape::Box { half, pos, quat, material } => {
                format!("{{\"type\":\"box\",\"half\":{},\"pos\":{},\"quat\":{},\"material\":{material}}}", jv(half), jv(pos), jv(quat))
            }
            Shape::Sphere { radius, pos, quat, material } => {
                format!("{{\"type\":\"sphere\",\"radius\":{},\"pos\":{},\"quat\":{},\"material\":{material}}}", jv(&[*radius])[1..].trim_end_matches(']'), jv(pos), jv(quat))
            }
            Shape::Capsule { radius, half_height, pos, quat, material } => format!(
                "{{\"type\":\"capsule\",\"radius\":{},\"half_height\":{},\"pos\":{},\"quat\":{},\"material\":{material}}}",
                jv(&[*radius])[1..].trim_end_matches(']'),
                jv(&[*half_height])[1..].trim_end_matches(']'),
                jv(pos),
                jv(quat)
            ),
        })
        .collect();
    let warns: Vec<String> = c.warnings.iter().map(|w| format!("\"{}\"", w.replace('\\', "\\\\").replace('"', "\\\""))).collect();
    format!("{{\"kind\":{},\"layer\":{},\"shapes\":[{}],\"warnings\":[{}]}}", c.kind, c.layer, shapes.join(","), warns.join(","))
}
