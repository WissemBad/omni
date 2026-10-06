//! Remaining-time estimates for long jobs whose items are very unequal.
//!
//! A batch that starts with its biggest items (props are ordered by mesh size) takes 40 h by a plain "items done /
//! time elapsed" rule at the start and 3 h in the end. Two estimators avoid that:
//!
//! * [`Eta`]  items with a known weight (mesh size, number of variations...): the cost of an item is learnt while
//!   the batch runs (`seconds = c0 + c1 * (weight + 1) ^ p`, refitted on the finished items) and the remaining items
//!   are priced with it; the number of items running side by side is measured on the recent completions.
//! * [`Rate`] a counter without item weights (a loop, a download, a stage): speed over a recent window, not the
//!   average since the start.
//!
//! Both return seconds or `None` while there is not enough to say, and are smoothed so the figure does not jump.
//! Time is a plain `f64` (seconds, any origin) passed by the caller: the tests replay hours in microseconds.
//! Checked on a real 27,686-prop conversion (26 CPU-hours): within about +/-30 % from 3 % of progress on.

use std::collections::{HashMap, VecDeque};

/// Finished items with a duration before the cost model is trusted.
const MIN_FIT: usize = 20;
/// Seconds: no item is free (start-up, files, compile).
const FLOOR: f64 = 1.2;
const GRID: [f64; 8] = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35];

/// Moves towards each new estimate with a time constant, counting the time already elapsed since the last one.
struct Smooth {
    tau: f64,
    value: Option<f64>,
    at: f64,
}

impl Smooth {
    fn new() -> Self {
        Smooth { tau: 8.0, value: None, at: 0.0 }
    }

    fn apply(&mut self, est: Option<f64>, now: f64) -> Option<f64> {
        let Some(est) = est else {
            return self.value.map(|v| (v - (now - self.at)).max(0.0));
        };
        let v = match self.value {
            None => est,
            Some(v) => {
                let prev = (v - (now - self.at)).max(0.0);
                prev + (1.0 - (-(now - self.at).max(0.0) / self.tau).exp()) * (est - prev)
            }
        };
        self.value = Some(v);
        self.at = now;
        Some(v)
    }
}

/// Best `y = FLOOR + c1 * (w + 1) ^ p` over the grid of exponents (the per-item overhead is a prior: the items seen
/// first are the big ones and cannot tell it; c1 >= 0 by least squares on seconds, the exponent by relative error).
fn fit(w: &[f64], y: &[f64]) -> (f64, [f64; 2]) {
    let mut best: Option<(f64, f64, [f64; 2])> = None;
    for &p in &GRID {
        let x: Vec<f64> = w.iter().map(|v| (v + 1.0).powf(p)).collect();
        let sxx: f64 = x.iter().map(|v| v * v).sum();
        let sxy: f64 = x.iter().zip(y).map(|(a, b)| a * (b - FLOOR)).sum();
        let c = [FLOOR, if sxx > 0.0 { (sxy / sxx).max(0.0) } else { 0.0 }];
        let err: f64 = x.iter().zip(y).map(|(a, b)| ((c[0] + c[1] * a - b) / b.max(0.2)).powi(2)).sum();
        if best.map_or(true, |b| err < b.0) {
            best = Some((err, p, c));
        }
    }
    let (_, p, c) = best.unwrap_or((0.0, 1.0, [FLOOR, 0.0]));
    (p, c)
}

pub struct Eta {
    total: usize,
    workers: f64,
    has_weights: bool,
    /// Weights of the items not finished yet.
    left: HashMap<String, f64>,
    /// Median weight: the per-item overhead of the rate fallback, in weight units.
    med: f64,
    n: usize,
    t0: f64,
    /// (time, item seconds, cost) of the latest completions.
    recent: VecDeque<(f64, Option<f64>, f64)>,
    /// Finished items with a duration: weight and seconds.
    w: Vec<f64>,
    y: Vec<f64>,
    model: Option<(f64, [f64; 2])>,
    model_n: usize,
    smooth: Smooth,
    cache: (f64, Option<f64>),
}

impl Eta {
    /// `weights`: {key: weight} of every item (bigger = slower; may be empty); `workers`: how many run side by
    /// side (used until the recent completions say otherwise).
    pub fn new(total: usize, weights: HashMap<String, f64>, workers: usize, now: f64) -> Self {
        let mut v: Vec<f64> = weights.values().copied().collect();
        v.sort_by(|a, b| a.total_cmp(b));
        let med = if v.is_empty() { 0.0 } else { (v[(v.len() - 1) / 2] + v[v.len() / 2]) / 2.0 };
        Eta {
            total,
            workers: workers as f64,
            has_weights: !weights.is_empty(),
            left: weights,
            med,
            n: 0,
            t0: now,
            recent: VecDeque::with_capacity(300),
            w: Vec::new(),
            y: Vec::new(),
            model: None,
            model_n: 0,
            smooth: Smooth::new(),
            cache: (-1e9, None),
        }
    }

    /// One item finished: `seconds` is its own duration when known, `weight` overrides the one given at the start.
    pub fn done(&mut self, key: Option<&str>, seconds: Option<f64>, weight: Option<f64>, now: f64) {
        let known = key.and_then(|k| self.left.remove(k));
        let w = weight.or(known);
        self.n += 1;
        let cost = w.map_or(1.0, |w| w + self.med);
        if self.recent.len() == 300 {
            self.recent.pop_front();
        }
        self.recent.push_back((now, seconds, cost));
        if let (Some(w), Some(s)) = (w, seconds) {
            if s > 0.0 {
                self.w.push(w);
                self.y.push(s);
            }
        }
    }

    /// Items' seconds finished per second of waiting, over the recent completions (steady state).
    fn parallel(&self) -> f64 {
        let pts: Vec<_> = self.recent.iter().filter(|p| p.1.is_some()).collect();
        if pts.len() >= 10 {
            let span = pts[pts.len() - 1].0 - pts[0].0;
            if span >= 5.0 {
                let par = pts[1..].iter().filter_map(|p| p.1).sum::<f64>() / span;
                return if self.workers > 0.0 { par.max(0.3).min(self.workers * 1.1) } else { par.max(0.3) };
            }
        }
        if self.workers > 0.0 { self.workers } else { 1.0 }
    }

    /// Real / predicted seconds on the latest items (the nearest in size to what is left), within 0.5-2.
    fn bias(&self, p: f64, c: [f64; 2]) -> f64 {
        let k = self.y.len().min(30.max(self.y.len() / 10));
        let (w, y) = (&self.w[self.w.len() - k..], &self.y[self.y.len() - k..]);
        let pred: f64 = w.iter().map(|v| c[0] + c[1] * (v + 1.0).powf(p)).sum();
        if pred > 0.0 { (y.iter().sum::<f64>() / pred).clamp(0.5, 2.0) } else { 1.0 }
    }

    fn by_model(&mut self) -> Option<f64> {
        if self.y.len() < MIN_FIT || self.left.is_empty() {
            return None;
        }
        // refitted geometrically: cheap on 30k items
        if self.model.is_none() || self.n as f64 >= self.model_n as f64 * 1.1 + 10.0 {
            self.model = Some(fit(&self.w, &self.y));
            self.model_n = self.n;
        }
        let (p, c) = self.model?;
        let mut cpu: f64 = self.left.values().map(|w| c[0] + c[1] * (w + 1.0).powf(p)).sum();
        let frac = if self.total > 0 { self.n as f64 / self.total as f64 } else { 0.0 };
        if frac > 0.8 {
            // near the end the latest items are the best reference
            cpu *= 1.0 + ((frac - 0.8) / 0.15).min(1.0) * (self.bias(p, c) - 1.0);
        }
        // items without a weight: an average one
        let unknown = self.total.saturating_sub(self.n + self.left.len()) as f64;
        cpu += unknown * (cpu / self.left.len().max(1) as f64);
        Some(cpu / self.parallel())
    }

    fn by_rate(&self) -> Option<f64> {
        let pts: Vec<_> = self.recent.iter().copied().collect();
        if pts.len() < 2 {
            return None;
        }
        let last = pts[pts.len() - 1].0;
        let span = 15f64.max((last - self.t0) * 0.25);
        let mut win: Vec<_> = pts.iter().filter(|p| last - p.0 <= span).copied().collect();
        if win.len() < 2 {
            win = pts[pts.len() - 2..].to_vec();
        }
        let dt = win[win.len() - 1].0 - win[0].0;
        if dt <= 0.0 {
            return None;
        }
        let rate = win[1..].iter().map(|p| p.2).sum::<f64>() / dt; // cost per second
        let unknown = self.total.saturating_sub(self.n + self.left.len()) as f64;
        let rest = if self.has_weights {
            self.left.values().sum::<f64>() + (self.left.len() as f64 + unknown) * self.med
        } else {
            self.total.saturating_sub(self.n) as f64
        };
        if rate > 0.0 { Some(rest / rate) } else { None }
    }

    /// Seconds left, or None. Recomputed at most once a second, whoever asks.
    pub fn estimate(&mut self, now: f64) -> Option<f64> {
        if self.total > 0 && self.n >= self.total {
            return Some(0.0);
        }
        if now - self.cache.0 < 1.0 {
            return self.cache.1.map(|v| (v - (now - self.cache.0)).max(0.0));
        }
        let est = self.by_model().or_else(|| self.by_rate());
        let out = self.smooth.apply(est, now);
        self.cache = (now, out);
        out
    }

    pub fn done_count(&self) -> usize {
        self.n
    }
}

/// `update(done, total)`; `estimate()` from the speed of the last seconds.
pub struct Rate {
    pts: VecDeque<(f64, f64)>,
    total: f64,
    smooth: Smooth,
}

impl Default for Rate {
    fn default() -> Self {
        Rate { pts: VecDeque::with_capacity(600), total: 0.0, smooth: Smooth::new() }
    }
}

impl Rate {
    pub fn update(&mut self, done: f64, total: Option<f64>, now: f64) {
        if let Some(t) = total {
            self.total = t;
        }
        if self.pts.back().map_or(false, |p| done < p.1) {
            self.pts.clear(); // the counter restarted
        }
        if self.pts.back().map_or(true, |p| now - p.0 >= 0.2) || done >= self.total {
            if self.pts.len() == 600 {
                self.pts.pop_front();
            }
            self.pts.push_back((now, done));
        }
    }

    pub fn estimate(&mut self, now: f64) -> Option<f64> {
        if self.pts.len() < 2 || self.total <= 0.0 {
            return None;
        }
        let (t1, d1) = *self.pts.back()?;
        if d1 >= self.total {
            return Some(0.0);
        }
        let span = 15f64.max((t1 - self.pts[0].0) * 0.25);
        let mut win: Vec<_> = self.pts.iter().filter(|p| t1 - p.0 <= span).copied().collect();
        if win.len() < 2 {
            win = self.pts.iter().rev().take(2).rev().copied().collect();
        }
        let (dt, dd) = (win[win.len() - 1].0 - win[0].0, win[win.len() - 1].1 - win[0].1);
        if dt <= 0.0 || dd <= 0.0 {
            return None;
        }
        self.smooth.apply(Some((self.total - d1) / (dd / dt)), now)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Deterministic pseudo-random numbers in [0, 1).
    fn rng(seed: &mut u64) -> f64 {
        *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        (*seed >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Heavy-tailed items processed biggest first by `workers` workers; estimates against the real remaining time.
    fn replay(n: usize, workers: usize, with_weights: bool) -> Vec<(f64, f64, f64)> {
        let mut seed = 7u64;
        let mut items: Vec<(f64, f64)> = (0..n)
            .map(|_| {
                let w = (rng(&mut seed) * 12.0).exp(); // 1 .. 160,000: a long tail like mesh sizes
                (w, 0.9 + 0.0004 * w.powf(1.05) * (0.7 + 0.6 * rng(&mut seed)))
            })
            .collect();
        items.sort_by(|a, b| b.0.total_cmp(&a.0));
        let weights: HashMap<String, f64> = if with_weights {
            items.iter().enumerate().map(|(i, it)| (i.to_string(), it.0)).collect()
        } else {
            HashMap::new()
        };
        let mut eta = Eta::new(n, weights, workers, 0.0);
        let mut t = vec![0.0; n];
        let mut acc = 0.0;
        for (i, it) in items.iter().enumerate() {
            acc += it.1 / workers as f64;
            t[i] = acc;
        }
        let total = acc;
        let mut out = Vec::new();
        for (i, it) in items.iter().enumerate() {
            eta.done(Some(&i.to_string()), Some(it.1), None, t[i]);
            if [n / 20, n / 10, n / 4, n / 2, n * 3 / 4, n * 9 / 10].contains(&i) {
                let est = eta.estimate(t[i] + 1.5).unwrap_or(f64::NAN);
                out.push((i as f64 / n as f64, est, total - t[i]));
            }
        }
        out
    }

    #[test]
    fn weighted_estimate_follows_a_heavy_tail() {
        for (frac, est, real) in replay(8000, 8, true) {
            assert!(est > real * 0.5 && est < real * 2.0, "at {frac}: estimated {est:.0}s, real {real:.0}s");
        }
    }

    #[test]
    fn without_weights_the_rate_is_recent_not_average() {
        // equal items: a plain rate is right
        let mut e = Eta::new(1000, HashMap::new(), 0, 0.0);
        for i in 0..400 {
            e.done(None, None, None, i as f64);
        }
        let est = e.estimate(400.0).unwrap();
        assert!((est - 600.0).abs() < 60.0, "{est}");
        // the speed doubles: the estimate follows the last seconds
        for i in 0..200 {
            e.done(None, None, None, 400.0 + i as f64 * 0.5);
        }
        let est = e.estimate(502.0).unwrap();                  // 400 items left at 2 per second
        assert!(est < 300.0 && est > 150.0, "{est}");
    }

    #[test]
    fn finished_is_zero_and_nothing_to_say_is_none() {
        let mut e = Eta::new(2, HashMap::new(), 0, 0.0);
        assert_eq!(e.estimate(1.0), None);
        e.done(None, None, None, 1.0);
        e.done(None, None, None, 2.0);
        assert_eq!(e.estimate(2.0), Some(0.0));
        let mut r = Rate::default();
        assert_eq!(r.estimate(0.0), None);
        r.update(0.0, Some(100.0), 0.0);
        r.update(10.0, None, 5.0);
        let est = r.estimate(5.0).unwrap();
        assert!((est - 45.0).abs() < 1.0, "{est}");
        r.update(100.0, None, 50.0);
        assert_eq!(r.estimate(50.0), Some(0.0));
    }

    #[test]
    fn fit_recovers_a_linear_cost() {
        let w: Vec<f64> = (0..200).map(|i| i as f64 * 50.0).collect();
        let y: Vec<f64> = w.iter().map(|w| FLOOR + 0.01 * (w + 1.0)).collect();
        let (p, c) = fit(&w, &y);
        assert!((p - 1.0).abs() < 1e-9 && (c[0] - FLOOR).abs() < 1e-9 && (c[1] - 0.01).abs() < 1e-3, "{p} {c:?}");
    }

    #[test]
    fn garbage_never_panics() {
        let mut e = Eta::new(0, HashMap::from([("a".to_string(), f64::NAN)]), 0, 0.0);
        for i in 0..100 {
            e.done(Some("a"), Some(f64::INFINITY), Some(-5.0), i as f64);
            let _ = e.estimate(i as f64 * 2.0);
        }
        let mut r = Rate::default();
        r.update(f64::NAN, Some(f64::INFINITY), 1.0);
        r.update(1.0, Some(-1.0), 0.5);
        let _ = r.estimate(1.0);
    }
}
