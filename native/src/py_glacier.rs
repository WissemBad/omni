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

    /// labels(hashes, threads=0) -> [original Wwise name or ""] of the .wem resources, decompressed and read in parallel.
    #[pyo3(signature = (hashes, threads=0))]
    fn labels(&self, py: Python<'_>, hashes: Vec<u64>, threads: usize) -> PyResult<Vec<String>> {
        let p = pool(threads)?;
        let store = &self.store;
        Ok(py.allow_threads(|| {
            p.install(|| {
                use rayon::prelude::*;
                hashes.par_iter().map(|h| store.read(*h).map(|d| crate::scan::wem_label_of(&d)).unwrap_or_default()).collect()
            })
        }))
    }

    fn package_of(&self, hash: u64) -> Option<String> {
        self.store.package_of(hash).map(|p| p.display().to_string())
    }

    #[getter]
    fn packages(&self) -> Vec<String> {
        self.store.packages.iter().map(|p| p.display().to_string()).collect()
    }
}

/// prim_headers(paths, threads=0) -> [(size, flags) | None]: size and header flags of many PRIM files, read in
/// parallel (opening a file costs milliseconds under a real-time antivirus).
#[pyfunction]
#[pyo3(signature = (paths, threads=0))]
fn prim_headers(py: Python<'_>, paths: Vec<String>, threads: usize) -> PyResult<Vec<Option<(u64, u32)>>> {
    let p = pool(threads)?;
    Ok(py.allow_threads(|| p.install(|| crate::scan::prim_headers(&paths))))
}

fn pool(threads: usize) -> PyResult<rayon::ThreadPool> {
    rayon::ThreadPoolBuilder::new().num_threads(if threads == 0 { 32 } else { threads }).build().map_err(err)
}

/// wem_labels([(file, offset, size)], threads=0) -> [original Wwise name or ""], one parallel pass over the chunk
/// headers of the sounds.
#[pyfunction]
#[pyo3(signature = (items, threads=0))]
fn wem_labels(py: Python<'_>, items: Vec<(String, u64, i64)>, threads: usize) -> PyResult<Vec<String>> {
    let p = pool(threads)?;
    Ok(py.allow_threads(|| p.install(|| crate::scan::wem_labels(&items))))
}

/// meta_refs_flags(paths, threads=0) -> [[(hash, flag)] | None] for each "<path>.meta".
#[pyfunction]
#[pyo3(signature = (paths, threads=0))]
fn meta_refs_flags(py: Python<'_>, paths: Vec<String>, threads: usize) -> PyResult<Vec<Option<Vec<(u64, u8)>>>> {
    let p = pool(threads)?;
    Ok(py.allow_threads(|| p.install(|| crate::scan::meta_refs_flags_many(&paths))))
}

/// read_files(paths, threads=0) -> [bytes | None]: whole files read in parallel.
#[pyfunction]
#[pyo3(signature = (paths, threads=0))]
fn read_files<'py>(py: Python<'py>, paths: Vec<String>, threads: usize) -> PyResult<Vec<Option<Bound<'py, PyBytes>>>> {
    let p = pool(threads)?;
    let data = py.allow_threads(|| p.install(|| crate::scan::read_files(&paths)));
    Ok(data.into_iter().map(|d| d.map(|b| PyBytes::new_bound(py, &b))).collect())
}

/// mate_slots(data) -> [(slot, meaning | None)]: the texture slots of a material class (see `mate.rs`).
#[pyfunction]
fn mate_slots(py: Python<'_>, data: &[u8]) -> Vec<(String, Option<String>)> {
    py.allow_threads(|| crate::mate::slots(data))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<GlacierStore>()?;
    m.add_function(wrap_pyfunction!(mate_slots, m)?)?;
    m.add_function(wrap_pyfunction!(wem_labels, m)?)?;
    m.add_function(wrap_pyfunction!(meta_refs_flags, m)?)?;
    m.add_function(wrap_pyfunction!(read_files, m)?)?;
    m.add_function(wrap_pyfunction!(prim_headers, m)?)?;
    Ok(())
}
