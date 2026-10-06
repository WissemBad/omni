//! CPython bindings of the remaining-time estimators (see `eta.rs`). Time defaults to a monotonic clock; the
//! optional `t` argument (seconds) lets tests replay hours at once.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use pyo3::prelude::*;

use crate::eta;

fn clock() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

/// Eta(total, weights=None, workers=0): ``done(key, seconds, weight, t)`` per finished item, ``estimate(t)`` -> seconds.
#[pyclass(frozen)]
pub struct Eta {
    inner: Mutex<eta::Eta>,
}

#[pymethods]
impl Eta {
    #[new]
    #[pyo3(signature = (total, weights=None, workers=0, t=None))]
    fn new(total: usize, weights: Option<HashMap<String, f64>>, workers: usize, t: Option<f64>) -> Self {
        Eta { inner: Mutex::new(eta::Eta::new(total, weights.unwrap_or_default(), workers, t.unwrap_or_else(clock))) }
    }

    #[pyo3(signature = (key=None, seconds=None, weight=None, t=None))]
    fn done(&self, key: Option<String>, seconds: Option<f64>, weight: Option<f64>, t: Option<f64>) {
        let now = t.unwrap_or_else(clock);
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).done(key.as_deref(), seconds, weight, now);
    }

    #[pyo3(signature = (t=None))]
    fn estimate(&self, t: Option<f64>) -> Option<f64> {
        let now = t.unwrap_or_else(clock);
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).estimate(now)
    }
}

/// Rate(): ``update(done, total, t)``, ``estimate(t)`` -> seconds from the speed of the last seconds.
#[pyclass(frozen)]
pub struct Rate {
    inner: Mutex<eta::Rate>,
}

#[pymethods]
impl Rate {
    #[new]
    fn new() -> Self {
        Rate { inner: Mutex::new(eta::Rate::default()) }
    }

    #[pyo3(signature = (done, total=None, t=None))]
    fn update(&self, done: f64, total: Option<f64>, t: Option<f64>) {
        let now = t.unwrap_or_else(clock);
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).update(done, total, now);
    }

    #[pyo3(signature = (t=None))]
    fn estimate(&self, t: Option<f64>) -> Option<f64> {
        let now = t.unwrap_or_else(clock);
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).estimate(now)
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Eta>()?;
    m.add_class::<Rate>()?;
    Ok(())
}
