//! Skin weight folding: per vertex, influences that land on the same target bone are summed and only the
//! strongest `max_links` are kept (studiomdl: 3), renormalised. Replaces a per-vertex Python loop.

use crate::par::*;

/// `bones`/`weights`: n x k row-major. Returns (n x max_links bones, n x max_links weights). Unused links
/// repeat the strongest bone with a zero weight; a vertex with no weight goes 100% to `fallback`.
pub fn fold(bones: &[i64], weights: &[f64], n: usize, k: usize, max_links: usize, fallback: i64) -> (Vec<i64>, Vec<f64>) {
    let mut ob = vec![0i64; n * max_links];
    let mut ow = vec![0f64; n * max_links];
    ob.par_chunks_mut(max_links)
        .zip(ow.par_chunks_mut(max_links))
        .enumerate()
        .for_each(|(v, (b_out, w_out))| {
            let mut acc: Vec<(i64, f64)> = Vec::with_capacity(k);
            for j in 0..k {
                let (b, w) = (bones[v * k + j], weights[v * k + j]);
                if !(w > 1e-5) {
                    continue;
                }
                match acc.iter_mut().find(|e| e.0 == b) {
                    Some(e) => e.1 += w,
                    None => acc.push((b, w)),
                }
            }
            // strongest first; ties keep the first-seen order (stable sort), like the Python version
            acc.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            acc.truncate(max_links);
            if acc.is_empty() {
                acc.push((fallback, 1.0));
            }
            let s: f64 = acc.iter().map(|e| e.1).sum();
            for i in 0..max_links {
                if i < acc.len() {
                    b_out[i] = acc[i].0;
                    w_out[i] = acc[i].1 / s;
                } else {
                    b_out[i] = acc[0].0;
                    w_out[i] = 0.0;
                }
            }
        });
    (ob, ow)
}

#[cfg(test)]
mod tests {
    #[test]
    fn merges_and_keeps_top() {
        let bones = [5i64, 5, 7, 9, 1, 2];
        let weights = [0.25f64, 0.25, 0.3, 0.2, 0.0, 0.0];
        let (b, w) = super::fold(&bones, &weights, 1, 6, 3, 0);
        assert_eq!(&b, &[5, 7, 9]);
        assert!((w[0] - 0.5).abs() < 1e-9 && (w[1] - 0.3).abs() < 1e-9);
    }

    #[test]
    fn empty_vertex_goes_to_fallback() {
        let (b, w) = super::fold(&[3, 4], &[0.0, 0.0], 1, 2, 3, 42);
        assert_eq!(b[0], 42);
        assert_eq!(w[0], 1.0);
    }
}
