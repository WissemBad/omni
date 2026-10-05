//! Glacier resource packages (.rpkg): reader and extractor, so omni can build its own `Assets/Sorted` tree from a
//! game installation (no external RPKG tool).
//!
//! Layout (RPKG v1 "GKPR" and v2 "2KPR", little endian; format as documented by the `rpkg-rs` project):
//!   magic[4]; v2 only: u32 unknown, u8 chunk id, u8 chunk type, u8 patch id, [u8;2] language
//!   u32 file_count, u32 offset_table_size, u32 metadata_table_size
//!   patches only: u32 unneeded_count + u64 ids (resources of earlier packages the patch removes)
//!   file_count x { u64 hash, u64 data offset, u32 flags (bit 31 = XOR-scrambled, low 31 bits = compressed size) }
//!   file_count x resource header { type[4] (reversed text), u32 references size, [u32 states size, v2 layouts],
//!                                  u32 data size, u32 system memory, u32 video memory, references }
//!   references: u32 (bits 0-29 count, bit 30 = flags before hashes) then count flag bytes + count u64 hashes
//!               (or hashes then flags in the legacy order)
//!   data: LZ4 block when the compressed size is set, after an XOR with dc 45 a6 9c d3 72 4c ab when scrambled.
//!
//! The output is `<root>/<chunk>/<TYPE>/<HASH>.<TYPE>` plus `<HASH>.<TYPE>.meta` (hash, flags, type, references:
//! what `sources/glacier/meta.py` reads). Existing files of the right size are kept: an extraction resumes.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use crate::par::*;

pub static DONE: AtomicU64 = AtomicU64::new(0);
pub static TOTAL: AtomicU64 = AtomicU64::new(0);
pub static CANCEL: AtomicBool = AtomicBool::new(false);

const XOR: [u8; 8] = [0xdc, 0x45, 0xa6, 0x9c, 0xd3, 0x72, 0x4c, 0xab];

#[derive(Clone, Debug)]
pub struct Entry {
    pub hash: u64,
    pub offset: u64,
    pub compressed: u32,
    pub scrambled: bool,
    pub ty: [u8; 4],
    pub data_size: u32,
    pub sys_mem: u32,
    pub vid_mem: u32,
    pub refs: Vec<(u64, u8)>,
}

impl Entry {
    /// Type name as the game writes it ("PRIM").
    pub fn type_name(&self) -> String {
        let mut t = self.ty;
        t.reverse();
        String::from_utf8_lossy(&t).into_owned()
    }
    pub fn stored_size(&self) -> u64 {
        if self.compressed != 0 { self.compressed as u64 } else { self.data_size as u64 }
    }
}

#[derive(Debug)]
pub struct Package {
    pub path: PathBuf,
    pub version: u8,
    pub chunk_id: u8,
    pub patch_id: u8,
    pub entries: Vec<Entry>,
    pub unneeded: Vec<u64>,
}

struct Cur<'a> {
    d: &'a [u8],
    o: usize,
}

impl<'a> Cur<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], String> {
        let s = self.d.get(self.o..self.o.checked_add(n).ok_or("overflow")?).ok_or("truncated package header")?;
        self.o += n;
        Ok(s)
    }
    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.take(1)?[0])
    }
    fn u32(&mut self) -> Result<u32, String> {
        let b = self.take(4)?;
        Ok(u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }
    fn u64(&mut self) -> Result<u64, String> {
        let b = self.take(8)?;
        Ok(u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
    }
}

/// Chunk and patch numbers from a file name: `chunk12patch3.rpkg` -> (12, 3); `chunk0.rpkg` -> (0, 0).
pub fn name_ids(path: &Path) -> Option<(u32, u32)> {
    let stem = path.file_stem()?.to_str()?.to_ascii_lowercase();
    let rest = stem.strip_prefix("chunk")?;
    let (c, p) = match rest.split_once("patch") {
        Some((c, p)) => (c, p),
        None => (rest, "0"),
    };
    // names like "chunk0" or "chunk25" ("chunk0audio" style variants are not packages of this game)
    Some((c.parse().ok()?, p.parse().ok()?))
}

fn parse_headers(d: &[u8], file_count: usize, table: usize, is_patch: bool, start: usize) -> Result<(Vec<Entry>, Vec<u64>), String> {
    let mut c = Cur { d, o: start };
    let mut unneeded = Vec::new();
    if is_patch {
        let n = c.u32()? as usize;
        if n > 50_000_000 {
            return Err("implausible patch table".into());
        }
        for _ in 0..n {
            unneeded.push(c.u64()?);
        }
    }
    let mut entries = Vec::with_capacity(file_count);
    for _ in 0..file_count {
        let hash = c.u64()?;
        let offset = c.u64()?;
        let flags = c.u32()?;
        entries.push(Entry { hash, offset, compressed: flags & 0x7FFF_FFFF, scrambled: flags & 0x8000_0000 != 0,
            ty: [0; 4], data_size: 0, sys_mem: 0, vid_mem: 0, refs: Vec::new() });
    }
    let meta_start = c.o;
    // the resource headers exist with or without a "states size" field: the right layout ends exactly at the end
    // of the metadata table
    for states in [false, true] {
        let mut m = Cur { d, o: meta_start };
        let mut out = entries.clone();
        let ok = (|| -> Result<bool, String> {
            for e in out.iter_mut() {
                let t = m.take(4)?;
                e.ty = [t[0], t[1], t[2], t[3]];
                let refs_size = m.u32()? as usize;
                if states {
                    m.u32()?;
                }
                e.data_size = m.u32()?;
                e.sys_mem = m.u32()?;
                e.vid_mem = m.u32()?;
                if refs_size > 0 {
                    let blk = m.take(refs_size)?;
                    let mut r = Cur { d: blk, o: 0 };
                    let cf = r.u32()?;
                    let count = (cf & 0x3FFF_FFFF) as usize;
                    let new_format = cf & 0x4000_0000 != 0;
                    if count > refs_size {
                        return Ok(false);
                    }
                    let (hashes, flags): (Vec<u64>, Vec<u8>);
                    if new_format {
                        flags = r.take(count)?.to_vec();
                        hashes = (0..count).map(|_| r.u64()).collect::<Result<_, _>>()?;
                    } else {
                        hashes = (0..count).map(|_| r.u64()).collect::<Result<_, _>>()?;
                        flags = r.take(count)?.to_vec();
                    }
                    e.refs = hashes.into_iter().zip(flags).collect();
                }
            }
            Ok(m.o - meta_start == table || (table == 0 && m.o <= d.len()))
        })();
        if matches!(ok, Ok(true)) {
            return Ok((out, unneeded));
        }
    }
    Err("cannot read the resource headers (unknown package layout)".into())
}

/// Read the tables of a package (the data is not touched).
pub fn open(path: &Path) -> Result<Package, String> {
    let mut f = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut head = vec![0u8; 64];
    let n = f.read(&mut head).map_err(|e| e.to_string())?;
    head.truncate(n);
    let magic = head.get(..4).ok_or("not a package")?;
    let version = match magic {
        b"GKPR" => 1,
        b"2KPR" => 2,
        _ => return Err(format!("{}: not an RPKG package", path.display())),
    };
    let mut c = Cur { d: &head, o: 4 };
    let (mut chunk_id, mut patch_id) = (0u8, 0u8);
    if version == 2 {
        c.u32()?;
        chunk_id = c.u8()?;
        c.u8()?;
        patch_id = c.u8()?;
        c.take(2)?;
    }
    let file_count = c.u32()? as usize;
    let offset_table = c.u32()? as usize;
    let meta_table = c.u32()? as usize;
    let header_len = c.o;
    let is_patch = name_ids(path).map(|(_, p)| p > 0).unwrap_or(false) || patch_id > 0;
    // read exactly the header: unneeded list + offset table + metadata table
    let mut rest = Vec::new();
    f = File::open(path).map_err(|e| e.to_string())?;
    let mut skip = vec![0u8; header_len];
    f.read_exact(&mut skip).map_err(|e| e.to_string())?;
    let mut buf = vec![0u8; 1 << 20];
    // the unneeded list of a patch precedes the tables and is not counted in their sizes: read in chunks until parsed
    let want = |r: &Vec<u8>| -> Option<usize> {
        let extra = if is_patch && r.len() >= 4 { 4 + 8 * u32::from_le_bytes([r[0], r[1], r[2], r[3]]) as usize } else if is_patch { return None } else { 0 };
        Some(extra + offset_table + meta_table)
    };
    loop {
        if let Some(w) = want(&rest) {
            if rest.len() >= w {
                break;
            }
        }
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        rest.extend_from_slice(&buf[..n]);
    }
    let table = meta_table;
    let (entries, unneeded) = parse_headers(&rest, file_count, table, is_patch, 0)?;
    Ok(Package { path: path.to_path_buf(), version, chunk_id, patch_id, entries, unneeded })
}

#[cfg(windows)]
fn read_at(f: &File, buf: &mut [u8], off: u64) -> std::io::Result<()> {
    use std::os::windows::fs::FileExt;
    let mut done = 0;
    while done < buf.len() {
        let n = f.seek_read(&mut buf[done..], off + done as u64)?;
        if n == 0 {
            return Err(std::io::ErrorKind::UnexpectedEof.into());
        }
        done += n;
    }
    Ok(())
}

#[cfg(unix)]
fn read_at(f: &File, buf: &mut [u8], off: u64) -> std::io::Result<()> {
    use std::os::unix::fs::FileExt;
    f.read_exact_at(buf, off)
}

/// Decoded bytes of one resource.
pub fn read_resource(f: &File, e: &Entry) -> Result<Vec<u8>, String> {
    let mut buf = vec![0u8; e.stored_size() as usize];
    read_at(f, &mut buf, e.offset).map_err(|err| format!("resource {:016X}: {err}", e.hash))?;
    if e.scrambled {
        for (i, b) in buf.iter_mut().enumerate() {
            *b ^= XOR[i % 8];
        }
    }
    if e.compressed != 0 {
        let out = lz4_flex::block::decompress(&buf, e.data_size as usize).map_err(|err| format!("resource {:016X}: LZ4 {err}", e.hash))?;
        return Ok(out);
    }
    Ok(buf)
}

/// The `.meta` file RPKG tools write next to a resource (layout read by `sources/glacier/meta.py`).
pub fn meta_bytes(e: &Entry) -> Vec<u8> {
    let refs_size = if e.refs.is_empty() { 0 } else { 4 + e.refs.len() * 9 };
    let mut m = Vec::with_capacity(44 + refs_size);
    m.extend(e.hash.to_le_bytes());
    m.extend((e.offset as u32).to_le_bytes());
    m.extend(0u32.to_le_bytes());
    m.extend((e.compressed | if e.scrambled { 0x8000_0000 } else { 0 }).to_le_bytes());
    m.extend(e.type_name().as_bytes());
    m.extend((refs_size as u32).to_le_bytes());
    m.extend(e.data_size.to_le_bytes());
    m.extend(e.sys_mem.to_le_bytes());
    m.extend(e.vid_mem.to_le_bytes());
    if !e.refs.is_empty() {
        m.extend((e.refs.len() as u32 | 0xC000_0000).to_le_bytes());
        m.extend(e.refs.iter().map(|r| r.1));
        for r in &e.refs {
            m.extend(r.0.to_le_bytes());
        }
    }
    m
}

fn write_atomic(path: &Path, data: &[u8]) -> Result<(), String> {
    if let Some(d) = path.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension(format!("{}.part", std::process::id()));
    let mut f = File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(data).map_err(|e| e.to_string())?;
    drop(f);
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

#[derive(Default, Debug)]
pub struct Stats {
    pub written: u64,
    pub skipped: u64,
    pub removed: u64,
    pub bytes: u64,
    pub errors: Vec<String>,
    pub by_type: HashMap<String, u64>,
}

/// Extract the resources of `types` (empty = all) of the packages `paths` into `<root>/chunk<N>/<TYPE>/`.
/// Packages are applied in order (chunk, patch): a later one overwrites earlier resources and removes the ones its
/// patch lists as unneeded.
pub fn extract(paths: &[PathBuf], root: &Path, types: &HashSet<String>) -> Result<Stats, String> {
    let mut pkgs: Vec<Package> = Vec::new();
    for p in paths {
        pkgs.push(open(p)?);
    }
    pkgs.sort_by_key(|p| name_ids(&p.path).unwrap_or((p.chunk_id as u32, p.patch_id as u32)));
    DONE.store(0, Ordering::SeqCst);
    CANCEL.store(false, Ordering::SeqCst);
    let wanted = |e: &Entry| types.is_empty() || types.contains(&e.type_name());
    TOTAL.store(pkgs.iter().map(|p| p.entries.iter().filter(|e| wanted(e)).count() as u64).sum(), Ordering::SeqCst);
    let mut st = Stats::default();
    for pkg in &pkgs {
        let (chunk, _) = name_ids(&pkg.path).unwrap_or((pkg.chunk_id as u32, 0));
        let dir = root.join(format!("chunk{chunk}"));
        // resources a patch declares unneeded
        if !pkg.unneeded.is_empty() {
            let gone: HashSet<u64> = pkg.unneeded.iter().copied().collect();
            if let Ok(rd) = fs::read_dir(&dir) {
                for t in rd.flatten() {
                    if let Ok(files) = fs::read_dir(t.path()) {
                        for f in files.flatten() {
                            let n = f.file_name().to_string_lossy().into_owned();
                            if let Some(h) = n.get(..16).and_then(|h| u64::from_str_radix(h, 16).ok()) {
                                if gone.contains(&h) {
                                    let _ = fs::remove_file(f.path());
                                    st.removed += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        let file = File::open(&pkg.path).map_err(|e| e.to_string())?;
        let is_patch = name_ids(&pkg.path).map(|(_, p)| p > 0).unwrap_or(false);
        let results: Vec<Result<(String, u64, bool), String>> = pkg
            .entries
            .par_iter()
            .filter(|e| wanted(e))
            .map(|e| {
                if CANCEL.load(Ordering::Relaxed) {
                    return Err("cancelled".into());
                }
                let ty = e.type_name();
                let target = dir.join(&ty).join(format!("{:016X}.{ty}", e.hash));
                // a base resource of the right size is already there (resume); a patch always rewrites
                let size_ok = fs::metadata(&target).map(|m| m.len() == e.data_size as u64).unwrap_or(false);
                let meta_ok = fs::metadata(format!("{}.meta", target.display())).is_ok();
                let r = if size_ok && meta_ok && !is_patch {
                    Ok((ty, 0, true))
                } else {
                    read_resource(&file, e).and_then(|data| {
                        write_atomic(&target, &data)?;
                        write_atomic(Path::new(&format!("{}.meta", target.display())), &meta_bytes(e))?;
                        Ok((ty, data.len() as u64, false))
                    })
                };
                DONE.fetch_add(1, Ordering::Relaxed);
                r
            })
            .collect();
        for r in results {
            match r {
                Ok((ty, n, skipped)) => {
                    if skipped {
                        st.skipped += 1;
                    } else {
                        st.written += 1;
                        st.bytes += n;
                        *st.by_type.entry(ty).or_insert(0) += 1;
                    }
                }
                Err(e) if e == "cancelled" => return Err("cancelled".into()),
                Err(e) => {
                    if st.errors.len() < 50 {
                        st.errors.push(e);
                    }
                }
            }
        }
    }
    Ok(st)
}

// ------------------------------------------------------------------------------------------------ tests
#[cfg(test)]
mod tests {
    use super::*;

    struct Res {
        hash: u64,
        ty: &'static str,
        data: Vec<u8>,
        refs: Vec<(u64, u8)>,
        scramble: bool,
        compress: bool,
    }

    /// A v2 package with the layout described above (headers with the states-size field).
    fn build(res: &[Res], patch_unneeded: &[u64], states: bool) -> Vec<u8> {
        let mut blobs: Vec<Vec<u8>> = Vec::new();
        for r in res {
            let mut b = if r.compress { lz4_flex::block::compress(&r.data) } else { r.data.clone() };
            if r.scramble {
                for (i, x) in b.iter_mut().enumerate() {
                    *x ^= XOR[i % 8];
                }
            }
            blobs.push(b);
        }
        let mut meta = Vec::new();
        for r in res {
            let mut t = r.ty.as_bytes().to_vec();
            t.reverse();
            meta.extend(&t);
            let refs_size = if r.refs.is_empty() { 0 } else { 4 + r.refs.len() * 9 };
            meta.extend((refs_size as u32).to_le_bytes());
            if states {
                meta.extend(0u32.to_le_bytes());
            }
            meta.extend((r.data.len() as u32).to_le_bytes());
            meta.extend(0u32.to_le_bytes());
            meta.extend(0xFFFF_FFFFu32.to_le_bytes());
            if !r.refs.is_empty() {
                meta.extend((r.refs.len() as u32 | 0xC000_0000).to_le_bytes());
                meta.extend(r.refs.iter().map(|x| x.1));
                for x in &r.refs {
                    meta.extend(x.0.to_le_bytes());
                }
            }
        }
        let unneeded_len = if patch_unneeded.is_empty() { 0 } else { 4 + 8 * patch_unneeded.len() };
        let table = res.len() * 20;
        let header = 4 + 9 + 12;
        let data_start = header + unneeded_len + table + meta.len();
        let mut out = b"2KPR".to_vec();
        out.extend([0, 0, 0, 0, 0, 0, if patch_unneeded.is_empty() { 0 } else { 1 }, b'x', b'x']);
        out.extend((res.len() as u32).to_le_bytes());
        out.extend((table as u32).to_le_bytes());
        out.extend((meta.len() as u32).to_le_bytes());
        if !patch_unneeded.is_empty() {
            out.extend((patch_unneeded.len() as u32).to_le_bytes());
            for h in patch_unneeded {
                out.extend(h.to_le_bytes());
            }
        }
        let mut off = data_start as u64;
        for (r, b) in res.iter().zip(&blobs) {
            out.extend(r.hash.to_le_bytes());
            out.extend(off.to_le_bytes());
            let flags = if r.compress { b.len() as u32 } else { 0 } | if r.scramble { 0x8000_0000 } else { 0 };
            out.extend(flags.to_le_bytes());
            off += b.len() as u64;
        }
        out.extend(&meta);
        for b in &blobs {
            out.extend(b);
        }
        out
    }

    fn sample() -> Vec<Res> {
        vec![
            Res { hash: 0x0100_0000_0000_0001, ty: "PRIM", data: (0..5000u32).flat_map(|i| (i % 251).to_le_bytes()).collect(), refs: vec![(0x0100_0000_0000_00AA, 0x5F), (0x0100_0000_0000_00BB, 0x1F)], scramble: true, compress: true },
            Res { hash: 0x0100_0000_0000_0002, ty: "TEXT", data: b"plain data, neither packed nor scrambled".to_vec(), refs: vec![], scramble: false, compress: false },
            Res { hash: 0x0100_0000_0000_0003, ty: "GFXV", data: vec![7; 100], refs: vec![], scramble: true, compress: false },
        ]
    }

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("omni_rpkg_{name}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    #[test]
    fn extracts_filters_and_writes_meta() {
        for states in [false, true] {
            let dir = tmp(&format!("a{states}"));
            let pkg = dir.join("chunk0.rpkg");
            let res = sample();
            fs::write(&pkg, build(&res, &[], states)).unwrap();
            let p = open(&pkg).unwrap();
            assert_eq!(p.entries.len(), 3);
            assert_eq!(p.entries[0].type_name(), "PRIM");
            assert_eq!(p.entries[0].refs.len(), 2);
            let out = dir.join("Sorted");
            let types: HashSet<String> = ["PRIM", "TEXT"].iter().map(|s| s.to_string()).collect();
            let st = extract(&[pkg.clone()], &out, &types).unwrap();
            assert_eq!(st.written, 2, "{:?}", st.errors);
            assert!(st.errors.is_empty());
            let prim = fs::read(out.join("chunk0/PRIM/0100000000000001.PRIM")).unwrap();
            assert_eq!(prim, res[0].data);
            assert_eq!(fs::read(out.join("chunk0/TEXT/0100000000000002.TEXT")).unwrap(), res[1].data);
            assert!(!out.join("chunk0/GFXV").exists());
            let meta = fs::read(out.join("chunk0/PRIM/0100000000000001.PRIM.meta")).unwrap();
            assert_eq!(&meta[20..24], b"PRIM");
            assert_eq!(u32::from_le_bytes(meta[24..28].try_into().unwrap()), 22);
            assert_eq!(u32::from_le_bytes(meta[40..44].try_into().unwrap()) & 0x3FFF_FFFF, 2);
            assert_eq!(meta[44], 0x5F);
            assert_eq!(u64::from_le_bytes(meta[46..54].try_into().unwrap()), 0x0100_0000_0000_00AA);
            // a second run keeps everything
            let again = extract(&[pkg], &out, &types).unwrap();
            assert_eq!((again.written, again.skipped), (0, 2));
        }
    }

    #[test]
    fn patch_overwrites_and_removes() {
        let dir = tmp("p");
        let base = dir.join("chunk0.rpkg");
        fs::write(&base, build(&sample(), &[], true)).unwrap();
        let newer = vec![Res { hash: 0x0100_0000_0000_0002, ty: "TEXT", data: b"patched".to_vec(), refs: vec![], scramble: false, compress: false }];
        let patch = dir.join("chunk0patch1.rpkg");
        fs::write(&patch, build(&newer, &[0x0100_0000_0000_0001], true)).unwrap();
        let out = dir.join("Sorted");
        let st = extract(&[patch, base], &out, &HashSet::new()).unwrap();
        assert!(st.errors.is_empty(), "{:?}", st.errors);
        assert_eq!(fs::read(out.join("chunk0/TEXT/0100000000000002.TEXT")).unwrap(), b"patched");
        assert!(!out.join("chunk0/PRIM/0100000000000001.PRIM").exists());
        assert!(out.join("chunk0/GFXV/0100000000000003.GFXV").exists());
    }

    #[test]
    fn rejects_garbage() {
        let dir = tmp("g");
        let f = dir.join("chunk9.rpkg");
        fs::write(&f, b"not a package at all").unwrap();
        assert!(open(&f).is_err());
        assert_eq!(name_ids(Path::new("x/chunk12patch3.rpkg")), Some((12, 3)));
        assert_eq!(name_ids(Path::new("chunk0.rpkg")), Some((0, 0)));
    }
}
