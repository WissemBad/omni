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

/// Static (single bone, no weights) triangles of a prop: `"<material>\n<3 vertex lines>\n"` per triangle. Positions are
/// already scaled and the V coordinate already flipped. Built in parallel chunks (a prop can have millions of rows).
pub fn static_triangles(material: &str, pos: &[f64], nrm: &[f64], uv: &[f64], indices: &[i64]) -> Result<(String, usize), String> {
    let n = pos.len() / 3;
    if nrm.len() != n * 3 || uv.len() != n * 2 {
        return Err("inconsistent mesh arrays".into());
    }
    if indices.len() % 3 != 0 || indices.iter().any(|&i| i < 0 || i as usize >= n) {
        return Err("bad triangle indices".into());
    }
    const CHUNK: usize = 3 * 2048;
    let parts: Vec<String> = indices
        .par_chunks(CHUNK)
        .map(|chunk| {
            let mut s = String::with_capacity(chunk.len() / 3 * (material.len() + 1 + 3 * 80));
            for tri in chunk.chunks_exact(3) {
                s.push_str(material);
                s.push('\n');
                for &i in tri {
                    let i = i as usize;
                    let _ = writeln!(
                        s,
                        "0 {:.5} {:.5} {:.5} {:.5} {:.5} {:.5} {:.5} {:.5}",
                        pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2], nrm[i * 3], nrm[i * 3 + 1], nrm[i * 3 + 2], uv[i * 2], uv[i * 2 + 1]
                    );
                }
            }
            s
        })
        .collect();
    Ok((parts.concat(), indices.len() / 3))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn static_rows_match_the_skinned_writer_without_links() {
        let (s, c) = static_triangles("m", &[1.0, 2.0, -0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0], &[0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
                                      &[0.0, 1.0, 0.5, 0.5, 1.0, 0.0], &[0, 1, 2]).unwrap();
        assert_eq!(c, 1);
        assert_eq!(s, "m\n0 1.00000 2.00000 -0.00000 0.00000 0.00000 1.00000 0.00000 1.00000\n0 0.00000 0.00000 0.00000 0.00000 0.00000 1.00000 0.50000 0.50000\n0 1.00000 1.00000 1.00000 0.00000 0.00000 1.00000 1.00000 0.00000\n");
        assert!(static_triangles("m", &[0.0; 3], &[0.0; 3], &[0.0; 2], &[0, 0, 1]).is_err());
    }

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
