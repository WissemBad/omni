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
        let store = py.detach(|| Store::open(&paths)).map_err(err)?;
        Ok(GlacierStore { store: Arc::new(store) })
    }

    fn __len__(&self) -> usize {
        self.store.len()
    }

    /// {type: count}
    fn types<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in self.store.types() {
            d.set_item(k, v)?;
        }
        Ok(d)
    }

    /// Hashes of the resources of a type.
    fn of_type(&self, py: Python<'_>, ty: &str) -> Vec<u64> {
        let ty = ty.to_string();
        py.detach(|| self.store.of_type(&ty))
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
        let data = py.detach(|| self.store.read(hash)).map_err(err)?;
        Ok(PyBytes::new(py, &data))
    }

    /// labels(hashes, threads=0) -> [original Wwise name or ""] of the .wem resources, decompressed and read in parallel.
    #[pyo3(signature = (hashes, threads=0))]
    fn labels(&self, py: Python<'_>, hashes: Vec<u64>, threads: usize) -> PyResult<Vec<String>> {
        let p = pool(threads)?;
        let store = &self.store;
        Ok(py.detach(|| {
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
    Ok(py.detach(|| p.install(|| crate::scan::prim_headers(&paths))))
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
    Ok(py.detach(|| p.install(|| crate::scan::wem_labels(&items))))
}

/// meta_refs_flags(paths, threads=0) -> [[(hash, flag)] | None] for each "<path>.meta".
#[pyfunction]
#[pyo3(signature = (paths, threads=0))]
fn meta_refs_flags(py: Python<'_>, paths: Vec<String>, threads: usize) -> PyResult<Vec<Option<Vec<(u64, u8)>>>> {
    let p = pool(threads)?;
    Ok(py.detach(|| p.install(|| crate::scan::meta_refs_flags_many(&paths))))
}

/// read_files(paths, threads=0) -> [bytes | None]: whole files read in parallel.
#[pyfunction]
#[pyo3(signature = (paths, threads=0))]
fn read_files<'py>(py: Python<'py>, paths: Vec<String>, threads: usize) -> PyResult<Vec<Option<Bound<'py, PyBytes>>>> {
    let p = pool(threads)?;
    let data = py.detach(|| p.install(|| crate::scan::read_files(&paths)));
    Ok(data.into_iter().map(|d| d.map(|b| PyBytes::new(py, &b))).collect())
}

/// mate_slots(data) -> [(slot, meaning | None)]: the texture slots of a material class (see `mate.rs`).
#[pyfunction]
fn mate_slots(py: Python<'_>, data: &[u8]) -> Vec<(String, Option<String>)> {
    py.detach(|| crate::mate::slots(data))
}

/// class_schema(data) -> [(CRC32 of the property name, type, has default)] of a CPPT (see `schema.rs`).
#[pyfunction]
fn class_schema(py: Python<'_>, data: &[u8]) -> PyResult<Vec<(u32, String, bool)>> {
    py.detach(|| crate::schema::class_schema(data))
        .map(|v| v.into_iter().map(|p| (p.crc, p.ty, p.default)).collect())
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

/// blueprint_class(data) -> class name of a CBLU.
#[pyfunction]
fn blueprint_class(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    py.detach(|| crate::schema::blueprint_class(data)).map_err(pyo3::exceptions::PyValueError::new_err)
}

/// enum_def(data) -> (name, [(member, value)], former names) of an ENUM.
#[pyfunction]
fn enum_def(py: Python<'_>, data: &[u8]) -> PyResult<(String, Vec<(String, i32)>, Vec<String>)> {
    py.detach(|| crate::schema::enum_def(data))
        .map(|e| (e.name, e.members, e.legacy))
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

/// parse_model_list(text) -> ["models/a/b.mdl", ...]: the models named by a user's text file, once each, in order.
#[pyfunction]
fn parse_model_list(py: Python<'_>, text: &str) -> Vec<String> {
    py.detach(|| crate::modellist::parse(text))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<GlacierStore>()?;
    m.add_function(wrap_pyfunction!(class_schema, m)?)?;
    m.add_function(wrap_pyfunction!(blueprint_class, m)?)?;
    m.add_function(wrap_pyfunction!(enum_def, m)?)?;
    m.add_function(wrap_pyfunction!(parse_model_list, m)?)?;
    m.add_function(wrap_pyfunction!(mate_slots, m)?)?;
    m.add_function(wrap_pyfunction!(wem_labels, m)?)?;
    m.add_function(wrap_pyfunction!(meta_refs_flags, m)?)?;
    m.add_function(wrap_pyfunction!(read_files, m)?)?;
    m.add_function(wrap_pyfunction!(prim_headers, m)?)?;
    Ok(())
}
