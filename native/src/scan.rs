//! Bulk reads of many small files. Opening a file costs milliseconds on a machine with a real-time antivirus, so
//! the catalogs of a game (tens of thousands of resources) are scanned by many threads at once.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use crate::par::*;

/// (size, header flags) of a PRIM resource: the u64 at the start gives the offset of the mesh header, whose last
/// four bytes are the property flags (skinned = 0b1000, linked = 0b100). `None` when the file cannot be read.
pub fn prim_header(path: &Path) -> Option<(u64, u32)> {
    let mut f = File::open(path).ok()?;
    let size = f.metadata().ok()?.len();
    let mut b = [0u8; 8];
    f.read_exact(&mut b).ok()?;
    let ho = u64::from_le_bytes(b);
    if ho.checked_add(8)? > size {
        return Some((size, 0));
    }
    f.seek(SeekFrom::Start(ho)).ok()?;
    f.read_exact(&mut b).ok()?;
    Some((size, u32::from_le_bytes([b[4], b[5], b[6], b[7]])))
}

pub fn prim_headers(paths: &[String]) -> Vec<Option<(u64, u32)>> {
    paths.par_iter().map(|p| prim_header(Path::new(p))).collect()
}

/// `(hash, flag)` of every reference of a `.meta` file; the flag's low bits are the language slot of a dialogue
/// reference. `None` when the data is truncated.
pub fn meta_refs_flags(data: &[u8]) -> Option<Vec<(u64, u8)>> {
    let refs_size = u32::from_le_bytes(data.get(24..28)?.try_into().ok()?);
    let mut out = Vec::new();
    if refs_size > 0 {
        let cnt = u16::from_le_bytes(data.get(40..42)?.try_into().ok()?) as usize;
        let flags = data.get(44..44 + cnt)?;
        let base = 44 + cnt;
        for (i, &f) in flags.iter().enumerate() {
            out.push((u64::from_le_bytes(data.get(base + i * 8..base + i * 8 + 8)?.try_into().ok()?), f));
        }
    }
    Some(out)
}

pub fn meta_refs_flags_many(paths: &[String]) -> Vec<Option<Vec<(u64, u8)>>> {
    paths.par_iter().map(|p| std::fs::read(format!("{p}.meta")).ok().and_then(|d| meta_refs_flags(&d))).collect()
}

/// Whole files, read in parallel (`None` for a file that cannot be read).
pub fn read_files(paths: &[String]) -> Vec<Option<Vec<u8>>> {
    paths.par_iter().map(|p| std::fs::read(p).ok()).collect()
}

/// Original Wwise name of a .wem stored at `file[offset..offset + size]` (`size < 0`: up to the RIFF length): the
/// `labl` entry of its `LIST` chunk, read from the chunk headers only. Only file-name-like labels are kept; the
/// markers of music ("Marker 2", "EXIT C") are not names.
pub fn wem_label(path: &Path, offset: u64, size: i64) -> String {
    raw_label(path, offset, size).filter(|l| filelike(l)).unwrap_or_default()
}

/// Same as [`wem_label`] for a .wem already in memory.
pub fn wem_label_of(data: &[u8]) -> String {
    label_in(&mut std::io::Cursor::new(data), 0, -1).filter(|l| filelike(l)).unwrap_or_default()
}

fn filelike(l: &str) -> bool {
    let b = l.as_bytes();
    let low = l.to_ascii_lowercase();
    b.len() >= 6
        && b[0].is_ascii_alphanumeric()
        && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
        && !low.starts_with("marker")
        && !low.starts_with("cue")
}

fn raw_label(path: &Path, offset: u64, size: i64) -> Option<String> {
    label_in(&mut File::open(path).ok()?, offset, size)
}

pub(crate) fn label_in<R: Read + Seek>(f: &mut R, offset: u64, size: i64) -> Option<String> {
    f.seek(SeekFrom::Start(offset)).ok()?;
    let mut head = [0u8; 12];
    f.read_exact(&mut head).ok()?;
    if &head[..4] != b"RIFF" {
        return None;
    }
    let end = offset.saturating_add(if size >= 0 { size as u64 } else { 8 + u32::from_le_bytes(head[4..8].try_into().ok()?) as u64 });
    let mut o = offset + 12;
    while o.saturating_add(8) <= end {
        f.seek(SeekFrom::Start(o)).ok()?;
        let mut h = [0u8; 8];
        f.read_exact(&mut h).ok()?;
        let sz = u32::from_le_bytes(h[4..8].try_into().ok()?) as u64;
        if &h[..4] == b"LIST" {
            let mut body = vec![0u8; sz.min(2048) as usize];
            let n = f.read(&mut body).ok()?;
            body.truncate(n);
            if let Some(i) = body.windows(4).position(|w| w == b"labl") {
                let n = u32::from_le_bytes(body.get(i + 4..i + 8)?.try_into().ok()?) as usize;
                let text = body.get(i + 12..(i + 8 + n).min(body.len()))?;
                let text = text.split(|&c| c == 0).next().unwrap_or(&[]);
                return Some(text.iter().map(|&c| c as char).collect::<String>().trim().to_string());
            }
        }
        o = o.saturating_add(8 + sz + (sz & 1));
    }
    None
}

pub fn wem_labels(items: &[(String, u64, i64)]) -> Vec<String> {
    items.par_iter().map(|(p, o, s)| wem_label(Path::new(p), *o, *s)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flags_come_from_the_mesh_header() {
        let dir = std::env::temp_dir().join(format!("omni_scan_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut data = 16u64.to_le_bytes().to_vec();
        data.extend([0u8; 8]);
        data.extend([1, 2, 3, 4, 0b1100, 0, 0, 0]);
        let good = dir.join("a.prim");
        std::fs::write(&good, &data).unwrap();
        let truncated = dir.join("b.prim");
        std::fs::write(&truncated, 9999u64.to_le_bytes()).unwrap();
        let r = prim_headers(&[good.display().to_string(), truncated.display().to_string(), dir.join("missing").display().to_string()]);
        assert_eq!(r[0], Some((data.len() as u64, 0b1100)));
        assert_eq!(r[1], Some((8, 0)));
        assert_eq!(r[2], None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn wem(label: &str) -> Vec<u8> {
        let mut list = b"adtl".to_vec();
        list.extend(b"labl");
        list.extend((4 + label.len() as u32 + 1).to_le_bytes());
        list.extend(1u32.to_le_bytes());
        list.extend(label.as_bytes());
        list.push(0);
        let mut body = b"WAVE".to_vec();
        body.extend(b"fmt ");
        body.extend(2u32.to_le_bytes());
        body.extend([1, 0]);
        body.extend(b"LIST");
        body.extend((list.len() as u32).to_le_bytes());
        body.extend(&list);
        let mut d = b"RIFF".to_vec();
        d.extend((body.len() as u32).to_le_bytes());
        d.extend(body);
        d
    }

    #[test]
    fn labels_keep_file_names_and_drop_markers() {
        let dir = std::env::temp_dir().join(format!("omni_lbl_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let named = dir.join("a.wem");
        std::fs::write(&named, wem("vox_cc_line_civ01_001")).unwrap();
        let marker = dir.join("b.wem");
        std::fs::write(&marker, wem("Marker 2")).unwrap();
        let mut prefixed = vec![9u8; 5];
        prefixed.extend(wem("sfx_door_open"));
        let embedded = dir.join("c.bin");
        std::fs::write(&embedded, &prefixed).unwrap();
        let junk = dir.join("d.wem");
        std::fs::write(&junk, b"not a riff").unwrap();
        let s = |p: &Path| p.display().to_string();
        let r = wem_labels(&[(s(&named), 0, -1), (s(&marker), 0, -1), (s(&embedded), 5, (prefixed.len() - 5) as i64), (s(&junk), 0, -1), (s(&dir.join("none")), 0, -1)]);
        assert_eq!(r, vec!["vox_cc_line_civ01_001", "", "sfx_door_open", "", ""]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn meta_references_carry_their_flags() {
        let mut d = vec![0u8; 40];
        d[24] = 1;
        d.extend(2u16.to_le_bytes());
        d.extend([0u8, 0]);
        d.extend([7u8, 9]);
        d.extend(0xAABBu64.to_le_bytes());
        d.extend(0xCCDDu64.to_le_bytes());
        assert_eq!(meta_refs_flags(&d), Some(vec![(0xAABB, 7), (0xCCDD, 9)]));
        assert_eq!(meta_refs_flags(&d[..d.len() - 3]), None);
        assert_eq!(meta_refs_flags(&d[..30]), None);
    }
}
