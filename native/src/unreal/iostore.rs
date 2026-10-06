//! Unreal IoStore containers (`.utoc` table of contents + `.ucas` data), UE 4.26 to 5.x.
//!
//! Layout (format as implemented by the MIT `retoc` project and Epic's IoStore code):
//!   header (0x90 bytes): magic "-==--==--==--==-", u8 version, ..., entry count, compressed block count/size,
//!     compression method count/length, block size, directory index size, partition count, container id,
//!     encryption key guid, u8 flags, perfect hash seed count, partition size, chunks without perfect hash
//!   entry count x FIoChunkId (12 bytes: u64 id, u16 index, u8 pad, u8 type)
//!   entry count x offset/length (5 + 5 bytes, big endian) in the uncompressed stream
//!   perfect hash seeds (i32) and overflow indices (i32), version dependent
//!   block count x compressed block (5 bytes offset, 3 compressed size, 3 raw size, 1 method index; little endian)
//!   method names (count x length bytes, NUL padded; method index 0 = stored)
//!   signatures when signed; directory index (AES when encrypted); per-entry metas.
//! Directory index: mount point FString, directory entries {name, first child, next sibling, first file} (u32,
//!   !0 = none), file entries {name, next file, toc index}, string table.

use std::collections::HashMap;
use std::fs::File;
use std::path::{Path, PathBuf};

use super::oodle;
use super::reader::{err, Error, Reader, Result};

pub const MAGIC: &[u8; 16] = b"-==--==--==--==-";

/// Chunk types (EIoChunkType, UE 5.0+ numbering; tables older than version 5 are remapped on load).
pub mod chunk {
    pub const EXPORT_BUNDLE_DATA: u8 = 1;
    pub const BULK_DATA: u8 = 2;
    pub const OPTIONAL_BULK_DATA: u8 = 3;
    pub const MEMORY_MAPPED_BULK_DATA: u8 = 4;
    pub const SCRIPT_OBJECTS: u8 = 5;
    pub const CONTAINER_HEADER: u8 = 6;
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ChunkId {
    pub id: u64,
    pub index: u16,
    pub kind: u8,
}

impl ChunkId {
    pub fn new(id: u64, index: u16, kind: u8) -> Self {
        ChunkId { id, index, kind }
    }
}

#[derive(Clone, Copy, Debug)]
struct Block {
    offset: u64,
    compressed: u32,
    raw: u32,
    method: u8,
}

pub struct Container {
    pub name: String,
    pub version: u8,
    pub container_id: u64,
    pub encrypted: bool,
    pub mount_point: String,
    block_size: u64,
    partition_size: u64,
    partitions: Vec<File>,
    chunks: Vec<ChunkId>,
    offsets: Vec<(u64, u64)>,
    blocks: Vec<Block>,
    methods: Vec<String>,
    by_id: HashMap<ChunkId, u32>,
    /// (path relative to the mount point, toc index)
    pub files: Vec<(String, u32)>,
    key: Option<[u8; 32]>,
}

#[cfg(windows)]
fn read_at(f: &File, buf: &mut [u8], mut off: u64) -> std::io::Result<()> {
    use std::os::windows::fs::FileExt;
    let mut done = 0;
    while done < buf.len() {
        let n = f.seek_read(&mut buf[done..], off)?;
        if n == 0 {
            return Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "end of container"));
        }
        done += n;
        off += n as u64;
    }
    Ok(())
}

#[cfg(unix)]
fn read_at(f: &File, buf: &mut [u8], off: u64) -> std::io::Result<()> {
    use std::os::unix::fs::FileExt;
    f.read_exact_at(buf, off)
}

fn be40(b: &[u8]) -> u64 {
    b.iter().fold(0u64, |a, &x| (a << 8) | x as u64)
}

/// Pre-5.0 chunk type numbers -> the current ones.
fn remap_old_type(t: u8) -> u8 {
    match t {
        1 => chunk::EXPORT_BUNDLE_DATA,
        2 => chunk::BULK_DATA,
        3 => chunk::OPTIONAL_BULK_DATA,
        4 => chunk::MEMORY_MAPPED_BULK_DATA,
        5 => 0x40 | 5, // LoaderGlobalMeta: not used by omni
        6 => 0x40 | 6, // LoaderInitialLoadMeta
        7 => 0x40 | 7, // LoaderGlobalNames
        8 => 0x40 | 8, // LoaderGlobalNameHashes
        9 => chunk::CONTAINER_HEADER,
        x => 0x40 | x,
    }
}

impl Container {
    pub fn open(utoc: &Path, key: Option<[u8; 32]>) -> Result<Container> {
        let toc = std::fs::read(utoc)?;
        let mut r = Reader::new(&toc);
        if r.bytes(16)? != MAGIC {
            return err(format!("{}: not an IoStore table of contents", utoc.display()));
        }
        let version = r.u8()?;
        r.skip(3)?;
        let header_size = r.u32()? as usize;
        let entry_count = r.u32()? as usize;
        let block_count = r.u32()? as usize;
        let block_entry_size = r.u32()? as usize;
        let method_count = r.u32()? as usize;
        let method_len = r.u32()? as usize;
        let block_size = r.u32()? as u64;
        let dir_size = r.u32()? as usize;
        let _partition_count = r.u32()?;
        let container_id = r.u64()?;
        let _key_guid = r.guid()?;
        let flags = r.u8()?;
        r.skip(3)?;
        let seeds = r.u32()? as usize;
        let partition_size = r.u64()?;
        let overflow = r.u32()? as usize;
        if header_size != 0x90 || block_entry_size != 12 || block_size == 0 {
            return err(format!("{}: unsupported IoStore header (size {header_size}, version {version})", utoc.display()));
        }
        r.seek(header_size)?;
        let encrypted = flags & 0x02 != 0;
        let signed = flags & 0x04 != 0;
        let indexed = flags & 0x08 != 0;

        let chunks = r.n_of(entry_count, 12, |r| {
            let id = r.u64()?;
            let index = r.u16()?;
            r.skip(1)?;
            let t = r.u8()?;
            Ok(ChunkId { id, index, kind: if version >= 5 { t } else { remap_old_type(t) } })
        })?;
        let offsets = r.n_of(entry_count, 10, |r| {
            let b = r.bytes(10)?;
            Ok((be40(&b[0..5]), be40(&b[5..10])))
        })?;
        let (n_seeds, n_overflow) = match version {
            v if v >= 5 => (seeds, overflow),
            4 => (seeds, 0),
            _ => (0, 0),
        };
        r.skip(n_seeds.checked_mul(4).ok_or(Error("seeds".into()))?)?;
        r.skip(n_overflow.checked_mul(4).ok_or(Error("overflow".into()))?)?;
        let blocks = r.n_of(block_count, 12, |r| {
            let b = r.bytes(12)?;
            Ok(Block {
                offset: u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], 0, 0, 0]),
                compressed: u32::from_le_bytes([b[5], b[6], b[7], 0]),
                raw: u32::from_le_bytes([b[8], b[9], b[10], 0]),
                method: b[11],
            })
        })?;
        let methods = r.n_of(method_count, method_len, |r| {
            let b = r.bytes(method_len)?;
            let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
            Ok(String::from_utf8_lossy(&b[..end]).to_ascii_lowercase())
        })?;
        if signed {
            let size = r.u32()? as usize;
            r.skip(size.checked_mul(2).ok_or(Error("signature".into()))?)?;
            r.skip(block_count.checked_mul(20).ok_or(Error("signature".into()))?)?;
        }
        let mut mount_point = String::new();
        let mut files = Vec::new();
        if indexed && dir_size > 0 {
            let mut dir = r.bytes(dir_size)?.to_vec();
            if encrypted {
                match &key {
                    Some(k) => super::aes::decrypt_ecb(k, &mut dir),
                    None => return err("encrypted container: an AES key is needed"),
                }
            }
            let (mp, f) = parse_directory_index(&dir)?;
            mount_point = mp;
            files = f;
        }
        let mut by_id = HashMap::with_capacity(chunks.len());
        for (i, c) in chunks.iter().enumerate() {
            by_id.insert(*c, i as u32);
        }
        let name = utoc.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let mut partitions = Vec::new();
        let base: PathBuf = utoc.with_extension("ucas");
        partitions.push(File::open(&base).map_err(|e| Error(format!("{}: {e}", base.display())))?);
        for i in 1..64 {
            let p = utoc.with_file_name(format!("{name}_s{i}.ucas"));
            match File::open(&p) {
                Ok(f) => partitions.push(f),
                Err(_) => break,
            }
        }
        Ok(Container {
            name,
            version,
            container_id,
            encrypted,
            mount_point,
            block_size,
            partition_size: if partition_size == 0 { u64::MAX } else { partition_size },
            partitions,
            chunks,
            offsets,
            blocks,
            methods,
            by_id,
            files,
            key,
        })
    }

    pub fn chunk_ids(&self) -> &[ChunkId] {
        &self.chunks
    }

    pub fn index_of(&self, id: &ChunkId) -> Option<u32> {
        self.by_id.get(id).copied()
    }

    pub fn chunk_id(&self, index: u32) -> Option<ChunkId> {
        self.chunks.get(index as usize).copied()
    }

    pub fn chunk_size(&self, index: u32) -> u64 {
        self.offsets.get(index as usize).map(|o| o.1).unwrap_or(0)
    }

    /// The whole chunk at `index`.
    pub fn read_index(&self, index: u32) -> Result<Vec<u8>> {
        let (off, len) = *self.offsets.get(index as usize).ok_or(Error("bad toc index".into()))?;
        self.read_range_raw(off, len, 0, len)
    }

    /// `len` bytes at `start` inside the chunk `index` (only the blocks that hold them are read).
    pub fn read_partial(&self, index: u32, start: u64, len: u64) -> Result<Vec<u8>> {
        let (off, size) = *self.offsets.get(index as usize).ok_or(Error("bad toc index".into()))?;
        if start.checked_add(len).map_or(true, |e| e > size) {
            return err(format!("range {start}+{len} outside chunk of {size} bytes"));
        }
        self.read_range_raw(off, size, start, len)
    }

    fn read_range_raw(&self, chunk_off: u64, chunk_len: u64, start: u64, len: u64) -> Result<Vec<u8>> {
        if len > (1u64 << 32) || chunk_len > (1u64 << 36) {
            return err("chunk too large");
        }
        if len == 0 {
            return Ok(Vec::new());
        }
        let abs = chunk_off + start;
        let first = (abs / self.block_size) as usize;
        let last = ((abs + len - 1) / self.block_size) as usize;
        if last >= self.blocks.len() {
            return err("chunk outside the compressed block table");
        }
        let mut out = Vec::with_capacity(len as usize);
        let mut comp = Vec::new();
        let mut raw = Vec::new();
        for bi in first..=last {
            let b = self.blocks[bi];
            let block_start = bi as u64 * self.block_size;
            let stored = if self.encrypted { (b.compressed as usize + 15) & !15 } else { b.compressed as usize };
            comp.resize(stored, 0);
            let part = (b.offset / self.partition_size) as usize;
            let poff = b.offset % self.partition_size;
            let file = self.partitions.get(part).ok_or(Error(format!("missing container partition {part}")))?;
            read_at(file, &mut comp, poff)?;
            if self.encrypted {
                match &self.key {
                    Some(k) => super::aes::decrypt_ecb(k, &mut comp),
                    None => return err("encrypted container: an AES key is needed"),
                }
            }
            let data: &[u8] = if b.method == 0 {
                &comp[..b.raw.min(b.compressed) as usize]
            } else {
                raw.resize(b.raw as usize, 0);
                let method = self.methods.get(b.method as usize - 1).map(|s| s.as_str()).unwrap_or("?");
                decompress(method, &comp[..b.compressed as usize], &mut raw)?;
                &raw
            };
            let lo = abs.saturating_sub(block_start) as usize;
            let hi = ((abs + len).min(block_start + b.raw as u64) - block_start) as usize;
            if lo > hi || hi > data.len() {
                return err("compressed block shorter than announced");
            }
            out.extend_from_slice(&data[lo..hi]);
        }
        if out.len() as u64 != len {
            return err(format!("read {} of {len} bytes", out.len()));
        }
        Ok(out)
    }
}

pub fn decompress(method: &str, src: &[u8], dst: &mut [u8]) -> Result<()> {
    match method {
        "oodle" => oodle::decompress(src, dst),
        "zlib" => {
            let v = miniz_oxide::inflate::decompress_to_vec_zlib_with_limit(src, dst.len())
                .map_err(|e| Error(format!("zlib: {e:?}")))?;
            if v.len() != dst.len() {
                return err("zlib: wrong size");
            }
            dst.copy_from_slice(&v);
            Ok(())
        }
        "gzip" => {
            let body = src.get(10..).ok_or(Error("gzip: truncated".into()))?;
            let v = miniz_oxide::inflate::decompress_to_vec_with_limit(body, dst.len())
                .map_err(|e| Error(format!("gzip: {e:?}")))?;
            let n = v.len().min(dst.len());
            dst[..n].copy_from_slice(&v[..n]);
            Ok(())
        }
        "lz4" => {
            let n = lz4_flex::block::decompress_into(src, dst).map_err(|e| Error(format!("lz4: {e}")))?;
            if n != dst.len() {
                return err("lz4: wrong size");
            }
            Ok(())
        }
        m => err(format!("unsupported compression method '{m}'")),
    }
}

pub(crate) fn parse_directory_index(data: &[u8]) -> Result<(String, Vec<(String, u32)>)> {
    let mut r = Reader::new(data);
    let mount = r.fstring()?;
    let dirs = r.array(16, |r| Ok([r.u32()?, r.u32()?, r.u32()?, r.u32()?]))?;
    let files = r.array(12, |r| Ok([r.u32()?, r.u32()?, r.u32()?]))?;
    let strings = r.array(4, |r| r.fstring())?;
    let mut out = Vec::with_capacity(files.len());
    if dirs.is_empty() {
        return Ok((mount, out));
    }
    // iterative walk (deep trees must not overflow the stack): (dir index, path prefix)
    let mut stack: Vec<(u32, String)> = vec![(0, String::new())];
    let mut guard = 0usize;
    while let Some((d, prefix)) = stack.pop() {
        guard += 1;
        if guard > dirs.len() + 1 {
            return err("directory index has a cycle");
        }
        let [name, first_child, _sibling, first_file] = *dirs.get(d as usize).ok_or(Error("bad directory index".into()))?;
        let path = if name == u32::MAX {
            prefix
        } else {
            let n = strings.get(name as usize).ok_or(Error("bad directory name".into()))?;
            if prefix.is_empty() { n.clone() } else { format!("{prefix}/{n}") }
        };
        let mut f = first_file;
        let mut seen = 0usize;
        while f != u32::MAX {
            let [fname, next, user] = *files.get(f as usize).ok_or(Error("bad file index".into()))?;
            let n = strings.get(fname as usize).ok_or(Error("bad file name".into()))?;
            out.push((if path.is_empty() { n.clone() } else { format!("{path}/{n}") }, user));
            f = next;
            seen += 1;
            if seen > files.len() {
                return err("file list has a cycle");
            }
        }
        let mut c = first_child;
        let mut seen = 0usize;
        while c != u32::MAX {
            stack.push((c, path.clone()));
            c = dirs.get(c as usize).ok_or(Error("bad directory index".into()))?[2];
            seen += 1;
            if seen > dirs.len() {
                return err("directory list has a cycle");
            }
        }
    }
    Ok((mount, out))
}

/// Mount-relative path -> game path: `<Project>/Content/X/Y.uasset` -> `/Game/X/Y.uasset`,
/// `Engine/Content/...` -> `/Engine/...`, `<..>/Plugins/<..>/<Plugin>/Content/...` -> `/<Plugin>/...`.
pub fn game_path(mount: &str, rel: &str) -> String {
    let full = format!("{}{}", mount.trim_start_matches("../../../"), rel);
    let parts: Vec<&str> = full.split('/').filter(|s| !s.is_empty()).collect();
    if parts.len() >= 2 && parts[0].eq_ignore_ascii_case("Engine") && parts[1].eq_ignore_ascii_case("Content") {
        return format!("/Engine/{}", parts[2..].join("/"));
    }
    if let Some(ci) = parts.iter().position(|p| p.eq_ignore_ascii_case("Content")) {
        if parts.iter().take(ci).any(|p| p.eq_ignore_ascii_case("Plugins")) && ci >= 1 {
            return format!("/{}/{}", parts[ci - 1], parts[ci + 1..].join("/"));
        }
        if ci == 1 {
            return format!("/Game/{}", parts[2..].join("/"));
        }
    }
    format!("/{}", parts.join("/"))
}

/// Package store entry of the container header: the packages a package imports.
pub struct StoreEntry {
    pub export_count: i32,
    pub export_bundle_count: i32,
    pub imported: Vec<u64>,
}

/// Container header (EIoContainerHeaderVersion >= 1, UE 5.0+): package ids and their store entries.
pub fn parse_container_header(data: &[u8]) -> Result<(i32, HashMap<u64, StoreEntry>)> {
    let mut r = Reader::new(data);
    let magic = r.u32()?;
    if magic != 0x496f436e {
        return err("UE4-era container header (not supported)");
    }
    let version = r.u32()? as i32;
    let _container_id = r.u64()?;
    if version < 2 {
        let _count = r.u32()?;
    }
    let ids = r.array(8, |r| r.u64())?;
    let n = r.count(1)?;
    let buf = r.bytes(n)?;
    let (member, size) = match version {
        1 | 2 => (8usize, 24usize),
        _ => (0, 16),
    };
    let mut out = HashMap::with_capacity(ids.len());
    for (i, id) in ids.iter().enumerate() {
        let base = i * size;
        let mut e = Reader::at(buf, base);
        let (export_count, export_bundle_count) = if version <= 2 { (e.i32()?, e.i32()?) } else { (0, 0) };
        if version > 2 {
            e.seek(base)?;
        }
        let num = e.u32()? as usize;
        let rel = e.u32()? as usize;
        let mut imported = Vec::new();
        if num > 0 {
            let mut a = Reader::at(buf, base + member + rel);
            imported = a.n_of(num, 8, |a| a.u64())?;
        }
        out.insert(*id, StoreEntry { export_count, export_bundle_count, imported });
    }
    Ok((version, out))
}
