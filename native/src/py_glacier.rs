//! CPython bindings of the Glacier package store (direct reading of a game's .rpkg files).

use std::path::PathBuf;
use std::sync::Arc;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::rpkg_store::Store;

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

#[pyclass(frozen)]
pub struct GlacierStore {
    store: Arc<Store>,
}

#[pymethods]
impl GlacierStore {
    /// GlacierStore(packages) — every chunk*.rpkg / chunk*patch*.rpkg of the game.
    #[new]
    fn new(py: Python<'_>, packages: Vec<String>) -> PyResult<Self> {
        let paths: Vec<PathBuf> = packages.into_iter().map(PathBuf::from).collect();
        let store = py.allow_threads(|| Store::open(&paths)).map_err(err)?;
        Ok(GlacierStore { store: Arc::new(store) })
    }

    fn __len__(&self) -> usize {
        self.store.len()
    }

    /// {type: count}
    fn types<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new_bound(py);
        for (k, v) in self.store.types() {
            d.set_item(k, v)?;
        }
        Ok(d)
    }

    /// Hashes of the resources of a type.
    fn of_type(&self, py: Python<'_>, ty: &str) -> Vec<u64> {
        let ty = ty.to_string();
        py.allow_threads(|| self.store.of_type(&ty))
    }

    fn has(&self, hash: u64) -> bool {
        self.store.entry(hash).is_some()
    }

    /// (type, data size, [(hash, flag)]) of a resource, None when absent.
    fn meta(&self, hash: u64) -> Option<(String, u32, Vec<(u64, u8)>)> {
        self.store.entry(hash).map(|e| (e.type_name(), e.data_size, e.refs.clone()))
    }

    fn type_of(&self, hash: u64) -> Option<String> {
        self.store.entry(hash).map(|e| e.type_name())
    }

    /// Decompressed bytes of a resource.
    fn read<'py>(&self, py: Python<'py>, hash: u64) -> PyResult<Bound<'py, PyBytes>> {
        let data = py.allow_threads(|| self.store.read(hash)).map_err(err)?;
        Ok(PyBytes::new_bound(py, &data))
    }

    fn package_of(&self, hash: u64) -> Option<String> {
        self.store.package_of(hash).map(|p| p.display().to_string())
    }

    #[getter]
    fn packages(&self) -> Vec<String> {
        self.store.packages.iter().map(|p| p.display().to_string()).collect()
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<GlacierStore>()?;
    Ok(())
}
