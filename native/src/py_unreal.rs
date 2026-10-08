//! CPython bindings of the Unreal reader: `UnrealGame` opens a game's containers once (thread-safe, the GIL is
//! released while reading) and hands out assets as plain Python values and numpy arrays.

use std::path::Path;
use std::sync::Arc;

use numpy::IntoPyArray;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::BoundObject;
use pyo3::types::{PyBytes, PyDict, PyList};

use crate::unreal::props::{Ctx, Value};
use crate::unreal::reflect::Schema;
use crate::unreal::zen::{obj_kind, ObjKind, Package};
use crate::unreal::{assets, main_export, mesh, oodle, reflect, skel, Game};

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// oodle_load(path) -> loads oo2core_9_win64.dll (needed by most UE5 games).
#[pyfunction]
fn oodle_load(path: &str) -> PyResult<bool> {
    oodle::set_library(path).map_err(err)?;
    Ok(oodle::available())
}

/// unreal_mappings(exe) -> text of the property layouts read from the game's executable.
#[pyfunction]
fn unreal_mappings(py: Python<'_>, exe: &str) -> PyResult<(String, usize, usize)> {
    let exe = exe.to_string();
    py.detach(move || {
        let data = std::fs::read(&exe).map_err(err)?;
        let s = reflect::extract(&data).map_err(err)?;
        Ok((s.to_text(), s.structs.len(), s.enums.len()))
    })
}

/// unreal_usmap(path) -> the same text, from community mappings (`.usmap`; Oodle must be loaded if compressed).
#[pyfunction]
fn unreal_usmap(py: Python<'_>, path: &str) -> PyResult<(String, usize, usize)> {
    let path = path.to_string();
    py.detach(move || {
        let data = std::fs::read(&path).map_err(err)?;
        let s = reflect::from_usmap(&data).map_err(err)?;
        Ok((s.to_text(), s.structs.len(), s.enums.len()))
    })
}

#[pyclass(frozen)]
pub struct UnrealGame {
    game: Arc<Game>,
}

impl UnrealGame {
    fn ctx<'a>(&'a self, pkg: &'a Package) -> Ctx<'a> {
        Ctx { schema: &self.game.schema, names: &pkg.names, ue5: self.game.ue5_version }
    }

    fn open_export(&self, path: &str, class: Option<&str>) -> PyResult<(Package, usize, String)> {
        let pkg = self.game.package(path).map_err(err)?;
        let want = class.map(|c| c.to_string());
        let idx = match &want {
            Some(c) => pkg
                .exports
                .iter()
                .position(|e| e.flags & 0x10 == 0 && &self.game.class_name(&pkg, e.class) == c)
                .or_else(|| main_export(&pkg)),
            None => main_export(&pkg),
        }
        .ok_or_else(|| err(format!("{path}: no export")))?;
        let cls = self.game.class_name(&pkg, pkg.exports[idx].class);
        Ok((pkg, idx, cls))
    }
}

/// FPackageIndex of `pkg` -> "package/path.Object" (imports of other packages give their package path).
fn object_ref(game: &Game, pkg: &Package, i: i32) -> Option<String> {
    if i == 0 {
        return None;
    }
    if i > 0 {
        let e = pkg.exports.get(i as usize - 1)?;
        return Some(format!("{}.{}", pkg.name, e.name));
    }
    let raw = *pkg.imports.get((-i - 1) as usize)?;
    match obj_kind(raw) {
        ObjKind::Script(_) => game.resolve(pkg, raw).map(|(m, n)| format!("{m}.{n}")),
        ObjKind::Package(pi, _) => {
            let pid = game.package_id_of(&pkg.name)?;
            let imported = game.imported_packages(pkg, pid);
            let target = imported.get(pi as usize)?;
            let p = game.package_paths.get(target)?;
            Some(p.trim_end_matches(".uasset").trim_end_matches(".umap").to_string())
        }
        _ => None,
    }
}

fn value_py(py: Python<'_>, v: &Value, game: &Game, pkg: &Package) -> PyResult<Py<PyAny>> {
    Ok(match v {
        Value::Bool(b) => b.into_pyobject(py)?.into_any().unbind(),
        Value::Int(i) => i.into_pyobject(py)?.into_any().unbind(),
        Value::Float(f) => f.into_pyobject(py)?.into_any().unbind(),
        Value::Str(s) | Value::Name(s) | Value::Enum(s) => s.into_pyobject(py)?.into_any().unbind(),
        Value::Object(i) => object_ref(game, pkg, *i).into_pyobject(py)?.into_any().unbind(),
        Value::SoftObject(p, s) => if s.is_empty() { p.clone() } else { format!("{p}:{s}") }.into_pyobject(py)?.into_any().unbind(),
        Value::Floats(f) => f.clone().into_pyobject(py)?.into_any().unbind(),
        Value::Struct(_, fields) => props_py(py, fields, game, pkg)?.into_pyobject(py)?.into_any().unbind(),
        Value::Array(a) => {
            let l = PyList::empty(py);
            for x in a {
                l.append(value_py(py, x, game, pkg)?)?;
            }
            l.into_pyobject(py)?.into_any().unbind()
        }
        Value::Map(m) => {
            let l = PyList::empty(py);
            for (k, x) in m {
                l.append((value_py(py, k, game, pkg)?, value_py(py, x, game, pkg)?))?;
            }
            l.into_pyobject(py)?.into_any().unbind()
        }
        Value::Unknown => py.None(),
    })
}

fn props_py<'py>(py: Python<'py>, props: &[(String, Value)], game: &Game, pkg: &Package) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    for (k, v) in props {
        d.set_item(k, value_py(py, v, game, pkg)?)?;
    }
    Ok(d)
}

fn lod_py<'py>(py: Python<'py>, l: mesh::Lod) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let secs = PyList::empty(py);
    for s in &l.sections {
        let sd = PyDict::new(py);
        sd.set_item("material", s.material)?;
        sd.set_item("first_index", s.first_index)?;
        sd.set_item("num_triangles", s.num_triangles)?;
        sd.set_item("base_vertex", s.base_vertex)?;
        sd.set_item("num_vertices", s.num_vertices)?;
        sd.set_item("bone_map", s.bone_map.clone())?;
        secs.append(sd)?;
    }
    d.set_item("sections", secs)?;
    d.set_item("positions", l.positions.into_pyarray(py))?;
    d.set_item("normals", l.normals.into_pyarray(py))?;
    d.set_item("tangents", l.tangents.into_pyarray(py))?;
    let uvs = PyList::empty(py);
    for u in l.uvs {
        uvs.append(u.into_pyarray(py))?;
    }
    d.set_item("uvs", uvs)?;
    d.set_item("colors", l.colors.into_pyarray(py))?;
    d.set_item("indices", l.indices.into_pyarray(py))?;
    d.set_item("influences", l.influences)?;
    d.set_item("bone_indices", l.bone_indices.into_pyarray(py))?;
    d.set_item("bone_weights", l.bone_weights.into_pyarray(py))?;
    Ok(d)
}

#[pymethods]
impl UnrealGame {
    /// UnrealGame(paks, engine="5.1", mappings="", aes_key="") — `mappings`: text from unreal_mappings().
    #[new]
    #[pyo3(signature = (paks, engine="5.1", mappings="", aes_key=""))]
    fn new(py: Python<'_>, paks: &str, engine: &str, mappings: &str, aes_key: &str) -> PyResult<Self> {
        let key = if aes_key.is_empty() { None } else { Some(crate::unreal::aes::parse_key(aes_key).ok_or_else(|| err("invalid AES key"))?) };
        let (paks, engine, mappings) = (paks.to_string(), engine.to_string(), mappings.to_string());
        let game = py.detach(move || -> Result<Game, String> {
            let mut g = Game::open(Path::new(&paks), &engine, key).map_err(|e| e.to_string())?;
            if !mappings.is_empty() {
                g.schema = Schema::from_text(&mappings).map_err(|e| e.to_string())?;
            }
            Ok(g)
        }).map_err(err)?;
        Ok(UnrealGame { game: Arc::new(game) })
    }

    #[getter]
    fn engine(&self) -> String {
        self.game.engine.clone()
    }

    #[getter]
    fn package_count(&self) -> usize {
        self.game.packages.len()
    }

    /// package_id(path) -> the 64-bit id of a package (omni's key), None when it is not in the game.
    fn package_id(&self, path: &str) -> Option<u64> {
        self.game.package_id_of(path)
    }

    /// package_path(id) -> game path of a package without extension.
    fn package_path(&self, id: u64) -> Option<String> {
        self.game.package_paths.get(&id).map(|p| p.trim_end_matches(".uasset").trim_end_matches(".umap").to_string())
    }

    /// texture_infos(paths) -> [(format, width, height, mip count, bytes) | None] read in parallel (no pixel data).
    fn texture_infos(&self, py: Python<'_>, paths: Vec<String>) -> Vec<Option<(String, u32, u32, usize, u64)>> {
        let game = self.game.clone();
        py.detach(move || {
            use crate::par::*;
            paths
                .par_iter()
                .map(|p| {
                    let pkg = game.package(p).ok()?;
                    let idx = main_export(&pkg)?;
                    let cls = game.class_name(&pkg, pkg.exports[idx].class);
                    let ctx = Ctx { schema: &game.schema, names: &pkg.names, ue5: game.ue5_version };
                    let t = assets::read_texture_opt(&game, &ctx, &pkg, idx, &cls, false).ok()?;
                    let bytes: u64 = t.mips.iter().map(|m| crate::texture::mip_nbytes(assets::omni_format(&t.format), m.width, m.height) as u64).sum();
                    Some((t.format, t.width, t.height, t.mips.len(), bytes))
                })
                .collect()
        })
    }

    /// Every game path of the containers (original case, with extension).
    fn files(&self) -> Vec<String> {
        self.game.files.iter().map(|f| f.0.clone()).collect()
    }

    /// index(classes) -> [(package path, export name, class, export size)] for the exports of those classes
    /// (class default objects excluded). Packages are read in parallel.
    fn index(&self, py: Python<'_>, classes: Vec<String>) -> PyResult<Vec<(String, String, String, u64)>> {
        let game = self.game.clone();
        py.detach(move || {
            use crate::par::*;
            let mut paths: Vec<String> = game.package_paths.values().cloned().collect();
            paths.sort();
            let rows: Vec<Vec<(String, String, String, u64)>> = paths
                .par_iter()
                .map(|p| {
                    let stem = p.trim_end_matches(".uasset").trim_end_matches(".umap");
                    let mut out = Vec::new();
                    if let Ok(pkg) = game.package(stem) {
                        for e in &pkg.exports {
                            if e.flags & 0x10 != 0 {
                                continue;
                            }
                            let c = game.class_name(&pkg, e.class);
                            if classes.iter().any(|x| x == &c) {
                                out.push((stem.to_string(), e.name.clone(), c, e.serial_size));
                            }
                        }
                    }
                    out
                })
                .collect();
            Ok(rows.into_iter().flatten().collect())
        })
    }

    /// exports(path) -> [(name, class, outer index, size)]
    fn exports(&self, path: &str) -> PyResult<Vec<(String, String, i64, u64)>> {
        let pkg = self.game.package(path).map_err(err)?;
        Ok(pkg
            .exports
            .iter()
            .map(|e| {
                let outer = match obj_kind(e.outer) {
                    ObjKind::Export(i) => i as i64,
                    _ => -1,
                };
                (e.name.clone(), self.game.class_name(&pkg, e.class), outer, e.serial_size)
            })
            .collect())
    }

    /// imports(path) -> referenced package paths / script objects.
    fn imports(&self, path: &str) -> PyResult<Vec<String>> {
        let pkg = self.game.package(path).map_err(err)?;
        Ok((0..pkg.imports.len()).filter_map(|i| object_ref(&self.game, &pkg, -(i as i32) - 1)).collect())
    }

    /// properties(path, class=None) -> (class, {name: value}) of the main export (or the first export of `class`).
    #[pyo3(signature = (path, class=None))]
    fn properties<'py>(&self, py: Python<'py>, path: &str, class: Option<&str>) -> PyResult<(String, Bound<'py, PyDict>)> {
        let (pkg, idx, cls) = self.open_export(path, class)?;
        let ctx = self.ctx(&pkg);
        let e = &pkg.exports[idx];
        let (props, _) = py.detach(|| crate::unreal::props::read_object(&ctx, &cls, pkg.export_data(idx), e.flags & 0x10 != 0)).map_err(err)?;
        Ok((cls, props_py(py, &props, &self.game, &pkg)?))
    }

    /// texture(path, mip_limit=0) -> {format (omni name), source_format, width, height, mips: [(w, h, bytes)], props}
    /// Mips are largest first; `mip_limit` > 0 drops mips larger than that size when smaller ones exist.
    #[pyo3(signature = (path, max_dim=0))]
    fn texture<'py>(&self, py: Python<'py>, path: &str, max_dim: u32) -> PyResult<Bound<'py, PyDict>> {
        let (pkg, idx, cls) = self.open_export(path, None)?;
        let ctx = self.ctx(&pkg);
        let game = &self.game;
        let (tx, mips) = py
            .detach(|| -> Result<_, String> {
                let tx = assets::read_texture(game, &ctx, &pkg, idx, &cls).map_err(|e| e.to_string())?;
                let mut out = Vec::new();
                for m in &tx.mips {
                    let Some(d) = &m.data else { continue };
                    if max_dim > 0 && m.width.max(m.height) > max_dim && tx.mips.iter().any(|x| x.data.is_some() && x.width.max(x.height) <= max_dim) {
                        continue;
                    }
                    let conv = assets::convert_mip(&tx.format, m.width, m.height, d).map_err(|e| e.to_string())?;
                    out.push((m.width, m.height, conv));
                }
                Ok((tx, out))
            })
            .map_err(err)?;
        let d = PyDict::new(py);
        d.set_item("format", assets::omni_format(&tx.format))?;
        d.set_item("source_format", &tx.format)?;
        d.set_item("width", tx.width)?;
        d.set_item("height", tx.height)?;
        d.set_item("slices", tx.slices)?;
        let l = PyList::empty(py);
        for (w, h, b) in mips {
            l.append((w, h, PyBytes::new(py, &b)))?;
        }
        d.set_item("mips", l)?;
        d.set_item("props", props_py(py, &tx.props, &self.game, &pkg)?)?;
        Ok(d)
    }

    /// sound(path) -> (format, bytes, props)
    fn sound<'py>(&self, py: Python<'py>, path: &str) -> PyResult<(String, Bound<'py, PyBytes>, Bound<'py, PyDict>)> {
        let (pkg, idx, cls) = self.open_export(path, Some("SoundWave"))?;
        let ctx = self.ctx(&pkg);
        let game = &self.game;
        let s = py.detach(|| assets::read_sound(game, &ctx, &pkg, idx, &cls)).map_err(err)?;
        Ok((s.format, PyBytes::new(py, &s.data), props_py(py, &s.props, &self.game, &pkg)?))
    }

    /// static_mesh(path, max_lods=1) -> {lods: [...], props}; geometry in Unreal units (cm, Z up, left-handed).
    #[pyo3(signature = (path, max_lods=1))]
    fn static_mesh<'py>(&self, py: Python<'py>, path: &str, max_lods: usize) -> PyResult<Bound<'py, PyDict>> {
        let (pkg, idx, cls) = self.open_export(path, Some("StaticMesh"))?;
        let ctx = self.ctx(&pkg);
        let game = &self.game;
        let m = py.detach(|| mesh::read_static_mesh(game, &ctx, &pkg, idx, &cls, max_lods)).map_err(err)?;
        self.mesh_py(py, m, &pkg)
    }

    /// skeletal_mesh(path, max_lods=1) -> {lods, bones: [(name, parent, rotation xyzw, translation, scale)], props}
    #[pyo3(signature = (path, max_lods=1))]
    fn skeletal_mesh<'py>(&self, py: Python<'py>, path: &str, max_lods: usize) -> PyResult<Bound<'py, PyDict>> {
        let (pkg, idx, cls) = self.open_export(path, Some("SkeletalMesh"))?;
        let ctx = self.ctx(&pkg);
        let game = &self.game;
        let m = py.detach(|| skel::read_skeletal_mesh(game, &ctx, &pkg, idx, &cls, max_lods)).map_err(err)?;
        self.mesh_py(py, m, &pkg)
    }
}

impl UnrealGame {
    fn mesh_py<'py>(&self, py: Python<'py>, m: mesh::Mesh, pkg: &Package) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        let lods = PyList::empty(py);
        let props = props_py(py, &m.props, &self.game, pkg)?;
        for l in m.lods {
            lods.append(lod_py(py, l)?)?;
        }
        d.set_item("lods", lods)?;
        let bones = PyList::empty(py);
        for b in &m.bones {
            bones.append((b.name.clone(), b.parent, b.rotation.to_vec(), b.translation.to_vec(), b.scale.to_vec()))?;
        }
        d.set_item("bones", bones)?;
        let mats: Vec<Option<String>> = m.materials.iter().map(|&i| object_ref(&self.game, pkg, i)).collect();
        d.set_item("materials", mats)?;
        d.set_item("props", props)?;
        Ok(d)
    }
}


pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(oodle_load, m)?)?;
    m.add_function(wrap_pyfunction!(unreal_mappings, m)?)?;
    m.add_function(wrap_pyfunction!(unreal_usmap, m)?)?;
    m.add_class::<UnrealGame>()?;
    Ok(())
}
