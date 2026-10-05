//! Mesh clean-up used by the collision builder (hash based replacements for `np.unique(axis=0)`, which sorts
//! millions of rows).

use std::collections::HashMap;

/// Merge vertices closer than `cell` (grid snapping, round-half-even like numpy) and drop degenerate triangles.
/// Output vertex order is the lexicographic order of the grid keys (identical to `np.unique(axis=0)`).
pub fn weld(pos: &[f64], tris: &[i64], cell: f64) -> Result<(Vec<f64>, Vec<i64>), String> {
    let n = pos.len() / 3;
    if tris.iter().any(|&t| t < 0 || t as usize >= n) {
        return Err("triangle index out of range".into());
    }
    let keys: Vec<[i64; 3]> = (0..n).map(|i| [0, 1, 2].map(|k| (pos[i * 3 + k] / cell).round_ties_even() as i64)).collect();
    let mut map: HashMap<[i64; 3], usize> = HashMap::with_capacity(n);
    let mut uniq: Vec<[i64; 3]> = Vec::new();
    let mut first: Vec<usize> = Vec::with_capacity(n);       // vertex -> provisional id
    for k in &keys {
        let id = *map.entry(*k).or_insert_with(|| {
            uniq.push(*k);
            uniq.len() - 1
        });
        first.push(id);
    }
    let mut order: Vec<usize> = (0..uniq.len()).collect();
    order.sort_by_key(|&i| uniq[i]);
    let mut rank = vec![0usize; uniq.len()];
    for (r, &i) in order.iter().enumerate() {
        rank[i] = r;
    }
    let inv: Vec<usize> = first.iter().map(|&p| rank[p]).collect();
    let mut sum = vec![0.0f64; uniq.len() * 3];
    let mut cnt = vec![0.0f64; uniq.len()];
    for (i, &g) in inv.iter().enumerate() {
        for k in 0..3 {
            sum[g * 3 + k] += pos[i * 3 + k];
        }
        cnt[g] += 1.0;
    }
    for g in 0..uniq.len() {
        for k in 0..3 {
            sum[g * 3 + k] /= cnt[g];
        }
    }
    let mut out = Vec::with_capacity(tris.len());
    for t in tris.chunks_exact(3) {
        let (a, b, c) = (inv[t[0] as usize] as i64, inv[t[1] as usize] as i64, inv[t[2] as usize] as i64);
        if a != b && b != c && a != c {
            out.extend([a, b, c]);
        }
    }
    Ok((sum, out))
}

/// (|signed volume|, fraction of unique edges used by exactly one triangle).
pub fn mesh_volume(verts: &[f64], tris: &[i64]) -> Result<(f64, f64), String> {
    let n = verts.len() / 3;
    if tris.is_empty() {
        return Ok((0.0, 1.0));
    }
    if tris.iter().any(|&t| t < 0 || t as usize >= n) {
        return Err("triangle index out of range".into());
    }
    let v = |i: i64| &verts[i as usize * 3..i as usize * 3 + 3];
    let mut vol = 0.0;
    let mut edges: HashMap<(i64, i64), u32> = HashMap::with_capacity(tris.len() * 3 / 2);
    for t in tris.chunks_exact(3) {
        let (a, b, c) = (v(t[0]), v(t[1]), v(t[2]));
        let cr = [b[1] * c[2] - b[2] * c[1], b[2] * c[0] - b[0] * c[2], b[0] * c[1] - b[1] * c[0]];
        vol += a[0] * cr[0] + a[1] * cr[1] + a[2] * cr[2];
        for (p, q) in [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])] {
            *edges.entry((p.min(q), p.max(q))).or_insert(0) += 1;
        }
    }
    let open = edges.values().filter(|&&c| c == 1).count();
    Ok(((vol / 6.0).abs(), open as f64 / edges.len() as f64))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn welds_and_measures() {
        // unit tetrahedron with a duplicated vertex (seam)
        let pos = [0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.00001];
        let tris = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 4];
        let (v, t) = weld(&pos, &tris, 1e-4).unwrap();
        assert_eq!(v.len(), 4 * 3);
        assert_eq!(t.len(), 12);
        let tet = [0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1.];
        let (vol, open) = mesh_volume(&tet, &[0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]).unwrap();
        assert!((vol - 1.0 / 6.0).abs() < 1e-9 && open == 0.0);
    }
}
