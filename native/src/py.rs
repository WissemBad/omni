//! CPython bindings of the core modules (the original entry points; py_media.rs has the audio/VTF ones).

use numpy::{IntoPyArray, PyArray2, PyArray3, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::BoundObject;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use crate::{aloc, entity, geom, lbs, skin, smd, texture};

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// A numpy array as a slice: borrowed when it is C-contiguous (no copy under the GIL), copied otherwise.
pub(crate) fn slice_of<'a, T: numpy::Element + Copy, D: numpy::ndarray::Dimension>(a: &'a numpy::PyReadonlyArray<'_, T, D>) -> std::borrow::Cow<'a, [T]> {
    match a.as_slice() {
        Ok(s) => std::borrow::Cow::Borrowed(s),
        Err(_) => std::borrow::Cow::Owned(a.as_array().iter().copied().collect()),
    }
}

#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// decode_texture(text, texd=None) -> (header dict, [(w, h, raw bytes)]) largest mip first.
#[pyfunction]
#[pyo3(signature = (text, texd=None))]
fn decode_texture<'py>(py: Python<'py>, text: &[u8], texd: Option<&[u8]>) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyList>)> {
    let (hd, mips) = py.detach(|| texture::decode_mips(text, texd)).map_err(err)?;
    let h = PyDict::new(py);
    h.set_item("width", hd.width)?;
    h.set_item("height", hd.height)?;
    h.set_item("format", texture::format_name(hd.fmt).unwrap_or("?"))?;
    h.set_item("mips", hd.mips)?;
    h.set_item("first_text_mip", hd.first_text_mip)?;
    let l = PyList::empty(py);
    for (w, hh, raw) in mips {
        l.append((w, hh, PyBytes::new(py, &raw)))?;
    }
    Ok((h, l))
}

/// to_rgba(format, w, h, data) -> uint8 array (h, w, 4).
#[pyfunction]
fn to_rgba<'py>(py: Python<'py>, format: &str, w: u32, h: u32, data: &[u8]) -> PyResult<Bound<'py, PyArray3<u8>>> {
    let px = py.detach(|| texture::to_rgba(format, w, h, data)).map_err(err)?;
    let arr = numpy::ndarray::Array3::from_shape_vec((h as usize, w as usize, 4), px).map_err(err)?;
    Ok(arr.into_pyarray(py))
}


/// parse_collision(data) -> {"kind", "layer", "shapes": [...], "warnings": [...]}; shapes are dicts with
/// "type" in convex/mesh/box/sphere/capsule (metres, prim frame).
#[pyfunction]
fn parse_collision<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyDict>> {
    let c = py.detach(|| aloc::parse(data)).map_err(err)?;
    let d = PyDict::new(py);
    d.set_item("kind", c.kind)?;
    d.set_item("layer", c.layer)?;
    d.set_item("warnings", c.warnings.clone())?;
    let shapes = PyList::empty(py);
    for s in &c.shapes {
        let e = PyDict::new(py);
        match s {
            aloc::Shape::Convex { points, pos, quat, material } => {
                e.set_item("type", "convex")?;
                e.set_item("points", points.iter().map(|p| p.to_vec()).collect::<Vec<_>>())?;
                e.set_item("pos", pos.to_vec())?;
                e.set_item("quat", quat.to_vec())?;
                e.set_item("material", *material)?;
            }
            aloc::Shape::Mesh { verts, tris } => {
                e.set_item("type", "mesh")?;
                e.set_item("verts", verts.iter().map(|p| p.to_vec()).collect::<Vec<_>>())?;
                e.set_item("tris", tris.iter().map(|t| t.to_vec()).collect::<Vec<_>>())?;
            }
            aloc::Shape::Box { half, pos, quat, material } => {
                e.set_item("type", "box")?;
                e.set_item("half", half.to_vec())?;
                e.set_item("pos", pos.to_vec())?;
                e.set_item("quat", quat.to_vec())?;
                e.set_item("material", *material)?;
            }
            aloc::Shape::Sphere { radius, pos, quat, material } => {
                e.set_item("type", "sphere")?;
                e.set_item("radius", *radius)?;
                e.set_item("pos", pos.to_vec())?;
                e.set_item("quat", quat.to_vec())?;
                e.set_item("material", *material)?;
            }
            aloc::Shape::Capsule { radius, half_height, pos, quat, material } => {
                e.set_item("type", "capsule")?;
                e.set_item("radius", *radius)?;
                e.set_item("half_height", *half_height)?;
                e.set_item("pos", pos.to_vec())?;
                e.set_item("quat", quat.to_vec())?;
                e.set_item("material", *material)?;
            }
        }
        shapes.append(e)?;
    }
    d.set_item("shapes", shapes)?;
    Ok(d)
}

/// fold_weights(bones (n, k) int64, weights (n, k) float64, max_links=3, fallback=0) -> (bones, weights)
#[pyfunction]
#[pyo3(signature = (bones, weights, max_links=3, fallback=0))]
fn fold_weights<'py>(
    py: Python<'py>,
    bones: PyReadonlyArray2<'py, i64>,
    weights: PyReadonlyArray2<'py, f64>,
    max_links: usize,
    fallback: i64,
) -> PyResult<(Bound<'py, PyArray2<i64>>, Bound<'py, PyArray2<f64>>)> {
    let (n, k) = (bones.shape()[0], bones.shape()[1]);
    if weights.shape() != [n, k] {
        return Err(err("bones and weights must have the same shape"));
    }
    let b = slice_of(&bones);
    let w = slice_of(&weights);
    let (ob, ow) = py.detach(|| skin::fold(&b, &w, n, k, max_links, fallback));
    let ob = numpy::ndarray::Array2::from_shape_vec((n, max_links), ob).map_err(err)?;
    let ow = numpy::ndarray::Array2::from_shape_vec((n, max_links), ow).map_err(err)?;
    Ok((ob.into_pyarray(py), ow.into_pyarray(py)))
}

fn ref_obj(py: Python<'_>, r: &entity::ERef) -> PyResult<Py<PyAny>> {
    Ok(PyTuple::new(py, [r.index.into_pyobject(py)?.into_any().unbind(), r.entity_id.into_pyobject(py)?.into_any().unbind(), r.exposed.clone().into_pyobject(py)?.into_any().unbind(), r.external_scene.into_pyobject(py)?.into_any().unbind()])?.into_pyobject(py)?.into_any().unbind())
}

fn value_obj(py: Python<'_>, v: &entity::Value) -> PyResult<Py<PyAny>> {
    use entity::Value::*;
    Ok(match v {
        None => py.None(),
        Floats(f) => PyTuple::new(py, f.iter().map(|x| *x as f64))?.into_pyobject(py)?.into_any().unbind(),
        F32(x) => (*x as f64).into_pyobject(py)?.into_any().unbind(),
        F64(x) => x.into_pyobject(py)?.into_any().unbind(),
        Bool(b) => b.into_pyobject(py)?.into_any().unbind(),
        Int(i) => i.into_pyobject(py)?.into_any().unbind(),
        UInt(u) => u.into_pyobject(py)?.into_any().unbind(),
        Str(s) => s.into_pyobject(py)?.into_any().unbind(),
        Refs(r) => {
            let l = PyList::empty(py);
            for x in r {
                l.append(ref_obj(py, x)?)?;
            }
            l.into_pyobject(py)?.into_any().unbind()
        }
    })
}

fn props_obj(py: Python<'_>, ps: &[entity::RawProp]) -> PyResult<Py<PyAny>> {
    let l = PyList::empty(py);
    for (pid, t, v) in ps {
        l.append((*pid, t.as_str(), value_obj(py, v)?))?;
    }
    Ok(l.into_pyobject(py)?.into_any().unbind())
}

/// parse_temp(data, refs) -> (blueprint index, [(parent ref, type ref, props, post props)], [(owner ref, (pid, type, value))])
/// refs: (index, entity id, exposed entity, external scene); props: [(CRC32 id, type name, value)].
#[pyfunction]
fn parse_temp(py: Python<'_>, data: &[u8], refs: Vec<u64>) -> PyResult<Py<PyAny>> {
    let t = py.detach(|| entity::parse_temp(data, &refs)).map_err(err)?;
    let subs = PyList::empty(py);
    for s in &t.subs {
        subs.append((ref_obj(py, &s.parent)?, s.type_ref, props_obj(py, &s.props)?, props_obj(py, &s.post)?))?;
    }
    let ov = PyList::empty(py);
    for (owner, (pid, ty, v)) in &t.overrides {
        ov.append((ref_obj(py, owner)?, (*pid, ty.as_str(), value_obj(py, v)?)))?;
    }
    Ok((t.blueprint_index, subs, ov).into_pyobject(py)?.into_any().unbind())
}

/// parse_tblu(data) -> (root, [(entity id, name, [(alias, entity index, property)])])
#[pyfunction]
fn parse_tblu(py: Python<'_>, data: &[u8]) -> PyResult<(i32, Vec<(u64, String, Vec<(String, i64, String)>)>)> {
    let (root, subs) = py.detach(|| entity::parse_tblu(data)).map_err(err)?;
    Ok((root, subs.into_iter().map(|s| (s.entity_id, s.name, s.aliases)).collect()))
}

/// meta_refs_many(paths) -> [list of reference hashes | None] for each "<path>.meta", read in parallel.
#[pyfunction]
fn meta_refs_many(py: Python<'_>, paths: Vec<String>) -> Vec<Option<Vec<u64>>> {
    use rayon::prelude::*;
    py.detach(|| {
        paths.par_iter().map(|p| std::fs::read(format!("{p}.meta")).ok().and_then(|d| entity::meta_refs(&d))).collect()
    })
}

/// template_index(temps [(hash, path)], prims [hash], max_prims) -> [(prim, template)] (see entity.rs).
#[pyfunction]
fn template_index(py: Python<'_>, temps: Vec<(u64, String)>, prims: Vec<u64>, max_prims: usize) -> Vec<(u64, u64)> {
    let set: std::collections::HashSet<u64> = prims.into_iter().collect();
    py.detach(|| entity::template_index(&temps, &set, max_prims))
}

/// lbs(v (n,3), normals (n,3), joints (n,k) int64, weights (n,k), K (nb,4,4)) -> (posed v, posed unit normals)
#[pyfunction]
#[pyo3(name = "lbs")]
fn py_lbs<'py>(
    py: Python<'py>,
    v: PyReadonlyArray2<'py, f64>,
    normals: PyReadonlyArray2<'py, f64>,
    joints: PyReadonlyArray2<'py, i64>,
    weights: PyReadonlyArray2<'py, f64>,
    k: PyReadonlyArray3<'py, f64>,
) -> PyResult<(Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<f64>>)> {
    let n = v.shape()[0];
    let nj = joints.shape()[1];
    if v.shape() != [n, 3] || normals.shape() != [n, 3] || joints.shape() != [n, nj] || weights.shape() != [n, nj] || k.shape()[1..] != [4, 4] {
        return Err(err("lbs: inconsistent shapes"));
    }
    let vv = slice_of(&v);
    let nn = slice_of(&normals);
    let jj = slice_of(&joints);
    let ww = slice_of(&weights);
    let kk = slice_of(&k);
    let (ov, on) = py.detach(|| lbs::lbs(&vv, &nn, &jj, &ww, nj, &kk));
    let ov = numpy::ndarray::Array2::from_shape_vec((n, 3), ov).map_err(err)?;
    let on = numpy::ndarray::Array2::from_shape_vec((n, 3), on).map_err(err)?;
    Ok((ov.into_pyarray(py), on.into_pyarray(py)))
}

/// smd_triangles(material, positions, normals, uvs (flipped), bones (n,l), weights (n,l), indices) -> (text, triangles)
#[pyfunction]
fn smd_triangles<'py>(
    py: Python<'py>,
    material: &str,
    positions: PyReadonlyArray2<'py, f64>,
    normals: PyReadonlyArray2<'py, f64>,
    uvs: PyReadonlyArray2<'py, f64>,
    bones: PyReadonlyArray2<'py, i64>,
    weights: PyReadonlyArray2<'py, f64>,
    indices: Vec<u32>,
) -> PyResult<(String, usize)> {
    let f = |a: &PyReadonlyArray2<'py, f64>| a.as_array().iter().copied().collect::<Vec<f64>>();
    let (p, n, u, w) = (f(&positions), f(&normals), f(&uvs), f(&weights));
    let b = slice_of(&bones);
    let links = bones.shape()[1];
    py.detach(|| smd::triangles(material, &smd::Mesh { pos: &p, nrm: &n, uv: &u, bones: &b, weights: &w, links, indices: &indices }))
        .map_err(err)
}

/// smd_static(material, positions (n,3), normals (n,3), uvs (n,2, V flipped), indices int64) -> (text, triangles)
#[pyfunction]
fn smd_static<'py>(
    py: Python<'py>,
    material: &str,
    positions: PyReadonlyArray2<'py, f64>,
    normals: PyReadonlyArray2<'py, f64>,
    uvs: PyReadonlyArray2<'py, f64>,
    indices: numpy::PyReadonlyArray1<'py, i64>,
) -> PyResult<(String, usize)> {
    let (p, n, u, i) = (positions.as_slice()?, normals.as_slice()?, uvs.as_slice()?, indices.as_slice()?);
    py.detach(|| smd::static_triangles(material, p, n, u, i)).map_err(err)
}

/// weld(positions (n,3), triangles (m,3) int64, cell=1e-4) -> (vertices, triangles): merged seams, no degenerate triangles
#[pyfunction]
#[pyo3(signature = (positions, triangles, cell=1e-4))]
fn weld<'py>(
    py: Python<'py>,
    positions: PyReadonlyArray2<'py, f64>,
    triangles: PyReadonlyArray2<'py, i64>,
    cell: f64,
) -> PyResult<(Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<i64>>)> {
    let p = slice_of(&positions);
    let t = slice_of(&triangles);
    let (v, tt) = py.detach(|| geom::weld(&p, &t, cell)).map_err(err)?;
    let (nv, nt) = (v.len() / 3, tt.len() / 3);
    Ok((
        numpy::ndarray::Array2::from_shape_vec((nv, 3), v).map_err(err)?.into_pyarray(py),
        numpy::ndarray::Array2::from_shape_vec((nt, 3), tt).map_err(err)?.into_pyarray(py),
    ))
}

/// mesh_volume(vertices, triangles) -> (|signed volume|, fraction of open edges)
#[pyfunction]
fn mesh_volume<'py>(py: Python<'py>, vertices: PyReadonlyArray2<'py, f64>, triangles: PyReadonlyArray2<'py, i64>) -> PyResult<(f64, f64)> {
    let v = slice_of(&vertices);
    let t = slice_of(&triangles);
    py.detach(|| geom::mesh_volume(&v, &t)).map_err(err)
}

#[pymodule]
pub fn omni_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(decode_texture, m)?)?;
    m.add_function(wrap_pyfunction!(to_rgba, m)?)?;
    m.add_function(wrap_pyfunction!(parse_collision, m)?)?;
    m.add_function(wrap_pyfunction!(fold_weights, m)?)?;
    m.add_function(wrap_pyfunction!(parse_temp, m)?)?;
    m.add_function(wrap_pyfunction!(parse_tblu, m)?)?;
    m.add_function(wrap_pyfunction!(meta_refs_many, m)?)?;
    m.add_function(wrap_pyfunction!(template_index, m)?)?;
    m.add_function(wrap_pyfunction!(py_lbs, m)?)?;
    m.add_function(wrap_pyfunction!(smd_triangles, m)?)?;
    m.add_function(wrap_pyfunction!(smd_static, m)?)?;
    m.add_function(wrap_pyfunction!(weld, m)?)?;
    m.add_function(wrap_pyfunction!(mesh_volume, m)?)?;
    crate::py_media::register(m)?;
    crate::py_eta::register(m)?;
    crate::py_gltf::register(m)?;
    crate::py_anim::register(m)?;
    crate::py_unreal::register(m)?;
    crate::py_glacier::register(m)?;
    Ok(())
}
