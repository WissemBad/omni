//! Linear-blend skinning of a posed character part.

use crate::par::*;

/// `k`: per-bone 4x4 row-major matrices (bind -> posed), `joints`/`weights`: (n, nj).
/// Returns posed positions and re-normalised normals (flat, n*3 each). Out-of-range joints are clamped.
pub fn lbs(v: &[f64], nrm: &[f64], joints: &[i64], weights: &[f64], nj: usize, k: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let nb = k.len() / 16;
    let n = v.len() / 3;
    let mut ov = vec![0.0; n * 3];
    let mut on = vec![0.0; n * 3];
    ov.par_chunks_mut(3).zip(on.par_chunks_mut(3)).enumerate().for_each(|(i, (pv, pn))| {
        let (p, q) = (&v[i * 3..i * 3 + 3], &nrm[i * 3..i * 3 + 3]);
        for j in 0..nj {
            let w = weights[i * nj + j];
            if w == 0.0 || nb == 0 {
                continue;
            }
            let b = (joints[i * nj + j].max(0) as usize).min(nb - 1);
            let m = &k[b * 16..b * 16 + 16];
            for r in 0..3 {
                pv[r] += w * (m[r * 4] * p[0] + m[r * 4 + 1] * p[1] + m[r * 4 + 2] * p[2] + m[r * 4 + 3]);
                pn[r] += w * (m[r * 4] * q[0] + m[r * 4 + 1] * q[1] + m[r * 4 + 2] * q[2]);
            }
        }
        let l = (pn[0] * pn[0] + pn[1] * pn[1] + pn[2] * pn[2]).sqrt().max(1e-9);
        pn.iter_mut().for_each(|x| *x /= l);
    });
    (ov, on)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn translates_and_normalises() {
        let mut k = vec![0.0; 16];
        for i in 0..4 {
            k[i * 5] = 1.0;
        }
        k[3] = 2.0;
        let (v, n) = lbs(&[1.0, 0.0, 0.0], &[0.0, 0.0, 3.0], &[0], &[1.0], 1, &k);
        assert_eq!(v, vec![3.0, 0.0, 0.0]);
        assert!((n[2] - 1.0).abs() < 1e-12);
    }
}
