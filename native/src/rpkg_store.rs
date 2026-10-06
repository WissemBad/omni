//! Direct reading of a Glacier game's packages: every resource by hash, without extracting anything.
//!
//! The packages (`chunkN.rpkg`, `chunkNpatchM.rpkg`) are opened in order (chunk, then patch level); a later package
//! replaces the resources it also holds, and a patch's "unneeded" list removes resources of its chunk. Only the
//! tables are kept in memory (a few MB); resource data is read (and LZ4-decompressed / unscrambled) on demand
//! with positional reads, so one store serves many threads.

use std::collections::HashMap;
use std::fs::File;
use std::path::{Path, PathBuf};

use crate::rpkg::{self, Entry};

pub struct Store {
    pub packages: Vec<PathBuf>,
    files: Vec<File>,
    entries: Vec<Vec<Entry>>,
    chunk_of: Vec<u32>,
    /// hash -> (package, entry)
    index: HashMap<u64, (u32, u32)>,
}

impl Store {
    pub fn open(paths: &[PathBuf]) -> Result<Store, String> {
        let mut ordered: Vec<(u32, u32, PathBuf)> = paths
            .iter()
            .filter_map(|p| rpkg::name_ids(p).map(|(c, pa)| (c, pa, p.clone())))
            .collect();
        ordered.sort();
        let mut store = Store { packages: Vec::new(), files: Vec::new(), entries: Vec::new(), chunk_of: Vec::new(), index: HashMap::new() };
        for (chunk, _patch, path) in ordered {
            let pkg = rpkg::open(&path)?;
            let file = File::open(&path).map_err(|e| format!("{}: {e}", path.display()))?;
            let pi = store.files.len() as u32;
            for h in &pkg.unneeded {
                if let Some(&(p, _)) = store.index.get(h) {
                    if store.chunk_of[p as usize] == chunk {
                        store.index.remove(h);
                    }
                }
            }
            for (ei, e) in pkg.entries.iter().enumerate() {
                store.index.insert(e.hash, (pi, ei as u32));
            }
            store.packages.push(path);
            store.files.push(file);
            store.entries.push(pkg.entries);
            store.chunk_of.push(chunk);
        }
        if store.files.is_empty() {
            return Err("no chunk*.rpkg package".into());
        }
        Ok(store)
    }

    pub fn len(&self) -> usize {
        self.index.len()
    }

    pub fn is_empty(&self) -> bool {
        self.index.is_empty()
    }

    pub fn entry(&self, hash: u64) -> Option<&Entry> {
        let &(p, e) = self.index.get(&hash)?;
        self.entries.get(p as usize)?.get(e as usize)
    }

    /// Hashes of every resource of a type ("PRIM"), in no particular order.
    pub fn of_type(&self, ty: &str) -> Vec<u64> {
        let want: Vec<u8> = ty.bytes().rev().collect();
        self.index
            .iter()
            .filter(|(_, &(p, e))| self.entries[p as usize][e as usize].ty[..] == want[..])
            .map(|(h, _)| *h)
            .collect()
    }

    /// Resource counts per type.
    pub fn types(&self) -> HashMap<String, usize> {
        let mut out: HashMap<String, usize> = HashMap::new();
        for &(p, e) in self.index.values() {
            *out.entry(self.entries[p as usize][e as usize].type_name()).or_default() += 1;
        }
        out
    }

    pub fn read(&self, hash: u64) -> Result<Vec<u8>, String> {
        let &(p, e) = self.index.get(&hash).ok_or_else(|| format!("resource {hash:016X} is not in the packages"))?;
        rpkg::read_resource(&self.files[p as usize], &self.entries[p as usize][e as usize])
    }

    pub fn package_of(&self, hash: u64) -> Option<&Path> {
        let &(p, _) = self.index.get(&hash)?;
        self.packages.get(p as usize).map(|x| x.as_path())
    }
}
