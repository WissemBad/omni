//! CPython bindings of the glTF writer (see `gltf.rs`): a `Glb` builder fed with numpy arrays.
//!
//! Textures are encoded without the GIL, so threads that export different models scale on the cores.

use std::path::PathBuf;

use numpy::{PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::gltf::{self, Frame, MaterialSpec, Prim};
use crate::py::slice_of;

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// Glb(frame="facing_z"): ``frame`` is "facing_z" (export) or "viewer" (web preview).
#[pyclass]
pub struct Glb {
    inner: gltf::Glb,
}

fn array<T: numpy::Element + Copy>(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<Vec<T>>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => {
            let a: PyReadonlyArray2<'_, T> = v.extract()?;
            Ok(Some(slice_of(&a).into_owned()))
        }
        _ => Ok(None),
    }
}

#[pymethods]
impl Glb {
    #[new]
    #[pyo3(signature = (frame="facing_z"))]
    fn new(frame: &str) -> PyResult<Self> {
        let f = Frame::parse(frame).ok_or_else(|| err(format!("unknown frame {frame:?}")))?;
        Ok(Glb { inner: gltf::Glb::new(f) })
    }

    /// texture(rgba (h, w, 4) uint8, max_size=0, jpeg=0) -> texture index. ``jpeg`` is a quality (1-100) for opaque
    /// colour maps, 0 keeps PNG; ``max_size`` shrinks the longest side.
    #[pyo3(signature = (rgba, max_size=0, jpeg=0))]
    fn texture(&mut self, py: Python<'_>, rgba: PyReadonlyArray3<'_, u8>, max_size: usize, jpeg: u8) -> PyResult<usize> {
        let s = rgba.shape();
        if s[2] != 4 {
            return Err(err("expected (h, w, 4) RGBA"));
        }
        let (h, w) = (s[0], s[1]);
        let px = slice_of(&rgba);
        let (data, mime) = py.detach(|| gltf::encode_image(&px, w, h, max_size, jpeg)).map_err(err)?;
        self.inner.add_encoded(&data, mime).map_err(err)
    }

    /// material(name, base=None, normal=None, metal_rough=None, occlusion=None, alpha="OPAQUE", cutoff=0.5,
    /// double_sided=True, metallic=1.0, roughness=1.0, emissive=None) -> material index (textures are indices of
    /// `texture`).
    #[pyo3(signature = (name, base=None, normal=None, metal_rough=None, occlusion=None, alpha="OPAQUE", cutoff=0.5, double_sided=true, metallic=1.0, roughness=1.0, emissive=None))]
    #[allow(clippy::too_many_arguments)]
    fn material(&mut self, name: &str, base: Option<usize>, normal: Option<usize>, metal_rough: Option<usize>, occlusion: Option<usize>, alpha: &str, cutoff: f64, double_sided: bool, metallic: f64, roughness: f64, emissive: Option<usize>) -> PyResult<usize> {
        self.inner
            .add_material(&MaterialSpec { name: name.into(), base, normal, metal_rough, occlusion, emissive, alpha: alpha.into(), cutoff, double_sided, metallic, roughness })
            .map_err(err)
    }

    /// mesh(name, primitives) -> mesh index. A primitive is a dict: positions (n,3) float32, normals (n,3), uvs (n,2),
    /// indices (m,) uint32 and optionally tangents (n,4), joints (n,4) uint16, weights (n,4) float32, material.
    fn mesh(&mut self, name: &str, primitives: Vec<Bound<'_, PyDict>>) -> PyResult<usize> {
        let mut owned: Vec<[Option<Vec<f32>>; 5]> = Vec::new();
        let mut idx: Vec<Vec<u32>> = Vec::new();
        let mut joints: Vec<Option<Vec<u16>>> = Vec::new();
        let mut mats: Vec<Option<usize>> = Vec::new();
        for p in &primitives {
            let get = |k: &str| array::<f32>(p, k);
            let need = |k: &str| get(k)?.ok_or_else(|| err(format!("primitive without {k}")));
            owned.push([Some(need("positions")?), Some(need("normals")?), Some(need("uvs")?), get("tangents")?, get("weights")?]);
            let i = p.get_item("indices")?.ok_or_else(|| err("primitive without indices"))?;
            let i: numpy::PyReadonlyArray1<'_, u32> = i.extract()?;
            idx.push(slice_of(&i).into_owned());
            joints.push(match p.get_item("joints")? {
                Some(v) if !v.is_none() => {
                    let j: PyReadonlyArray2<'_, u16> = v.extract()?;
                    Some(slice_of(&j).into_owned())
                }
                _ => None,
            });
            mats.push(match p.get_item("material")? {
                Some(v) if !v.is_none() => Some(v.extract()?),
                _ => None,
            });
        }
        let prims: Vec<Prim> = (0..primitives.len())
            .map(|k| Prim {
                positions: owned[k][0].as_deref().unwrap_or(&[]),
                normals: owned[k][1].as_deref().unwrap_or(&[]),
                uvs: owned[k][2].as_deref().unwrap_or(&[]),
                indices: &idx[k],
                tangents: owned[k][3].as_deref(),
                joints: joints[k].as_deref(),
                weights: owned[k][4].as_deref(),
                material: mats[k],
            })
            .collect();
        self.inner.add_mesh(name, &prims).map_err(err)
    }

    /// skeleton(names, parents, world (n,4,4) float64) -> skin index; joint nodes are added to the scene.
    fn skeleton(&mut self, names: Vec<String>, parents: Vec<i64>, world: PyReadonlyArray3<'_, f64>) -> PyResult<usize> {
        if world.shape()[1..] != [4, 4] {
            return Err(err("world must be (n, 4, 4)"));
        }
        let w = slice_of(&world);
        self.inner.add_skeleton(&names, &parents, &w).map_err(err)
    }

    #[pyo3(signature = (name, mesh=None, skin=None))]
    fn node(&mut self, name: &str, mesh: Option<usize>, skin: Option<usize>) -> PyResult<usize> {
        self.inner.add_node(name, mesh, skin).map_err(err)
    }

    fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, &self.inner.finish("omni").map_err(err)?))
    }

    /// Write the file atomically (temporary file, then rename), creating the folders.
    fn save(&self, py: Python<'_>, path: PathBuf) -> PyResult<()> {
        let inner = &self.inner;
        py.detach(|| {
            let bytes = inner.finish("omni")?;
            if let Some(d) = path.parent() {
                std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
            }
            let mut tmp = path.clone().into_os_string();
            tmp.push(".part");
            let tmp = PathBuf::from(tmp);
            std::fs::write(&tmp, bytes).map_err(|e| e.to_string())?;
            std::fs::rename(&tmp, &path).map_err(|e| {
                let _ = std::fs::remove_file(&tmp);
                e.to_string()
            })
        })
        .map_err(err)
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Glb>()?;
    Ok(())
}
