//! Unreal Engine 5 games: IoStore containers, zen packages, reflection data read from the game executable
//! (property layouts for unversioned packages) and the asset types omni converts (textures, static and skeletal
//! meshes, materials, sounds).

pub mod aes;
pub mod assets;
pub mod iostore;
pub mod mesh;
pub mod oodle;
pub mod props;
pub mod reader;
pub mod reflect;
pub mod skel;
pub mod zen;

use std::collections::HashMap;
use std::path::Path;

use iostore::{chunk, ChunkId, Container, StoreEntry};
use reader::{err, Error, Result};
use zen::{obj_kind, ObjKind, Package, ScriptObjects};

/// UE5 object version of a release (packages cooked unversioned do not carry it).
pub fn ue5_version_of(engine: &str) -> i32 {
    match engine {
        "5.0" => 1004,
        "5.1" => 1008,
        "5.2" => 1009,
        "5.3" => 1010,
        "5.4" => 1012,
        "5.5" => 1013,
        "5.6" => 1016,
        _ if engine.starts_with("5.") => 1017,
        _ => 1008,
    }
}

pub struct Game {
    pub containers: Vec<Container>,
    pub engine: String,
    pub ue5_version: i32,
    pub header_version: i32,
    /// lower-case game path without extension (`/game/props/sm_chair`) -> (container, toc index) of the package
    pub packages: HashMap<String, (usize, u32)>,
    /// package id -> game path (original case, with extension) for every .uasset/.umap
    pub package_paths: HashMap<u64, String>,
    pub store: HashMap<u64, StoreEntry>,
    pub script: Option<ScriptObjects>,
    /// every file of the containers: game path (original case) -> (container, toc index)
    pub files: Vec<(String, usize, u32)>,
}

impl Game {
    pub fn open(paks: &Path, engine: &str, key: Option<[u8; 32]>) -> Result<Game> {
        let mut tocs: Vec<_> = std::fs::read_dir(paks)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().map_or(false, |x| x.eq_ignore_ascii_case("utoc")))
            .collect();
        // patch containers (`_P`) override the base ones: open them first so their entries win
        tocs.sort_by_key(|p| {
            let n = p.file_stem().map(|s| s.to_string_lossy().to_ascii_lowercase()).unwrap_or_default();
            (!n.ends_with("_p"), n)
        });
        let mut containers = Vec::new();
        for t in &tocs {
            match Container::open(t, key) {
                Ok(c) => containers.push(c),
                Err(e) => return err(format!("{}: {e}", t.display())),
            }
        }
        if containers.is_empty() {
            return err(format!("no .utoc container in {}", paks.display()));
        }
        let mut g = Game {
            containers,
            engine: engine.to_string(),
            ue5_version: ue5_version_of(engine),
            header_version: 2,
            packages: HashMap::new(),
            package_paths: HashMap::new(),
            store: HashMap::new(),
            script: None,
            files: Vec::new(),
        };
        let mut header_versions = Vec::new();
        for (ci, c) in g.containers.iter().enumerate() {
            for (rel, idx) in &c.files {
                let gp = iostore::game_path(&c.mount_point, rel);
                let lower = gp.to_ascii_lowercase();
                if let Some(stem) = lower.strip_suffix(".uasset").or_else(|| lower.strip_suffix(".umap")) {
                    g.packages.entry(stem.to_string()).or_insert((ci, *idx));
                    if let Some(id) = c.chunk_id(*idx) {
                        g.package_paths.entry(id.id).or_insert_with(|| gp.clone());
                    }
                }
                g.files.push((gp, ci, *idx));
            }
            if let Some(i) = c.index_of(&ChunkId::new(c.container_id, 0, chunk::CONTAINER_HEADER)) {
                if let Ok(data) = c.read_index(i) {
                    if let Ok((v, entries)) = iostore::parse_container_header(&data) {
                        header_versions.push(v);
                        for (k, e) in entries {
                            g.store.entry(k).or_insert(e);
                        }
                    }
                }
            }
            if g.script.is_none() {
                if let Some(i) = c.index_of(&ChunkId::new(0, 0, chunk::SCRIPT_OBJECTS)) {
                    g.script = Some(ScriptObjects::parse(&c.read_index(i)?)?);
                }
            }
        }
        if let Some(v) = header_versions.iter().max() {
            g.header_version = *v;
        }
        Ok(g)
    }

    fn locate(&self, path: &str) -> Option<(usize, u32)> {
        let p = path.to_ascii_lowercase();
        let p = p.strip_suffix(".uasset").or_else(|| p.strip_suffix(".umap")).unwrap_or(&p);
        let p = match p.rfind('.') {
            // `/Game/X/SM_Chair.SM_Chair` object path -> package path
            Some(dot) if dot > p.rfind('/').unwrap_or(0) => &p[..dot],
            _ => p,
        };
        self.packages.get(p).copied()
    }

    pub fn package(&self, path: &str) -> Result<Package> {
        let (ci, idx) = self.locate(path).ok_or_else(|| Error(format!("package not found: {path}")))?;
        let data = self.containers[ci].read_index(idx)?;
        Package::parse(data, self.header_version, self.ue5_version)
    }

    pub fn package_by_id(&self, id: u64) -> Result<Package> {
        let path = self.package_paths.get(&id).ok_or_else(|| Error(format!("package {id:016x} not found")))?;
        self.package(path)
    }

    /// Bulk data chunk of a package (`.ubulk`: index 0 type BulkData; `.uptnl`: OptionalBulkData).
    pub fn bulk_chunk(&self, package_id: u64, kind: u8, index: u16) -> Option<(usize, u32)> {
        let id = ChunkId::new(package_id, index, kind);
        self.containers.iter().enumerate().find_map(|(ci, c)| c.index_of(&id).map(|i| (ci, i)))
    }

    pub fn read_bulk(&self, package_id: u64, kind: u8, offset: u64, size: u64) -> Result<Vec<u8>> {
        let (ci, idx) = self.bulk_chunk(package_id, kind, 0).ok_or_else(|| Error("bulk data chunk not found".into()))?;
        let c = &self.containers[ci];
        if offset == 0 && size == c.chunk_size(idx) {
            return c.read_index(idx);
        }
        c.read_partial(idx, offset, size)
    }

    pub fn package_id_of(&self, path: &str) -> Option<u64> {
        let (ci, idx) = self.locate(path)?;
        self.containers[ci].chunk_id(idx).map(|c| c.id)
    }

    /// Imported packages of a package: from its own name list (5.3+) or the container's store entry.
    pub fn imported_packages(&self, pkg: &Package, package_id: u64) -> Vec<u64> {
        if let Some(e) = self.store.get(&package_id) {
            return e.imported.clone();
        }
        pkg.imported_package_names
            .iter()
            .filter_map(|n| self.package_id_of(n))
            .collect()
    }

    pub fn script_name(&self, index: u64) -> String {
        self.script.as_ref().and_then(|s| s.name(index)).unwrap_or("").to_string()
    }

    /// Class name of an export (`StaticMesh`, `Texture2D`, a Blueprint class...).
    pub fn class_name(&self, pkg: &Package, class_index: u64) -> String {
        match obj_kind(class_index) {
            ObjKind::Script(i) => self.script_name(i),
            ObjKind::Export(e) => pkg.exports.get(e as usize).map(|x| x.name.clone()).unwrap_or_default(),
            ObjKind::Package(..) => self.resolve(pkg, class_index).map(|(_, n)| n).unwrap_or_default(),
            ObjKind::Null => String::new(),
        }
    }

    /// Object reference -> (package path, object name). Script objects give (`/Script/Module`, name).
    pub fn resolve(&self, pkg: &Package, index: u64) -> Option<(String, String)> {
        match obj_kind(index) {
            ObjKind::Null => None,
            ObjKind::Export(e) => pkg.exports.get(e as usize).map(|x| (pkg.name.clone(), x.name.clone())),
            ObjKind::Script(i) => {
                let s = self.script.as_ref()?;
                let path = s.path(i);
                let (module, name) = path.split_once('.').unwrap_or((&path, ""));
                Some((module.to_string(), name.rsplit(':').next().unwrap_or(name).to_string()))
            }
            ObjKind::Package(pi, hi) => {
                let hash = *pkg.imported_hashes.get(hi as usize)?;
                let pid = self.package_id_of(&pkg.name)?;
                let imported = self.imported_packages(pkg, pid);
                let target = *imported.get(pi as usize)?;
                let path = self.package_paths.get(&target)?.clone();
                let path = path.trim_end_matches(".uasset").trim_end_matches(".umap").to_string();
                let other = self.package(&path).ok()?;
                let name = other.exports.iter().find(|e| e.public_hash == hash).map(|e| e.name.clone())?;
                Some((path, name))
            }
        }
    }
}

/// Main export of a package: the public export whose name is the package's leaf name, else the first one.
pub fn main_export(pkg: &Package) -> Option<usize> {
    let leaf = pkg.name.rsplit('/').next().unwrap_or("");
    pkg.exports
        .iter()
        .position(|e| e.name.eq_ignore_ascii_case(leaf) && e.outer == zen::INDEX_NULL)
        .or_else(|| pkg.exports.iter().position(|e| e.outer == zen::INDEX_NULL && e.flags & 1 != 0))
        .or(if pkg.exports.is_empty() { None } else { Some(0) })
}
