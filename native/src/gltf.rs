//! glTF 2.0 binary (.glb) writer: geometry, PBR materials with embedded PNG/JPEG textures, skeleton and skin.
//!
//! One builder serves every model export (the glTF target, the 3D previews, the optional Blender step). Inputs are
//! checked before anything is written (array lengths, index range), so a malformed mesh is an error, not a file
//! that Blender refuses to open. Source frames: omni is Z up and faces +Y; see [`Frame`].

use crate::vtf::encode_png;

/// Axis conversion from the omni frame (Z up, facing +Y) to glTF (Y up).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Frame {
    /// (x, y, z) -> (-x, z, y): the model faces +Z, as glTF viewers expect (export for Blender and others).
    FacingZ,
    /// (x, y, z) -> (x, z, -y): the frame the web viewer was built on.
    Viewer,
}

impl Frame {
    pub fn parse(s: &str) -> Option<Frame> {
        match s {
            "facing_z" => Some(Frame::FacingZ),
            "viewer" => Some(Frame::Viewer),
            _ => None,
        }
    }

    fn matrix(self) -> [[f64; 3]; 3] {
        match self {
            Frame::FacingZ => [[-1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]],
            Frame::Viewer => [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]],
        }
    }

    fn vec(self, v: [f32; 3]) -> [f32; 3] {
        let m = self.matrix();
        let v = [v[0] as f64, v[1] as f64, v[2] as f64];
        let r = |i: usize| (m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2]) as f32;
        [r(0), r(1), r(2)]
    }
}

// --- minimal JSON ---------------------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum J {
    Bool(bool),
    Int(i64),
    Num(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(&'static str, J)>),
}

fn esc(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

impl J {
    fn write(&self, out: &mut String) {
        match self {
            J::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            J::Int(i) => out.push_str(&i.to_string()),
            J::Num(f) => {
                if f.is_finite() {
                    out.push_str(&format!("{f}"));
                } else {
                    out.push('0');
                }
            }
            J::Str(s) => esc(s, out),
            J::Arr(a) => {
                out.push('[');
                for (i, v) in a.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    v.write(out);
                }
                out.push(']');
            }
            J::Obj(o) => {
                out.push('{');
                for (i, (k, v)) in o.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    esc(k, out);
                    out.push(':');
                    v.write(out);
                }
                out.push('}');
            }
        }
    }

    pub fn to_string_compact(&self) -> String {
        let mut s = String::new();
        self.write(&mut s);
        s
    }
}

fn int(i: usize) -> J {
    J::Int(i as i64)
}

fn floats(v: &[f64]) -> J {
    J::Arr(v.iter().map(|&x| J::Num(x)).collect())
}

// --- images -----------------------------------------------------------------------------------------------------------

/// Reduce an RGBA8 image to fit `max` pixels on its longest side (2x2 box filter while both sides are even, then
/// nearest). `max == 0` keeps the size.
pub fn fit_rgba(px: &[u8], w: usize, h: usize, max: usize) -> (Vec<u8>, usize, usize) {
    let (mut px, mut w, mut h) = (px.to_vec(), w, h);
    while max > 0 && w.max(h) > max && w % 2 == 0 && h % 2 == 0 && w > 1 && h > 1 {
        let (nw, nh) = (w / 2, h / 2);
        let mut out = vec![0u8; nw * nh * 4];
        for y in 0..nh {
            for x in 0..nw {
                for c in 0..4 {
                    let at = |dx: usize, dy: usize| px[((2 * y + dy) * w + 2 * x + dx) * 4 + c] as u32;
                    out[(y * nw + x) * 4 + c] = ((at(0, 0) + at(1, 0) + at(0, 1) + at(1, 1) + 2) / 4) as u8;
                }
            }
        }
        px = out;
        w = nw;
        h = nh;
    }
    if max > 0 && w.max(h) > max {
        let s = max as f64 / w.max(h) as f64;
        let (nw, nh) = (((w as f64 * s) as usize).max(1), ((h as f64 * s) as usize).max(1));
        let mut out = vec![0u8; nw * nh * 4];
        for y in 0..nh {
            let sy = (y * h / nh).min(h - 1);
            for x in 0..nw {
                let sx = (x * w / nw).min(w - 1);
                out[(y * nw + x) * 4..(y * nw + x) * 4 + 4].copy_from_slice(&px[(sy * w + sx) * 4..(sy * w + sx) * 4 + 4]);
            }
        }
        return (out, nw, nh);
    }
    (px, w, h)
}

/// Encoded texture: PNG (lossless, keeps alpha) or JPEG (opaque colour maps, `quality` 1..=100).
pub fn encode_image(px: &[u8], w: usize, h: usize, max: usize, jpeg_quality: u8) -> Result<(Vec<u8>, &'static str), String> {
    if w == 0 || h == 0 || px.len() != w * h * 4 {
        return Err(format!("image: {} bytes for {w}x{h} RGBA", px.len()));
    }
    if w > 65535 || h > 65535 {
        return Err("image too large".into());
    }
    let (px, w, h) = fit_rgba(px, w, h, max);
    if jpeg_quality > 0 {
        let rgb: Vec<u8> = px.chunks_exact(4).flat_map(|p| [p[0], p[1], p[2]]).collect();
        let mut out = Vec::new();
        jpeg_encoder::Encoder::new(&mut out, jpeg_quality.min(100))
            .encode(&rgb, w as u16, h as u16, jpeg_encoder::ColorType::Rgb)
            .map_err(|e| e.to_string())?;
        Ok((out, "image/jpeg"))
    } else {
        Ok((encode_png(&px, w, h), "image/png"))
    }
}

// --- builder ----------------------------------------------------------------------------------------------------------

pub struct Prim<'a> {
    pub positions: &'a [f32],
    pub normals: &'a [f32],
    pub uvs: &'a [f32],
    pub indices: &'a [u32],
    pub tangents: Option<&'a [f32]>,
    pub joints: Option<&'a [u16]>,
    pub weights: Option<&'a [f32]>,
    pub material: Option<usize>,
}

pub struct MaterialSpec {
    pub name: String,
    pub base: Option<usize>,
    pub normal: Option<usize>,
    pub metal_rough: Option<usize>,
    pub occlusion: Option<usize>,
    pub emissive: Option<usize>,
    pub alpha: String,
    pub cutoff: f64,
    pub double_sided: bool,
    pub metallic: f64,
    pub roughness: f64,
}

struct NodeRec {
    name: String,
    mesh: Option<usize>,
    skin: Option<usize>,
    matrix: Option<[f64; 16]>,
    children: Vec<usize>,
}

pub struct Glb {
    frame: Frame,
    blob: Vec<u8>,
    views: Vec<J>,
    accessors: Vec<J>,
    images: Vec<J>,
    textures: Vec<J>,
    materials: Vec<J>,
    meshes: Vec<J>,
    nodes: Vec<NodeRec>,
    skins: Vec<J>,
    joint_count: usize,
}

const ARRAY_BUFFER: i64 = 34962;
const ELEMENT_ARRAY_BUFFER: i64 = 34963;
const FLOAT: i64 = 5126;
const UNSIGNED_SHORT: i64 = 5123;
const UNSIGNED_INT: i64 = 5125;

/// glTF stores 32-bit sizes: refuse to build what cannot be written instead of wrapping around.
const MAX_BLOB: usize = u32::MAX as usize - 1_048_576;

impl Glb {
    pub fn new(frame: Frame) -> Glb {
        Glb {
            frame,
            blob: Vec::new(),
            views: Vec::new(),
            accessors: Vec::new(),
            images: Vec::new(),
            textures: Vec::new(),
            materials: Vec::new(),
            meshes: Vec::new(),
            nodes: Vec::new(),
            skins: Vec::new(),
            joint_count: 0,
        }
    }

    fn view(&mut self, data: &[u8], target: Option<i64>) -> Result<usize, String> {
        while self.blob.len() % 4 != 0 {
            self.blob.push(0);
        }
        if self.blob.len() + data.len() > MAX_BLOB {
            return Err("model too large for a .glb (4 GB)".into());
        }
        let mut o = vec![("buffer", int(0)), ("byteOffset", int(self.blob.len())), ("byteLength", int(data.len()))];
        if let Some(t) = target {
            o.push(("target", J::Int(t)));
        }
        self.views.push(J::Obj(o));
        self.blob.extend_from_slice(data);
        Ok(self.views.len() - 1)
    }

    fn accessor(&mut self, data: &[u8], target: Option<i64>, ctype: i64, count: usize, kind: &'static str, minmax: Option<(Vec<f64>, Vec<f64>)>) -> Result<usize, String> {
        let v = self.view(data, target)?;
        let mut o = vec![("bufferView", int(v)), ("componentType", J::Int(ctype)), ("count", int(count)), ("type", J::Str(kind.into()))];
        if let Some((lo, hi)) = minmax {
            o.push(("min", floats(&lo)));
            o.push(("max", floats(&hi)));
        }
        self.accessors.push(J::Obj(o));
        Ok(self.accessors.len() - 1)
    }

    fn f32_accessor(&mut self, v: &[f32], comps: usize, kind: &'static str, target: Option<i64>, minmax: bool) -> Result<usize, String> {
        let count = v.len() / comps;
        let mm = if minmax && count > 0 {
            let mut lo = vec![f64::INFINITY; comps];
            let mut hi = vec![f64::NEG_INFINITY; comps];
            for p in v.chunks_exact(comps) {
                for c in 0..comps {
                    lo[c] = lo[c].min(p[c] as f64);
                    hi[c] = hi[c].max(p[c] as f64);
                }
            }
            Some((lo, hi))
        } else {
            None
        };
        let bytes: Vec<u8> = v.iter().flat_map(|f| f.to_le_bytes()).collect();
        self.accessor(&bytes, target, FLOAT, count, kind, mm)
    }

    /// Embed an already encoded image; returns the texture index.
    pub fn add_encoded(&mut self, data: &[u8], mime: &str) -> Result<usize, String> {
        let v = self.view(data, None)?;
        self.images.push(J::Obj(vec![("bufferView", int(v)), ("mimeType", J::Str(mime.into()))]));
        self.textures.push(J::Obj(vec![("sampler", int(0)), ("source", int(self.images.len() - 1))]));
        Ok(self.textures.len() - 1)
    }

    pub fn add_material(&mut self, m: &MaterialSpec) -> Result<usize, String> {
        let tex = |i: usize| -> Result<J, String> {
            if i >= self.textures.len() {
                return Err(format!("material {}: texture {i} does not exist", m.name));
            }
            Ok(J::Obj(vec![("index", int(i))]))
        };
        let mut pbr = Vec::new();
        if let Some(i) = m.base {
            pbr.push(("baseColorTexture", tex(i)?));
        }
        if let Some(i) = m.metal_rough {
            pbr.push(("metallicRoughnessTexture", tex(i)?));
        }
        pbr.push(("metallicFactor", J::Num(m.metallic)));
        pbr.push(("roughnessFactor", J::Num(m.roughness)));
        let mut o = vec![("name", J::Str(m.name.clone())), ("pbrMetallicRoughness", J::Obj(pbr))];
        if let Some(i) = m.normal {
            o.push(("normalTexture", tex(i)?));
        }
        if let Some(i) = m.occlusion {
            o.push(("occlusionTexture", tex(i)?));
        }
        if let Some(i) = m.emissive {
            o.push(("emissiveTexture", tex(i)?));
            o.push(("emissiveFactor", J::Arr(vec![J::Num(1.0), J::Num(1.0), J::Num(1.0)])));
        }
        match m.alpha.as_str() {
            "MASK" => {
                o.push(("alphaMode", J::Str("MASK".into())));
                o.push(("alphaCutoff", J::Num(m.cutoff)));
            }
            "BLEND" => o.push(("alphaMode", J::Str("BLEND".into()))),
            _ => {}
        }
        o.push(("doubleSided", J::Bool(m.double_sided)));
        self.materials.push(J::Obj(o));
        Ok(self.materials.len() - 1)
    }

    /// One mesh of several primitives. Positions and normals are converted to Y up; tangents are normalised with
    /// a +-1 handedness (Glacier stores a raw byte, >= 128 meaning positive); skin weights are normalised.
    pub fn add_mesh(&mut self, name: &str, prims: &[Prim]) -> Result<usize, String> {
        let mut out = Vec::new();
        for (pi, p) in prims.iter().enumerate() {
            let n = p.positions.len() / 3;
            let bad = |what: &str| format!("mesh {name}, primitive {pi}: {what}");
            if p.positions.len() % 3 != 0 || p.normals.len() != n * 3 || p.uvs.len() != n * 2 {
                return Err(bad("positions, normals and uvs disagree"));
            }
            if p.indices.len() < 3 || p.indices.len() % 3 != 0 {
                return Err(bad("indices are not a triangle list"));
            }
            if p.indices.iter().any(|&i| i as usize >= n) {
                return Err(bad("an index points past the vertices"));
            }
            if p.positions.iter().chain(p.normals).chain(p.uvs).any(|f| !f.is_finite()) {
                return Err(bad("non-finite vertex data"));
            }
            let conv = |a: &[f32]| -> Vec<f32> { a.chunks_exact(3).flat_map(|v| self.frame.vec([v[0], v[1], v[2]])).collect() };
            let (pos, nrm) = (conv(p.positions), conv(p.normals));
            let mut attrs = vec![
                ("POSITION", int(self.f32_accessor(&pos, 3, "VEC3", Some(ARRAY_BUFFER), true)?)),
                ("NORMAL", int(self.f32_accessor(&nrm, 3, "VEC3", Some(ARRAY_BUFFER), false)?)),
                ("TEXCOORD_0", int(self.f32_accessor(p.uvs, 2, "VEC2", Some(ARRAY_BUFFER), false)?)),
            ];
            if let Some(t) = p.tangents {
                if t.len() == n * 4 {
                    let mut tv = Vec::with_capacity(n * 4);
                    for c in t.chunks_exact(4) {
                        let x = self.frame.vec([c[0], c[1], c[2]]);
                        let l = ((x[0] as f64).powi(2) + (x[1] as f64).powi(2) + (x[2] as f64).powi(2)).sqrt();
                        let (x, y, z) = if l > 1e-6 { (x[0] as f64 / l, x[1] as f64 / l, x[2] as f64 / l) } else { (1.0, 0.0, 0.0) };
                        let h = c[3];
                        let sign = if h.abs() > 1.5 { if h >= 128.0 { 1.0 } else { -1.0 } } else if h < 0.0 { -1.0 } else { 1.0 };
                        tv.extend([x as f32, y as f32, z as f32, sign]);
                    }
                    attrs.push(("TANGENT", int(self.f32_accessor(&tv, 4, "VEC4", Some(ARRAY_BUFFER), false)?)));
                }
            }
            if let (Some(j), Some(w)) = (p.joints, p.weights) {
                if self.joint_count > 0 && j.len() == n * 4 && w.len() == n * 4 {
                    let top = (self.joint_count - 1) as u16;
                    let (mut jo, mut wo) = (Vec::with_capacity(n * 4), Vec::with_capacity(n * 4));
                    for (jc, wc) in j.chunks_exact(4).zip(w.chunks_exact(4)) {
                        let tot: f32 = wc.iter().sum();
                        for k in 0..4 {
                            let wk = if tot > 0.0 { wc[k] / tot.max(1e-6) } else if k == 0 { 1.0 } else { 0.0 };
                            wo.push(wk);
                            jo.push(if wk == 0.0 { 0 } else { jc[k].min(top) });
                        }
                    }
                    let jb: Vec<u8> = jo.iter().flat_map(|v| v.to_le_bytes()).collect();
                    attrs.push(("JOINTS_0", int(self.accessor(&jb, Some(ARRAY_BUFFER), UNSIGNED_SHORT, n, "VEC4", None)?)));
                    attrs.push(("WEIGHTS_0", int(self.f32_accessor(&wo, 4, "VEC4", Some(ARRAY_BUFFER), false)?)));
                }
            }
            let ib: Vec<u8> = p.indices.iter().flat_map(|v| v.to_le_bytes()).collect();
            let idx = self.accessor(&ib, Some(ELEMENT_ARRAY_BUFFER), UNSIGNED_INT, p.indices.len(), "SCALAR", None)?;
            let mut prim = vec![("attributes", J::Obj(attrs)), ("indices", int(idx))];
            if let Some(m) = p.material {
                if m >= self.materials.len() {
                    return Err(bad("unknown material"));
                }
                prim.push(("material", int(m)));
            }
            out.push(J::Obj(prim));
        }
        self.meshes.push(J::Obj(vec![("name", J::Str(name.into())), ("primitives", J::Arr(out))]));
        Ok(self.meshes.len() - 1)
    }

    /// Joint nodes, their hierarchy and the skin. `world` holds one row-major 4x4 matrix per joint (omni frame,
    /// bind pose); returns the skin index. Parents must come before their children or be -1.
    pub fn add_skeleton(&mut self, names: &[String], parents: &[i64], world: &[f64]) -> Result<usize, String> {
        let n = names.len();
        if n == 0 || parents.len() != n || world.len() != n * 16 {
            return Err("skeleton: names, parents and matrices disagree".into());
        }
        for (i, &p) in parents.iter().enumerate() {
            if p >= i as i64 || p < -1 {
                return Err(format!("skeleton: joint {i} has parent {p} (parents must come first)"));
            }
        }
        let c = self.frame.matrix();
        let mut cm = [[0.0f64; 4]; 4];
        for i in 0..3 {
            cm[i][..3].copy_from_slice(&c[i]);
        }
        cm[3][3] = 1.0;
        let cinv = transpose(&cm); // a rotation: inverse == transpose
        let wy: Vec<[[f64; 4]; 4]> = (0..n)
            .map(|i| {
                let mut w = [[0.0; 4]; 4];
                for r in 0..4 {
                    for k in 0..4 {
                        w[r][k] = world[i * 16 + r * 4 + k];
                    }
                }
                mul(&mul(&cm, &w), &cinv)
            })
            .collect();
        let base = self.nodes.len();
        let mut ibm = Vec::with_capacity(n * 16);
        for i in 0..n {
            let local = if parents[i] >= 0 { mul(&inverse(&wy[parents[i] as usize])?, &wy[i]) } else { wy[i] };
            let t = transpose(&local);
            let mut m = [0.0; 16];
            for r in 0..4 {
                for k in 0..4 {
                    m[r * 4 + k] = t[r][k];
                }
            }
            self.nodes.push(NodeRec { name: names[i].clone(), mesh: None, skin: None, matrix: Some(m), children: vec![] });
            if parents[i] >= 0 {
                self.nodes[base + parents[i] as usize].children.push(base + i);
            }
            let inv_t = transpose(&inverse(&wy[i])?);
            for r in 0..4 {
                for k in 0..4 {
                    ibm.push(inv_t[r][k] as f32);
                }
            }
        }
        let acc = self.f32_accessor(&ibm, 16, "MAT4", None, false)?;
        let root = (0..n).find(|&i| parents[i] < 0).map(|i| base + i);
        let mut o = vec![("joints", J::Arr((base..base + n).map(int).collect())), ("inverseBindMatrices", int(acc))];
        if let Some(r) = root {
            o.push(("skeleton", int(r)));
        }
        self.skins.push(J::Obj(o));
        self.joint_count = n;
        Ok(self.skins.len() - 1)
    }

    pub fn add_node(&mut self, name: &str, mesh: Option<usize>, skin: Option<usize>) -> Result<usize, String> {
        if mesh.is_some_and(|m| m >= self.meshes.len()) || skin.is_some_and(|s| s >= self.skins.len()) {
            return Err("node: unknown mesh or skin".into());
        }
        self.nodes.push(NodeRec { name: name.into(), mesh, skin, matrix: None, children: vec![] });
        Ok(self.nodes.len() - 1)
    }

    /// The finished file: header, JSON chunk, binary chunk. The scene lists every node that is nobody's child.
    pub fn finish(&self, generator: &str) -> Result<Vec<u8>, String> {
        let mut is_child = vec![false; self.nodes.len()];
        for n in &self.nodes {
            for &c in &n.children {
                is_child[c] = true;
            }
        }
        let nodes: Vec<J> = self
            .nodes
            .iter()
            .map(|n| {
                let mut o = vec![("name", J::Str(n.name.clone()))];
                if let Some(m) = n.mesh {
                    o.push(("mesh", int(m)));
                }
                if let Some(s) = n.skin {
                    o.push(("skin", int(s)));
                }
                if !n.children.is_empty() {
                    o.push(("children", J::Arr(n.children.iter().map(|&c| int(c)).collect())));
                }
                if let Some(m) = n.matrix {
                    o.push(("matrix", floats(&m)));
                }
                J::Obj(o)
            })
            .collect();
        let roots: Vec<J> = (0..self.nodes.len()).filter(|&i| !is_child[i]).map(int).collect();
        let mut root = vec![
            ("asset", J::Obj(vec![("version", J::Str("2.0".into())), ("generator", J::Str(generator.into()))])),
            ("scene", int(0)),
            ("scenes", J::Arr(vec![J::Obj(vec![("nodes", J::Arr(roots))])])),
            ("nodes", J::Arr(nodes)),
            ("meshes", J::Arr(self.meshes.clone())),
            ("materials", J::Arr(self.materials.clone())),
            ("accessors", J::Arr(self.accessors.clone())),
            ("bufferViews", J::Arr(self.views.clone())),
            ("buffers", J::Arr(vec![J::Obj(vec![("byteLength", int(self.blob.len()))])])),
            (
                "samplers",
                J::Arr(vec![J::Obj(vec![("magFilter", J::Int(9729)), ("minFilter", J::Int(9987)), ("wrapS", J::Int(10497)), ("wrapT", J::Int(10497))])]),
            ),
        ];
        if !self.images.is_empty() {
            root.push(("images", J::Arr(self.images.clone())));
            root.push(("textures", J::Arr(self.textures.clone())));
        }
        if !self.skins.is_empty() {
            root.push(("skins", J::Arr(self.skins.clone())));
        }
        let mut json = J::Obj(root).to_string_compact().into_bytes();
        while json.len() % 4 != 0 {
            json.push(b' ');
        }
        let mut bin = self.blob.clone();
        while bin.len() % 4 != 0 {
            bin.push(0);
        }
        let total = 12 + 8 + json.len() + 8 + bin.len();
        if total > u32::MAX as usize {
            return Err("model too large for a .glb (4 GB)".into());
        }
        let mut out = Vec::with_capacity(total);
        out.extend(0x4654_6C67u32.to_le_bytes());
        out.extend(2u32.to_le_bytes());
        out.extend((total as u32).to_le_bytes());
        out.extend((json.len() as u32).to_le_bytes());
        out.extend(0x4E4F_534Au32.to_le_bytes());
        out.extend(&json);
        out.extend((bin.len() as u32).to_le_bytes());
        out.extend(0x004E_4942u32.to_le_bytes());
        out.extend(&bin);
        Ok(out)
    }
}

// --- 4x4 matrices (row-major) -----------------------------------------------------------------------------------------

type M4 = [[f64; 4]; 4];

fn mul(a: &M4, b: &M4) -> M4 {
    let mut r = [[0.0; 4]; 4];
    for i in 0..4 {
        for j in 0..4 {
            r[i][j] = (0..4).map(|k| a[i][k] * b[k][j]).sum();
        }
    }
    r
}

fn transpose(a: &M4) -> M4 {
    let mut r = [[0.0; 4]; 4];
    for i in 0..4 {
        for j in 0..4 {
            r[i][j] = a[j][i];
        }
    }
    r
}

/// Gauss-Jordan with partial pivoting; a singular matrix is an error (a bone scaled to zero cannot be skinned).
fn inverse(m: &M4) -> Result<M4, String> {
    let mut a = *m;
    let mut inv = [[0.0; 4]; 4];
    for (i, row) in inv.iter_mut().enumerate() {
        row[i] = 1.0;
    }
    for c in 0..4 {
        let p = (c..4).max_by(|&x, &y| a[x][c].abs().total_cmp(&a[y][c].abs())).unwrap_or(c);
        if a[p][c].abs() < 1e-12 {
            return Err("skeleton: singular bone matrix".into());
        }
        a.swap(c, p);
        inv.swap(c, p);
        let d = a[c][c];
        for k in 0..4 {
            a[c][k] /= d;
            inv[c][k] /= d;
        }
        for r in 0..4 {
            if r != c {
                let f = a[r][c];
                if f != 0.0 {
                    for k in 0..4 {
                        a[r][k] -= f * a[c][k];
                        inv[r][k] -= f * inv[c][k];
                    }
                }
            }
        }
    }
    Ok(inv)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn quad() -> (Vec<f32>, Vec<f32>, Vec<f32>, Vec<u32>) {
        (vec![0., 0., 0., 1., 0., 0., 1., 0., 1., 0., 0., 1.], [0., -1., 0.].repeat(4), vec![0.; 8], vec![0, 1, 2, 0, 2, 3])
    }

    fn chunks(g: &[u8]) -> (String, Vec<u8>) {
        assert_eq!(&g[0..4], b"glTF");
        assert_eq!(u32::from_le_bytes(g[8..12].try_into().unwrap()) as usize, g.len());
        let jl = u32::from_le_bytes(g[12..16].try_into().unwrap()) as usize;
        assert_eq!(&g[16..20], b"JSON");
        let json = String::from_utf8(g[20..20 + jl].to_vec()).unwrap();
        let b = 20 + jl;
        let bl = u32::from_le_bytes(g[b..b + 4].try_into().unwrap()) as usize;
        assert_eq!(&g[b + 4..b + 8], b"BIN\0");
        assert_eq!(b + 8 + bl, g.len());
        assert_eq!(jl % 4, 0);
        assert_eq!(bl % 4, 0);
        (json, g[b + 8..].to_vec())
    }

    #[test]
    fn static_mesh_with_textured_material() {
        let (p, n, u, i) = quad();
        let mut g = Glb::new(Frame::FacingZ);
        let (png, mime) = encode_image(&[200, 100, 50, 255].repeat(16), 4, 4, 0, 0).unwrap();
        assert_eq!(mime, "image/png");
        let t = g.add_encoded(&png, mime).unwrap();
        let m = g
            .add_material(&MaterialSpec { name: "mat".into(), base: Some(t), normal: None, metal_rough: None, occlusion: None, emissive: None, alpha: "MASK".into(), cutoff: 0.5, double_sided: true, metallic: 0.0, roughness: 0.7 })
            .unwrap();
        let me = g.add_mesh("quad", &[Prim { positions: &p, normals: &n, uvs: &u, indices: &i, tangents: None, joints: None, weights: None, material: Some(m) }]).unwrap();
        g.add_node("quad", Some(me), None).unwrap();
        let (json, bin) = chunks(&g.finish("omni").unwrap());
        assert!(json.contains("\"alphaMode\":\"MASK\"") && json.contains("\"POSITION\"") && json.contains("image/png"));
        assert!(json.contains("\"min\":[-1,0,0]") && json.contains("\"max\":[0,1,0]"), "{json}");
        assert!(bin.len() >= 48 + 48 + 32 + 24);
    }

    #[test]
    fn frames_are_proper_rotations() {
        assert_eq!(Frame::FacingZ.vec([1., 2., 3.]), [-1., 3., 2.]);
        assert_eq!(Frame::Viewer.vec([1., 2., 3.]), [1., 3., -2.]);
    }

    #[test]
    fn malformed_meshes_are_rejected() {
        let (p, n, u, _) = quad();
        let mut g = Glb::new(Frame::FacingZ);
        let bad = |i: &[u32], g: &mut Glb| g.add_mesh("m", &[Prim { positions: &p, normals: &n, uvs: &u, indices: i, tangents: None, joints: None, weights: None, material: None }]);
        assert!(bad(&[0, 1, 9], &mut g).is_err());
        assert!(bad(&[0, 1], &mut g).is_err());
        let nan = vec![f32::NAN; 12];
        assert!(g.add_mesh("m", &[Prim { positions: &nan, normals: &n, uvs: &u, indices: &[0, 1, 2], tangents: None, joints: None, weights: None, material: None }]).is_err());
        assert!(g.add_node("x", Some(3), None).is_err());
    }

    #[test]
    fn skinned_mesh_normalises_weights_and_clamps_joints() {
        let (p, n, u, i) = quad();
        let mut g = Glb::new(Frame::FacingZ);
        let mut w = vec![0.0f64; 32];
        for k in 0..2 {
            for d in 0..4 {
                w[k * 16 + d * 5] = 1.0;
            }
        }
        w[16 + 11] = 1.0;
        let skin = g.add_skeleton(&["root".into(), "top".into()], &[-1, 0], &w).unwrap();
        let joints = [0u16, 7, 0, 0].repeat(4);
        let weights = [0.5f32, 0.5, 0.0, 0.0].repeat(4);
        let me = g.add_mesh("m", &[Prim { positions: &p, normals: &n, uvs: &u, indices: &i, tangents: None, joints: Some(&joints), weights: Some(&weights), material: None }]).unwrap();
        g.add_node("m", Some(me), Some(skin)).unwrap();
        let (json, _) = chunks(&g.finish("omni").unwrap());
        assert!(json.contains("JOINTS_0") && json.contains("WEIGHTS_0") && json.contains("inverseBindMatrices") && json.contains("\"skeleton\":0"), "{json}");
        assert!(json.contains("\"scenes\":[{\"nodes\":[0,2]}]"), "{json}");
    }

    #[test]
    fn skeleton_rejects_bad_parents_and_singular_matrices() {
        let mut id = vec![0.0f64; 16];
        for d in 0..4 {
            id[d * 5] = 1.0;
        }
        let mut g = Glb::new(Frame::FacingZ);
        assert!(g.add_skeleton(&["a".into()], &[0], &id).is_err());
        assert!(g.add_skeleton(&["a".into()], &[-1], &[0.0; 16]).is_err());
        assert!(g.add_skeleton(&["a".into()], &[-1], &id).is_ok());
    }

    #[test]
    fn tangent_handedness_and_zero_tangents() {
        let (p, n, u, i) = quad();
        let t = [0., 0., 0., 200., 1., 0., 0., 10., 1., 0., 0., -1., 1., 0., 0., 1.];
        let mut g = Glb::new(Frame::Viewer);
        g.add_mesh("m", &[Prim { positions: &p, normals: &n, uvs: &u, indices: &i, tangents: Some(&t), joints: None, weights: None, material: None }]).unwrap();
        let (json, _) = chunks(&g.finish("omni").unwrap());
        assert!(json.contains("TANGENT"));
    }

    #[test]
    fn images_fit_and_jpeg() {
        let px = vec![255u8; 64 * 32 * 4];
        let (o, w, h) = fit_rgba(&px, 64, 32, 16);
        assert_eq!((w, h, o.len()), (16, 8, 16 * 8 * 4));
        let (o, w, h) = fit_rgba(&px, 64, 32, 0);
        assert_eq!((w, h, o.len()), (64, 32, px.len()));
        let (_, w, _) = fit_rgba(&vec![1u8; 30 * 10 * 4], 30, 10, 10);
        assert_eq!(w, 10);
        let (jpg, mime) = encode_image(&px, 64, 32, 0, 88).unwrap();
        assert_eq!((mime, &jpg[..2]), ("image/jpeg", &[0xFF, 0xD8][..]));
        assert!(encode_image(&px[..10], 64, 32, 0, 0).is_err());
        assert!(encode_image(&[], 0, 0, 0, 0).is_err());
    }

    #[test]
    fn json_strings_are_escaped() {
        let j = J::Obj(vec![("n", J::Str("a\"b\\c\n\u{1}".into()))]).to_string_compact();
        assert_eq!(j, "{\"n\":\"a\\\"b\\\\c\\n\\u0001\"}");
    }
}
