//! CPython bindings of the Source animation reader (see `source_anim.rs`).

use numpy::{IntoPyArray, PyArray3};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::source_anim as sa;

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// mdl_info(mdl) -> {name, bones: [{name, parent, pos, quat}], sequences: [{name, activity, flags, anim, blends, fps,
/// frames, anim_flags}], includes: [str], animations, needs_ani}
#[pyfunction]
fn mdl_info<'py>(py: Python<'py>, mdl: &[u8]) -> PyResult<Bound<'py, PyDict>> {
    let info = py.allow_threads(|| sa::parse(mdl)).map_err(err)?;
    let d = PyDict::new_bound(py);
    d.set_item("name", &info.name)?;
    let bones = PyList::empty_bound(py);
    for b in &info.bones {
        let e = PyDict::new_bound(py);
        e.set_item("name", &b.name)?;
        e.set_item("parent", b.parent)?;
        e.set_item("pos", b.pos.to_vec())?;
        e.set_item("quat", b.quat.to_vec())?;
        bones.append(e)?;
    }
    d.set_item("bones", bones)?;
    let seqs = PyList::empty_bound(py);
    for s in &info.seqs {
        let e = PyDict::new_bound(py);
        e.set_item("name", &s.name)?;
        e.set_item("activity", &s.activity)?;
        e.set_item("flags", s.flags)?;
        e.set_item("anim", s.anim)?;
        e.set_item("blends", s.blends)?;
        e.set_item("blend_anims", s.blend_anims.clone())?;
        e.set_item("fps", s.fps)?;
        e.set_item("frames", s.frames)?;
        e.set_item("anim_flags", s.anim_flags)?;
        seqs.append(e)?;
    }
    d.set_item("sequences", seqs)?;
    d.set_item("includes", info.includes)?;
    d.set_item("animations", info.anims)?;
    d.set_item("needs_ani", info.needs_ani)?;
    Ok(d)
}

/// mdl_sample(mdl, ani, anim) -> (fps, flags, float32 array (frames, bones, 7): position then quaternion x y z w)
#[pyfunction]
#[pyo3(signature = (mdl, ani=None, anim=0))]
fn mdl_sample<'py>(py: Python<'py>, mdl: &[u8], ani: Option<&[u8]>, anim: usize) -> PyResult<(f32, i32, Bound<'py, PyArray3<f32>>)> {
    let s = py.allow_threads(|| sa::sample(mdl, ani, anim)).map_err(err)?;
    let arr = numpy::ndarray::Array3::from_shape_vec((s.frames, s.bones, 7), s.data).map_err(err)?;
    Ok((s.fps, s.flags, arr.into_pyarray_bound(py)))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(mdl_info, m)?)?;
    m.add_function(wrap_pyfunction!(mdl_sample, m)?)?;
    Ok(())
}
