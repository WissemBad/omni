//! Zen packages (the cooked package format inside IoStore containers, UE 5.0+) and the global script objects.
//!
//! Package header (container header version >= 1):
//!   u32 has versioning info, u32 header size, FMappedName name, u32 package flags, u32 cooked header size,
//!   i32 imported public export hashes offset, i32 import map offset, i32 export map offset,
//!   i32 export bundle entries offset, then i32 graph data offset (< 5.3) or the dependency bundle headers /
//!   entries / imported package names offsets (5.3+); versioning info; [5.6+: cell import/export map offsets];
//!   name batch; [5.2+: bulk data map]; imported public export hashes (u64); import map (FPackageObjectIndex u64);
//!   export map (72-byte entries); export bundle entries {u32 export index, u32 command (0 create, 1 serialize)}.
//! Export data follows the header in export bundle order (< 5.3) or at header size + cooked serial offset.

use super::reader::{err, Error, Reader, Result};

pub const INDEX_NULL: u64 = u64::MAX;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ObjKind {
    Export(u32),
    Script(u64),
    /// (imported package index, imported public export hash index)
    Package(u32, u32),
    Null,
}

pub fn obj_kind(v: u64) -> ObjKind {
    if v == INDEX_NULL {
        return ObjKind::Null;
    }
    let id = v & ((1u64 << 62) - 1);
    match v >> 62 {
        0 => ObjKind::Export(id as u32),
        1 => ObjKind::Script(v),
        2 => ObjKind::Package((id >> 32) as u32, id as u32),
        _ => ObjKind::Null,
    }
}

/// Names of a name batch (FNameBatch: count, string bytes, hash version, hashes, BE headers, strings).
pub fn read_name_batch(r: &mut Reader) -> Result<Vec<String>> {
    let n = r.u32()? as usize;
    if n == 0 {
        return Ok(Vec::new());
    }
    let _bytes = r.u32()?;
    let _hash_version = r.u64()?;
    r.check_count(n as i64, 10)?;
    r.skip(n * 8)?;
    let heads: Vec<u16> = r.n_of(n, 2, |r| Ok(u16::from_be_bytes([r.u8()?, r.u8()?])))?;
    let mut names = Vec::with_capacity(n);
    for h in heads {
        let len = (h & 0x7FFF) as usize;
        if h & 0x8000 != 0 {
            let b = r.bytes(len * 2)?;
            let w: Vec<u16> = b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            names.push(String::from_utf16_lossy(&w));
        } else {
            names.push(String::from_utf8_lossy(r.bytes(len)?).into_owned());
        }
    }
    Ok(names)
}

pub fn mapped_name(names: &[String], index: u32, number: u32) -> String {
    let base = names.get((index & 0x3FFF_FFFF) as usize).map(|s| s.as_str()).unwrap_or("None");
    if number == 0 {
        base.to_string()
    } else {
        format!("{base}_{}", number - 1)
    }
}

#[derive(Clone, Debug)]
pub struct Export {
    pub name: String,
    pub outer: u64,
    pub class: u64,
    pub super_index: u64,
    pub template: u64,
    pub public_hash: u64,
    pub flags: u32,
    pub serial_offset: u64,
    pub serial_size: u64,
    /// absolute range of the export's data in `Package::data`
    pub start: usize,
    pub end: usize,
}

#[derive(Clone, Debug)]
pub struct BulkEntry {
    pub offset: i64,
    pub dup_offset: i64,
    pub size: i64,
    pub flags: u32,
    pub cooked_index: u8,
}

pub struct Package {
    pub data: Vec<u8>,
    pub names: Vec<String>,
    pub name: String,
    pub flags: u32,
    pub header_size: usize,
    pub cooked_header_size: usize,
    pub ue5_version: i32,
    pub imported_hashes: Vec<u64>,
    pub imports: Vec<u64>,
    pub exports: Vec<Export>,
    pub bulk: Vec<BulkEntry>,
    pub imported_package_names: Vec<String>,
    /// end of the export data (start of inline bulk payloads in older layouts)
    pub exports_end: usize,
}

impl Package {
    /// `header_version`: container header version (1 = 5.0, 2 = 5.1/5.2, 3+ = 5.3+, 5 = 5.6+).
    /// `ue5_version`: object version used when the package is unversioned (from the engine version).
    pub fn parse(data: Vec<u8>, header_version: i32, ue5_version: i32) -> Result<Package> {
        let mut r = Reader::new(&data);
        if header_version < 1 {
            return err("UE4 zen packages are not supported");
        }
        let has_versioning = r.u32()? != 0;
        let header_size = r.u32()? as usize;
        let name_idx = r.u32()?;
        let name_num = r.u32()?;
        let flags = r.u32()?;
        let cooked_header_size = r.u32()? as usize;
        let hashes_off = r.i32()?;
        let import_off = r.i32()?;
        let export_off = r.i32()?;
        let bundle_off = r.i32()?;
        let (mut graph_off, mut dep_headers_off, mut imported_names_off) = (-1, -1, -1);
        if header_version >= 3 {
            dep_headers_off = r.i32()?;
            let _dep_entries_off = r.i32()?;
            imported_names_off = r.i32()?;
        } else {
            graph_off = r.i32()?;
        }
        let mut ue5 = ue5_version;
        if has_versioning {
            let _zen_version = r.u32()?;
            let _ue4 = r.i32()?;
            ue5 = r.i32()?;
            let _licensee = r.i32()?;
            let n = r.count(20)?;
            r.skip(n * 20)?;
        }
        let mut cell_import_off = bundle_off;
        if header_version >= 5 {
            cell_import_off = r.i32()?;
            let _cell_export_off = r.i32()?;
        }
        let names = read_name_batch(&mut r)?;
        let mut bulk = Vec::new();
        if ue5 >= 1009 {
            if ue5 >= 1012 {
                let pad = r.u64()? as usize;
                r.skip(pad)?;
            }
            let size = r.i64()?;
            let n = r.check_count(size / 32, 32)?;
            bulk = r.n_of(n, 32, |r| {
                let e = BulkEntry { offset: r.i64()?, dup_offset: r.i64()?, size: r.i64()?, flags: r.u32()?, cooked_index: r.u8()? };
                r.skip(3)?;
                Ok(e)
            })?;
        }
        let check = |o: i32| -> Result<usize> {
            if o < 0 || o as usize > data.len() {
                return err(format!("package offset {o} outside {} bytes", data.len()));
            }
            Ok(o as usize)
        };
        let (hashes_off, import_off, export_off, bundle_off) = (check(hashes_off)?, check(import_off)?, check(export_off)?, check(bundle_off)?);
        let cell_import_off = check(cell_import_off)?;
        if !(hashes_off <= import_off && import_off <= export_off && export_off <= cell_import_off) {
            return err("package header offsets out of order");
        }
        let mut r = Reader::at(&data, hashes_off);
        let imported_hashes = r.n_of((import_off - hashes_off) / 8, 8, |r| r.u64())?;
        let mut r = Reader::at(&data, import_off);
        let imports = r.n_of((export_off - import_off) / 8, 8, |r| r.u64())?;
        let n_exports = (cell_import_off - export_off) / 72;
        let mut r = Reader::at(&data, export_off);
        let mut exports = r.n_of(n_exports, 72, |r| {
            let serial_offset = r.u64()?;
            let serial_size = r.u64()?;
            let ni = r.u32()?;
            let nn = r.u32()?;
            let outer = r.u64()?;
            let class = r.u64()?;
            let super_index = r.u64()?;
            let template = r.u64()?;
            let public_hash = r.u64()?;
            let flags = r.u32()?;
            r.skip(4)?;
            Ok(Export { name: mapped_name(&names, ni, nn), outer, class, super_index, template, public_hash, flags, serial_offset, serial_size, start: 0, end: 0 })
        })?;
        let bundle_end = if dep_headers_off > 0 { check(dep_headers_off)? } else if graph_off > 0 { check(graph_off)? } else { bundle_off + n_exports * 16 };
        let mut r = Reader::at(&data, bundle_off);
        let entries = r.n_of(bundle_end.saturating_sub(bundle_off) / 8, 8, |r| Ok((r.u32()?, r.u32()?)))?;
        let mut exports_end = header_size;
        if header_version < 3 {
            let mut cur = header_size;
            for (idx, cmd) in &entries {
                if *cmd != 1 {
                    continue;
                }
                let e = exports.get_mut(*idx as usize).ok_or(Error("export bundle names a missing export".into()))?;
                let end = cur.checked_add(e.serial_size as usize).ok_or(Error("export size".into()))?;
                if end > data.len() {
                    return err(format!("export '{}' outside the package", e.name));
                }
                e.start = cur;
                e.end = end;
                cur = end;
            }
            exports_end = cur;
        } else {
            for e in exports.iter_mut() {
                let start = header_size.checked_add(e.serial_offset as usize).ok_or(Error("export offset".into()))?;
                let end = start.checked_add(e.serial_size as usize).ok_or(Error("export size".into()))?;
                if end > data.len() {
                    return err(format!("export '{}' outside the package", e.name));
                }
                e.start = start;
                e.end = end;
                exports_end = exports_end.max(end);
            }
        }
        let mut imported_package_names = Vec::new();
        if imported_names_off > 0 {
            let mut r = Reader::at(&data, check(imported_names_off)?);
            let base = read_name_batch(&mut r)?;
            for b in base {
                let num = r.i32().unwrap_or(0);
                imported_package_names.push(if num > 0 { format!("{b}_{}", num - 1) } else { b });
            }
        }
        let name = mapped_name(&names, name_idx, name_num);
        Ok(Package {
            data,
            names,
            name,
            flags,
            header_size,
            cooked_header_size,
            ue5_version: ue5,
            imported_hashes,
            imports,
            exports,
            bulk,
            imported_package_names,
            exports_end,
        })
    }

    pub fn export_data(&self, i: usize) -> &[u8] {
        let e = &self.exports[i];
        &self.data[e.start..e.end]
    }

    pub fn name_at(&self, index: u32, number: u32) -> String {
        mapped_name(&self.names, index, number)
    }
}

/// Global script objects (`global.utoc`, chunk ScriptObjects): every native class / struct / function object.
pub struct ScriptObjects {
    pub names: Vec<String>,
    /// global index -> (name, outer global index, CDO class index)
    pub by_index: std::collections::HashMap<u64, (String, u64, u64)>,
}

impl ScriptObjects {
    pub fn parse(data: &[u8]) -> Result<ScriptObjects> {
        let mut r = Reader::new(data);
        let names = read_name_batch(&mut r)?;
        let n = r.count(32)?;
        let mut by_index = std::collections::HashMap::with_capacity(n);
        for _ in 0..n {
            let ni = r.u32()?;
            let nn = r.u32()?;
            let gi = r.u64()?;
            let outer = r.u64()?;
            let cdo = r.u64()?;
            by_index.insert(gi, (mapped_name(&names, ni, nn), outer, cdo));
        }
        Ok(ScriptObjects { names, by_index })
    }

    pub fn name(&self, index: u64) -> Option<&str> {
        self.by_index.get(&index).map(|e| e.0.as_str())
    }

    /// `/Script/Engine.StaticMesh` style path of a script object.
    pub fn path(&self, index: u64) -> String {
        let mut parts = Vec::new();
        let mut cur = index;
        for _ in 0..16 {
            match self.by_index.get(&cur) {
                Some((n, outer, _)) => {
                    parts.push(n.clone());
                    if *outer == INDEX_NULL || *outer == 0 {
                        break;
                    }
                    cur = *outer;
                }
                None => break,
            }
        }
        parts.reverse();
        match parts.len() {
            0 => String::new(),
            1 => parts[0].clone(),
            _ => format!("{}.{}", parts[0], parts[1..].join(":")),
        }
    }
}
