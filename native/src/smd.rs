//! Valve SMD `triangles` block writer (the Python version formatted ~10 strings per triangle vertex).

use crate::par::*;
use std::fmt::Write;

pub struct Mesh<'a> {
    pub pos: &'a [f64],     // n*3
    pub nrm: &'a [f64],     // n*3
    pub uv: &'a [f64],      // n*2, already flipped (v = 1 - v)
    pub bones: &'a [i64],   // n*links
    pub weights: &'a [f64], // n*links
    pub links: usize,
    pub indices: &'a [u32],
}

fn vertex_line(m: &Mesh, i: usize) -> String {
    let mut links: Vec<(i64, f64)> = (0..m.links)
        .filter(|&k| m.weights[i * m.links + k] > 1e-4)
        .map(|k| (m.bones[i * m.links + k], m.weights[i * m.links + k]))
        .collect();
    if links.is_empty() {
        links.push((m.bones[i * m.links], 1.0));
    }
    let s: f64 = links.iter().map(|l| l.1).sum();
    let mut o = String::with_capacity(96);
    let _ = write!(
        o,
        "{} {:.5} {:.5} {:.5} {:.5} {:.5} {:.5} {:.5} {:.5} {}",
        links[0].0, m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2], m.nrm[i * 3], m.nrm[i * 3 + 1], m.nrm[i * 3 + 2],
        m.uv[i * 2], m.uv[i * 2 + 1], links.len()
    );
    for (b, w) in &links {
        let _ = write!(o, " {} {:.5}", b, w / s);
    }
    o
}

/// "<material>\n<3 vertex lines>\n" for every triangle; returns (text, triangle count).
pub fn triangles(material: &str, m: &Mesh) -> Result<(String, usize), String> {
    let n = m.pos.len() / 3;
    if m.links == 0 || m.nrm.len() != n * 3 || m.uv.len() != n * 2 || m.bones.len() != n * m.links || m.weights.len() != n * m.links {
        return Err("inconsistent mesh arrays".into());
    }
    if m.indices.iter().any(|&i| i as usize >= n) || m.indices.len() % 3 != 0 {
        return Err("bad triangle indices".into());
    }
    let lines: Vec<String> = (0..n).into_par_iter().map(|i| vertex_line(m, i)).collect();
    let mut out = String::with_capacity(m.indices.len() * 100);
    for t in m.indices.chunks_exact(3) {
        out.push_str(material);
        out.push('\n');
        for (j, &i) in t.iter().enumerate() {
            if j > 0 {
                out.push('\n');
            }
            out.push_str(&lines[i as usize]);
        }
        out.push('\n');
    }
    Ok((out, m.indices.len() / 3))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_like_python() {
        let m = Mesh {
            pos: &[1.0, 2.0, -0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
            nrm: &[0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            uv: &[0.0, 1.0, 0.5, 0.5, 1.0, 0.0],
            bones: &[3, 4, 3, 4, 3, 4],
            weights: &[0.75, 0.25, 1.0, 0.0, 0.5, 0.5],
            links: 2,
            indices: &[0, 1, 2],
        };
        let (s, c) = triangles("mat", &m).unwrap();
        assert_eq!(c, 1);
        assert!(s.starts_with("mat\n3 1.00000 2.00000 -0.00000 0.00000 0.00000 1.00000 0.00000 1.00000 2 3 0.75000 4 0.25000\n3 "));
    }
}
