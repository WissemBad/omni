//! Python bindings of the audio and Source-texture modules (registered by lib.rs).

use crate::{audio, texture, vtf, wwise};
use numpy::{IntoPyArray, PyArray3, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn info_dict<'py>(py: Python<'py>, i: &audio::WemInfo) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("codec", i.codec)?;
    d.set_item("channels", i.channels)?;
    d.set_item("rate", i.rate)?;
    d.set_item("samples", i.samples)?;
    d.set_item("label", &i.label)?;
    d.set_item("data_size", i.data_size)?;
    d.set_item("block_align", i.block_align)?;
    Ok(d)
}

/// wem_info(data) -> {codec, channels, rate, samples, label, data_size, block_align} | None
#[pyfunction]
fn wem_info<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Option<Bound<'py, PyDict>>> {
    audio::wem_info(data).map(|i| info_dict(py, &i)).transpose()
}

/// convert_wem(data, fmt="auto", tags=[]) -> (extension, file bytes, info). fmt: auto | flac | wav | pcm.
#[pyfunction]
#[pyo3(signature = (data, fmt="auto", tags=vec![]))]
fn convert_wem<'py>(py: Python<'py>, data: &[u8], fmt: &str, tags: Vec<(String, String)>) -> PyResult<(String, Bound<'py, PyBytes>, Bound<'py, PyDict>)> {
    let c = py.allow_threads(|| audio::convert(data, fmt, &tags)).map_err(err)?;
    Ok((c.ext.to_string(), PyBytes::new_bound(py, &c.bytes), info_dict(py, &c.info)?))
}

fn pool(threads: usize) -> PyResult<rayon::ThreadPool> {
    rayon::ThreadPoolBuilder::new().num_threads(threads).build().map_err(err)
}

/// scan_media([(file, offset, size)], threads=0) -> [(sha1, info | None, error)] (one read per media, parallel)
#[pyfunction]
#[pyo3(signature = (items, threads=0))]
fn scan_media(py: Python<'_>, items: Vec<(String, u64, i64)>, threads: usize) -> PyResult<Vec<(String, Option<PyObject>, String)>> {
    let p = pool(threads)?;
    let res = py.allow_threads(|| p.install(|| audio::scan(&items)));
    res.into_iter()
        .map(|s| {
            let info = match s.info {
                Some(i) => Some(info_dict(py, &i)?.into_py(py)),
                None => None,
            };
            Ok((s.sha1, info, s.error))
        })
        .collect()
}

/// export_media([(file, offset, size, out_base, [(tag, value)])], fmt="auto", threads=0)
///   -> [{path, skipped, codec, channels, rate, samples, label, bytes, error}]
#[pyfunction]
#[pyo3(signature = (jobs, fmt="auto", threads=0))]
fn export_media(py: Python<'_>, jobs: Vec<(String, u64, i64, String, Vec<(String, String)>)>, fmt: &str, threads: usize) -> PyResult<Vec<PyObject>> {
    let jobs: Vec<audio::Job> = jobs.into_iter().map(|(file, offset, size, out_base, tags)| audio::Job { file, offset, size, out_base, tags }).collect();
    let p = pool(threads)?;
    let fmt = fmt.to_string();
    let res = py.allow_threads(|| p.install(|| audio::export(&jobs, &fmt)));
    res.iter()
        .map(|r| {
            let d = match &r.info {
                Some(i) => info_dict(py, i)?,
                None => PyDict::new_bound(py),
            };
            d.set_item("path", &r.path)?;
            d.set_item("skipped", r.skipped)?;
            d.set_item("bytes", r.bytes)?;
            d.set_item("error", &r.error)?;
            Ok(d.into_py(py))
        })
        .collect()
}

type Links = (Vec<(u64, u32)>, Vec<(u64, Vec<u32>)>, Vec<(u32, Vec<(u32, Vec<u32>)>)>, Vec<u32>, usize);

/// bank_links([(resource hash, bank bytes)], known switch/state ids)
///   -> ([(hash, bank id)], [(hash, [embedded media])], [(media, [(event id, [switch ids])])], [sourced media], events)
#[pyfunction]
fn bank_links(py: Python<'_>, banks: Vec<(u64, Vec<u8>)>, known: Vec<u32>) -> PyResult<Links> {
    Ok(py.allow_threads(|| {
        let parsed: Vec<(u64, wwise::Bank)> = banks.iter().filter_map(|(h, d)| wwise::parse_bank(d).map(|b| (*h, b))).collect();
        let known: std::collections::HashSet<u32> = known.into_iter().collect();
        let ids = parsed.iter().map(|(h, b)| (*h, b.bank_id)).collect();
        let media = parsed.iter().map(|(h, b)| (*h, b.media.clone())).collect();
        let only: Vec<wwise::Bank> = parsed.into_iter().map(|(_, b)| b).collect();
        let l = wwise::link(&only, &known);
        (ids, media, l.media.into_iter().collect(), l.sourced.into_iter().collect(), l.events)
    }))
}

fn rgba_slice<'a>(a: &'a PyReadonlyArray3<'_, u8>, owned: &'a mut Vec<u8>) -> PyResult<(&'a [u8], usize, usize)> {
    let shape = a.shape();
    if shape[2] != 4 {
        return Err(err("expected (h, w, 4) RGBA"));
    }
    let (h, w) = (shape[0], shape[1]);
    let s: &[u8] = match a.as_slice() {
        Ok(s) => s,
        Err(_) => {
            *owned = a.as_array().iter().copied().collect();
            owned
        }
    };
    Ok((s, w, h))
}

/// encode_vtf(path, rgba (h,w,4), format="dxt1", kind="srgb", max_size=0, flags=0, quality=1, coverage=0.0)
///   -> (width, height) written. Builds the full mip chain (see vtf.rs) and writes the file atomically.
#[pyfunction]
#[pyo3(signature = (path, rgba, format="dxt1", kind="srgb", max_size=0, flags=0, quality=1, coverage=0.0))]
#[allow(clippy::too_many_arguments)]
fn encode_vtf<'py>(py: Python<'py>, path: &str, rgba: PyReadonlyArray3<'py, u8>, format: &str, kind: &str, max_size: usize, flags: u32, quality: u8, coverage: f32) -> PyResult<(usize, usize)> {
    let k = vtf::Kind::parse(kind).map_err(err)?;
    let mut owned = Vec::new();
    let (data, w, h) = rgba_slice(&rgba, &mut owned)?;
    py.allow_threads(|| vtf::encode_vtf(std::path::Path::new(path), data, w, h, format, k, max_size, flags, quality, coverage)).map_err(err)
}

/// write_vtf(path, format code, [(w, h, bytes)] largest first, flags=0, reflectivity=(.5,.5,.5)): raw blocks
#[pyfunction]
#[pyo3(signature = (path, fmt, mips, flags=0, reflectivity=(0.5, 0.5, 0.5)))]
fn write_vtf(py: Python<'_>, path: &str, fmt: u32, mips: Vec<(usize, usize, Vec<u8>)>, flags: u32, reflectivity: (f32, f32, f32)) -> PyResult<()> {
    if mips.is_empty() {
        return Err(err("no mip"));
    }
    py.allow_threads(|| vtf::write_vtf(std::path::Path::new(path), fmt, &mips, flags, [reflectivity.0, reflectivity.1, reflectivity.2])).map_err(err)
}

/// decode_vtf(bytes, max_dim=1024) -> uint8 (h, w, 4)
#[pyfunction]
#[pyo3(signature = (data, max_dim=1024))]
fn decode_vtf<'py>(py: Python<'py>, data: &[u8], max_dim: usize) -> PyResult<Bound<'py, PyArray3<u8>>> {
    let (w, h, px) = py.allow_threads(|| vtf::decode_vtf(data, max_dim)).map_err(err)?;
    Ok(numpy::ndarray::Array3::from_shape_vec((h, w, 4), px).map_err(err)?.into_pyarray_bound(py))
}

/// png_rgba(rgba (h,w,4), channel="rgb", max_dim=0) -> PNG bytes
#[pyfunction]
#[pyo3(signature = (rgba, channel="rgb", max_dim=0))]
fn png_rgba<'py>(py: Python<'py>, rgba: PyReadonlyArray3<'py, u8>, channel: &str, max_dim: usize) -> PyResult<Bound<'py, PyBytes>> {
    let mut owned = Vec::new();
    let (data, w, h) = rgba_slice(&rgba, &mut owned)?;
    let out = py.allow_threads(|| vtf::png(data, w, h, channel, max_dim)).map_err(err)?;
    Ok(PyBytes::new_bound(py, &out))
}

/// vtf_png(vtf bytes, channel="rgb", max_dim=1024) -> PNG bytes
#[pyfunction]
#[pyo3(signature = (data, channel="rgb", max_dim=1024))]
fn vtf_png<'py>(py: Python<'py>, data: &[u8], channel: &str, max_dim: usize) -> PyResult<Bound<'py, PyBytes>> {
    let out = py
        .allow_threads(|| {
            let (w, h, px) = vtf::decode_vtf(data, max_dim)?;
            vtf::png(&px, w, h, channel, 0)
        })
        .map_err(err)?;
    Ok(PyBytes::new_bound(py, &out))
}

/// texture_rgba(text, texd=None, max_dim=0, normal=False) -> uint8 (h, w, 4): the largest game mip that fits
/// (BC5/BC4/RG8 normals get their Z rebuilt when `normal`).
#[pyfunction]
#[pyo3(signature = (text, texd=None, max_dim=0, normal=false))]
fn texture_rgba<'py>(py: Python<'py>, text: &[u8], texd: Option<&[u8]>, max_dim: usize, normal: bool) -> PyResult<Bound<'py, PyArray3<u8>>> {
    let (w, h, px) = py.allow_threads(|| texture::top_rgba(text, texd, max_dim, normal)).map_err(err)?;
    Ok(numpy::ndarray::Array3::from_shape_vec((h, w, 4), px).map_err(err)?.into_pyarray_bound(py))
}

/// texture_png(text, texd=None, channel="rgb", max_dim=1024, normal=False) -> (PNG bytes, (w, h) shown)
#[pyfunction]
#[pyo3(signature = (text, texd=None, channel="rgb", max_dim=1024, normal=false))]
fn texture_png<'py>(py: Python<'py>, text: &[u8], texd: Option<&[u8]>, channel: &str, max_dim: usize, normal: bool) -> PyResult<(Bound<'py, PyBytes>, (usize, usize))> {
    let (png, wh) = py
        .allow_threads(|| -> Result<(Vec<u8>, (usize, usize)), String> {
            let (w, h, px) = texture::top_rgba(text, texd, max_dim, normal)?;
            Ok((vtf::png(&px, w, h, channel, max_dim)?, (w, h)))
        })
        .map_err(err)?;
    Ok((PyBytes::new_bound(py, &png), wh))
}

/// texture_header(text) -> {width, height, format, mips, first_text_mip} (header only)
#[pyfunction]
fn texture_header<'py>(py: Python<'py>, text: &[u8]) -> PyResult<Bound<'py, PyDict>> {
    let hd = texture::parse_header(text).map_err(err)?;
    let h = PyDict::new_bound(py);
    h.set_item("width", hd.width)?;
    h.set_item("height", hd.height)?;
    h.set_item("format", texture::format_name(hd.fmt).unwrap_or("?"))?;
    h.set_item("mips", hd.mips)?;
    h.set_item("first_text_mip", hd.first_text_mip)?;
    Ok(h)
}

/// texture_headers([path]) -> [(width, height, format, mips) | None], read in parallel (texture catalog)
#[pyfunction]
fn texture_headers(py: Python<'_>, paths: Vec<String>) -> Vec<Option<(u32, u32, String, u32)>> {
    use rayon::prelude::*;
    py.allow_threads(|| {
        paths
            .par_iter()
            .map(|p| {
                let mut f = std::fs::File::open(p).ok()?;
                let mut b = vec![0u8; 0x98];
                std::io::Read::read_exact(&mut f, &mut b).ok()?;
                let hd = texture::parse_header(&b).ok()?;
                Some((hd.width, hd.height, texture::format_name(hd.fmt).unwrap_or("?").to_string(), hd.mips))
            })
            .collect()
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    for f in [
        wrap_pyfunction!(wem_info, m)?,
        wrap_pyfunction!(convert_wem, m)?,
        wrap_pyfunction!(scan_media, m)?,
        wrap_pyfunction!(export_media, m)?,
        wrap_pyfunction!(bank_links, m)?,
        wrap_pyfunction!(encode_vtf, m)?,
        wrap_pyfunction!(write_vtf, m)?,
        wrap_pyfunction!(decode_vtf, m)?,
        wrap_pyfunction!(png_rgba, m)?,
        wrap_pyfunction!(vtf_png, m)?,
        wrap_pyfunction!(texture_rgba, m)?,
        wrap_pyfunction!(texture_png, m)?,
        wrap_pyfunction!(texture_header, m)?,
        wrap_pyfunction!(texture_headers, m)?,
    ] {
        m.add_function(f)?;
    }
    Ok(())
}
