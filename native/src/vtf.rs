//! Source textures: mip chains built from the full-size image, DXT/BGRA encoding, VTF 7.2 writing and reading,
//! PNG previews.
//!
//! Mips are made from the top image, each level a 2x2 box of the previous one, filtered the way the data means:
//!   srgb    colour maps: averaged in linear light (a plain average darkens high-contrast detail), alpha linear
//!   linear  masks / data maps (roughness, metal, AO, exponents): plain average
//!   normal  tangent-space normals: averaged as vectors then renormalised (keeps the bumps' strength in the
//!           distance), alpha (specular mask) linear
//! Alpha-tested maps can keep their coverage: each level's alpha is rescaled so the share of pixels passing the
//! test stays the one of the full-size image (thin strands and leaves do not vanish with distance).

use crate::par::*;
use std::fs;
use std::path::Path;

pub const DXT1: u32 = 13;
pub const DXT3: u32 = 14;
pub const DXT5: u32 = 15;
pub const BGRA8888: u32 = 12;
pub const DXT1A: u32 = 20;
pub const FLAG_EIGHTBITALPHA: u32 = 0x2000;
pub const FLAG_ONEBITALPHA: u32 = 0x1000;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Srgb,
    Linear,
    Normal,
}

impl Kind {
    pub fn parse(s: &str) -> Result<Kind, String> {
        Ok(match s {
            "srgb" | "color" | "colour" => Kind::Srgb,
            "linear" | "data" | "mask" => Kind::Linear,
            "normal" => Kind::Normal,
            _ => return Err(format!("unknown mip kind {s}")),
        })
    }
}

fn to_lin_table() -> &'static [f32; 256] {
    static T: std::sync::OnceLock<[f32; 256]> = std::sync::OnceLock::new();
    T.get_or_init(|| {
        let mut t = [0f32; 256];
        for (i, e) in t.iter_mut().enumerate() {
            let c = i as f32 / 255.0;
            *e = if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) };
        }
        t
    })
}

fn to_srgb8(l: f32) -> u8 {
    let l = l.clamp(0.0, 1.0);
    let c = if l <= 0.003_130_8 { l * 12.92 } else { 1.055 * l.powf(1.0 / 2.4) - 0.055 };
    (c * 255.0 + 0.5) as u8
}

/// Byte length of a `w` x `h` RGBA8 image, or an error when the size is empty, absurd or does not fit `have` bytes.
pub fn rgba_len(w: usize, h: usize, have: usize) -> Result<usize, String> {
    let n = w
        .checked_mul(h)
        .and_then(|px| px.checked_mul(4))
        .filter(|&n| w > 0 && h > 0 && n <= 1 << 31)
        .ok_or_else(|| format!("invalid image size {w}x{h}"))?;
    if have < n {
        return Err("image buffer too small".into());
    }
    Ok(n)
}

/// Next mip level (each output pixel = the 1, 2 or 4 source pixels it covers).
pub fn half(src: &[u8], w: usize, h: usize, kind: Kind) -> (Vec<u8>, usize, usize) {
    if rgba_len(w, h, src.len()).is_err() {
        return (vec![0, 0, 0, 255], 1, 1);                 // callers validate; this only keeps a bad one from panicking
    }
    let (nw, nh) = ((w / 2).max(1), (h / 2).max(1));
    let lin = to_lin_table();
    let mut out = vec![0u8; nw * nh * 4];
    out.par_chunks_mut(nw * 4).enumerate().for_each(|(y, row)| {
        let ys = if h > 1 { [2 * y, (2 * y + 1).min(h - 1)] } else { [0, 0] };
        for x in 0..nw {
            let xs = if w > 1 { [2 * x, (2 * x + 1).min(w - 1)] } else { [0, 0] };
            let mut acc = [0f32; 4];
            for &sy in &ys {
                for &sx in &xs {
                    let p = &src[(sy * w + sx) * 4..(sy * w + sx) * 4 + 4];
                    match kind {
                        Kind::Srgb => {
                            acc[0] += lin[p[0] as usize];
                            acc[1] += lin[p[1] as usize];
                            acc[2] += lin[p[2] as usize];
                        }
                        Kind::Linear => {
                            acc[0] += p[0] as f32;
                            acc[1] += p[1] as f32;
                            acc[2] += p[2] as f32;
                        }
                        Kind::Normal => {
                            acc[0] += p[0] as f32 / 127.5 - 1.0;
                            acc[1] += p[1] as f32 / 127.5 - 1.0;
                            acc[2] += p[2] as f32 / 127.5 - 1.0;
                        }
                    }
                    acc[3] += p[3] as f32;
                }
            }
            let o = &mut row[x * 4..x * 4 + 4];
            match kind {
                Kind::Srgb => {
                    for c in 0..3 {
                        o[c] = to_srgb8(acc[c] / 4.0);
                    }
                }
                Kind::Linear => {
                    for c in 0..3 {
                        o[c] = (acc[c] / 4.0 + 0.5) as u8;
                    }
                }
                Kind::Normal => {
                    let n = (acc[0] * acc[0] + acc[1] * acc[1] + acc[2] * acc[2]).sqrt().max(1e-6);
                    for c in 0..3 {
                        o[c] = ((acc[c] / n * 0.5 + 0.5) * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
                    }
                }
            }
            o[3] = (acc[3] / 4.0 + 0.5) as u8;
        }
    });
    (out, nw, nh)
}

/// Full chain, largest first; the top is halved until it fits `max_size` (0 = no limit).
pub fn mip_chain(rgba: &[u8], w: usize, h: usize, kind: Kind, max_size: usize, coverage: f32) -> Vec<(usize, usize, Vec<u8>)> {
    let mut cur = (rgba.to_vec(), w, h);
    while max_size > 0 && cur.1.max(cur.2) > max_size {
        cur = half(&cur.0, cur.1, cur.2, kind);
    }
    let mut out = vec![(cur.1, cur.2, cur.0)];
    loop {
        let (lw, lh) = {
            let l = out.last().unwrap();
            (l.0, l.1)
        };
        if lw == 1 && lh == 1 {
            break;
        }
        let (n, nw, nh) = half(&out.last().unwrap().2, lw, lh, kind);
        out.push((nw, nh, n));
    }
    if coverage > 0.0 {
        keep_coverage(&mut out, coverage);
    }
    out
}

fn keep_coverage(mips: &mut [(usize, usize, Vec<u8>)], reference: f32) {
    let cut = reference * 255.0;
    let share = |a: &[u8]| a.chunks_exact(4).filter(|p| p[3] as f32 >= cut).count() as f32 / (a.len() / 4).max(1) as f32;
    let target = share(&mips[0].2);
    if target <= 0.0 || target >= 1.0 {
        return;
    }
    for m in mips.iter_mut().skip(1) {
        let mut al: Vec<u8> = m.2.chunks_exact(4).map(|p| p[3]).collect();
        al.sort_unstable();
        let q = ((1.0 - target) * (al.len() - 1) as f32).round() as usize;
        let t = al[q.min(al.len() - 1)] as f32;
        if t > 1.0 {
            for p in m.2.chunks_exact_mut(4) {
                p[3] = (p[3] as f32 * (cut / t)).clamp(0.0, 255.0) as u8;
            }
        }
    }
}

/// Average colour of an image in linear light: the VTF reflectivity (used by VRAD for bounced light).
pub fn reflectivity(rgba: &[u8], kind: Kind) -> [f32; 3] {
    if kind != Kind::Srgb {
        return [0.5, 0.5, 0.5];
    }
    let lin = to_lin_table();
    let n = (rgba.len() / 4).max(1) as f64;
    let mut s = [0f64; 3];
    for p in rgba.chunks_exact(4) {
        for c in 0..3 {
            s[c] += lin[p[c] as usize] as f64;
        }
    }
    [(s[0] / n) as f32, (s[1] / n) as f32, (s[2] / n) as f32]
}

/// VTF 7.2 file bytes: 80-byte header, no low-res thumbnail, mips smallest first.
pub fn vtf_bytes(fmt: u32, mips: &[(usize, usize, Vec<u8>)], flags: u32, refl: [f32; 3]) -> Vec<u8> {
    let (w, h) = (mips[0].0 as u16, mips[0].1 as u16);
    let mut hdr = Vec::with_capacity(80);
    hdr.extend(b"VTF\0");
    hdr.extend(7u32.to_le_bytes());
    hdr.extend(2u32.to_le_bytes());
    hdr.extend(80u32.to_le_bytes());
    hdr.extend(w.to_le_bytes());
    hdr.extend(h.to_le_bytes());
    hdr.extend(flags.to_le_bytes());
    hdr.extend(1u16.to_le_bytes()); // frames
    hdr.extend(0u16.to_le_bytes()); // first frame
    hdr.extend([0u8; 4]);
    for r in refl {
        hdr.extend(r.to_le_bytes());
    }
    hdr.extend([0u8; 4]);
    hdr.extend(1f32.to_le_bytes()); // bumpmap scale
    hdr.extend(fmt.to_le_bytes());
    hdr.push(mips.len() as u8);
    hdr.extend(0xFFFF_FFFFu32.to_le_bytes()); // no low-res thumbnail
    hdr.push(0);
    hdr.push(0);
    hdr.extend(1u16.to_le_bytes()); // depth
    hdr.resize(80, 0);
    let total: usize = mips.iter().map(|m| m.2.len()).sum();
    let mut data = hdr;
    data.reserve(total);
    for m in mips.iter().rev() {
        data.extend_from_slice(&m.2);
    }
    data
}

pub fn write_vtf(path: &Path, fmt: u32, mips: &[(usize, usize, Vec<u8>)], flags: u32, refl: [f32; 3]) -> Result<(), String> {
    let data = vtf_bytes(fmt, mips, flags, refl);
    if let Some(d) = path.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    // atomic: parallel workers may write the same shared texture at the same time
    let tmp = path.with_extension(format!("{}.tmp", std::process::id()));
    fs::write(&tmp, &data).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// Build the mip chain of `rgba`, encode it and write the VTF. `format`: dxt1 | dxt5 | bgra8888.
#[allow(clippy::too_many_arguments)]
pub fn encode_vtf(
    path: &Path,
    rgba: &[u8],
    w: usize,
    h: usize,
    format: &str,
    kind: Kind,
    max_size: usize,
    flags: u32,
    quality: u8,
    coverage: f32,
) -> Result<(usize, usize), String> {
    let (data, tw, th) = encode_vtf_bytes(rgba, w, h, format, kind, max_size, flags, quality, coverage)?;
    if let Some(d) = path.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension(format!("{}.tmp", std::process::id()));
    fs::write(&tmp, &data).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())?;
    Ok((tw, th))
}

/// Same as `encode_vtf`, returning the file bytes and the top mip size.
#[allow(clippy::too_many_arguments)]
pub fn encode_vtf_bytes(
    rgba: &[u8],
    w: usize,
    h: usize,
    format: &str,
    kind: Kind,
    max_size: usize,
    flags: u32,
    quality: u8,
    coverage: f32,
) -> Result<(Vec<u8>, usize, usize), String> {
    let n = rgba_len(w, h, rgba.len())?;
    let chain = mip_chain(&rgba[..n], w, h, kind, max_size, coverage);
    let refl = reflectivity(&chain[chain.len().saturating_sub(4).min(chain.len() - 1)].2, kind);
    let (fmt, mut flags) = match format {
        "dxt1" => (DXT1, flags),
        "dxt5" => (DXT5, flags | FLAG_EIGHTBITALPHA),
        "bgra8888" => (BGRA8888, flags | FLAG_EIGHTBITALPHA),
        _ => return Err(format!("unknown VTF format {format}")),
    };
    if kind == Kind::Normal {
        flags |= 0x80;
    }
    let encoded: Vec<(usize, usize, Vec<u8>)> = chain
        .par_iter()
        .map(|(mw, mh, px)| {
            let data = match fmt {
                BGRA8888 => px.chunks_exact(4).flat_map(|p| [p[2], p[1], p[0], p[3]]).collect(),
                _ => crate::texture::encode_dxt(px, *mw, *mh, fmt == DXT5, quality, kind == Kind::Normal),
            };
            (*mw, *mh, data)
        })
        .collect();
    Ok((vtf_bytes(fmt, &encoded, flags, refl), chain[0].0, chain[0].1))
}

// ------------------------------------------------------------------------------------------------ reading

/// `v >> i` that does not panic for i >= 64 (mip counts come from the file).
fn shr(v: usize, i: usize) -> usize {
    v.checked_shr(i as u32).unwrap_or(0)
}

fn mip_bytes(fmt: u32, w: usize, h: usize) -> Option<usize> {
    let blocks = w.div_ceil(4).max(1) * h.div_ceil(4).max(1);
    Some(match fmt {
        13 | 20 => blocks * 8,
        14 | 15 => blocks * 16,
        0 | 1 | 11 | 12 | 16 | 23 | 26 => w * h * 4,
        2 | 3 => w * h * 3,
        4 | 6 | 17 | 18 | 19 | 21 | 22 => w * h * 2,
        5 | 7 | 8 => w * h,
        24 | 25 => w * h * 8,
        _ => return None,
    })
}

/// First frame of the largest mip not larger than `max_dim`, as RGBA8.
pub fn decode_vtf(b: &[u8], max_dim: usize) -> Result<(usize, usize, Vec<u8>), String> {
    if b.len() < 64 || &b[..4] != b"VTF\0" {
        return Err("not a VTF".into());
    }
    let rd16 = |o: usize| u16::from_le_bytes([b[o], b[o + 1]]) as usize;
    let rd32 = |o: usize| u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]]);
    let minor = rd32(8);
    let hdr = rd32(12) as usize;
    let (w, h) = (rd16(16), rd16(18));
    let flags = rd32(20);
    let frames = rd16(24).max(1);
    let fmt = rd32(52);
    let nmips = b[56] as usize;
    let lowfmt = rd32(57);
    let (lw, lh) = (b[61] as usize, b[62] as usize);
    let faces = if flags & 0x4000 != 0 { 6 } else { 1 };
    let mut off = hdr + if lowfmt != 0xFFFF_FFFF && lw > 0 && lh > 0 { mip_bytes(lowfmt, lw, lh).unwrap_or(0) } else { 0 };
    // 7.3+: the resource directory gives the high-res data offset
    if minor >= 3 && b.len() >= 80 {
        let nres = rd32(68) as usize;
        for i in 0..nres.min(32) {
            let o = 80 + i * 8;
            if o + 8 > b.len() {
                break;
            }
            if b[o..o + 3] == [0x30, 0, 0] {
                off = rd32(o + 4) as usize;
            }
        }
    }
    let fmt_size = |w, h| mip_bytes(fmt, w, h).ok_or_else(|| format!("unsupported VTF format {fmt}"));
    let sizes: Vec<(usize, usize)> = (0..nmips.clamp(1, 32)).map(|i| (shr(w, i).max(1), shr(h, i).max(1))).collect();
    let mut start = vec![0usize; sizes.len()];
    for i in (0..sizes.len()).rev() {
        start[i] = off;
        off += fmt_size(sizes[i].0, sizes[i].1)? * frames * faces;
    }
    let level = sizes.iter().position(|s| s.0.max(s.1) <= max_dim).unwrap_or(sizes.len() - 1);
    let (mw, mh) = sizes[level];
    let n = fmt_size(mw, mh)?;
    let d = b.get(start[level]..start[level] + n).ok_or("truncated VTF")?;
    let px = match fmt {
        13 | 20 => crate::texture::to_rgba("BC1", mw as u32, mh as u32, d)?,
        14 => crate::texture::to_rgba("BC2", mw as u32, mh as u32, d)?,
        15 => crate::texture::to_rgba("BC3", mw as u32, mh as u32, d)?,
        _ => {
            let mut out = vec![255u8; mw * mh * 4];
            for (i, o) in out.chunks_exact_mut(4).enumerate() {
                let p = |k: usize, s: usize| d[i * s + k];
                match fmt {
                    0 => o.copy_from_slice(&d[i * 4..i * 4 + 4]),
                    1 => o.copy_from_slice(&[p(3, 4), p(2, 4), p(1, 4), p(0, 4)]),
                    11 => o.copy_from_slice(&[p(1, 4), p(2, 4), p(3, 4), p(0, 4)]),
                    12 => o.copy_from_slice(&[p(2, 4), p(1, 4), p(0, 4), p(3, 4)]),
                    16 => o.copy_from_slice(&[p(2, 4), p(1, 4), p(0, 4), 255]),
                    2 => o.copy_from_slice(&[p(0, 3), p(1, 3), p(2, 3), 255]),
                    3 => o.copy_from_slice(&[p(2, 3), p(1, 3), p(0, 3), 255]),
                    5 => o.copy_from_slice(&[p(0, 1), p(0, 1), p(0, 1), 255]),
                    6 => o.copy_from_slice(&[p(0, 2), p(0, 2), p(0, 2), p(1, 2)]),
                    8 => o.copy_from_slice(&[255, 255, 255, p(0, 1)]),
                    22 => o.copy_from_slice(&[p(0, 2), p(1, 2), 255, 255]),
                    _ => return Err(format!("unsupported VTF format {fmt}")),
                }
            }
            out
        }
    };
    Ok((mw, mh, px))
}

// ------------------------------------------------------------------------------------------------ PNG

/// RGBA8 -> PNG. `channel`: rgb (opaque), rgba, or one of r g b a shown as grey. `max_dim` downsizes (box).
pub fn png(rgba: &[u8], w: usize, h: usize, channel: &str, max_dim: usize) -> Result<Vec<u8>, String> {
    let n = rgba_len(w, h, rgba.len())?;
    let mut cur = (rgba[..n].to_vec(), w, h);
    while max_dim > 0 && cur.1.max(cur.2) > max_dim {
        cur = half(&cur.0, cur.1, cur.2, Kind::Linear);
    }
    let (mut px, w, h) = cur;
    match channel {
        "rgba" => {}
        "rgb" => px.chunks_exact_mut(4).for_each(|p| p[3] = 255),
        "r" | "g" | "b" | "a" => {
            let i = "rgba".find(channel).unwrap();
            px.chunks_exact_mut(4).for_each(|p| {
                let v = p[i];
                p.copy_from_slice(&[v, v, v, 255]);
            });
        }
        _ => return Err(format!("unknown channel {channel}")),
    }
    Ok(encode_png(&px, w, h))
}

fn crc32(d: &[u8]) -> u32 {
    static T: std::sync::OnceLock<[u32; 256]> = std::sync::OnceLock::new();
    let t = T.get_or_init(|| {
        let mut t = [0u32; 256];
        for (i, e) in t.iter_mut().enumerate() {
            let mut c = i as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
            }
            *e = c;
        }
        t
    });
    let mut c = 0xFFFF_FFFFu32;
    for &b in d {
        c = t[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFF_FFFF
}

/// RGBA8 PNG: "Sub" row filter + zlib at a fast level, enough for previews.
pub fn encode_png(px: &[u8], w: usize, h: usize) -> Vec<u8> {
    let stride = w * 4;
    let mut raw = Vec::with_capacity((stride + 1) * h);
    for y in 0..h {
        let row = &px[y * stride..(y + 1) * stride];
        raw.push(1);
        for x in 0..stride {
            let left = if x >= 4 { row[x - 4] } else { 0 };
            raw.push(row[x].wrapping_sub(left));
        }
    }
    let z = miniz_oxide::deflate::compress_to_vec_zlib(&raw, 3);
    let mut out = vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A];
    let mut chunk = |kind: &[u8; 4], data: &[u8]| {
        out.extend((data.len() as u32).to_be_bytes());
        let mut c = kind.to_vec();
        c.extend_from_slice(data);
        out.extend(&c);
        out.extend(crc32(&c).to_be_bytes());
    };
    let mut ihdr = Vec::new();
    ihdr.extend((w as u32).to_be_bytes());
    ihdr.extend((h as u32).to_be_bytes());
    ihdr.extend([8, 6, 0, 0, 0]);
    chunk(b"IHDR", &ihdr);
    chunk(b"IDAT", &z);
    chunk(b"IEND", &[]);
    out
}

#[cfg(test)]
mod guard_tests {
    use super::*;

    #[test]
    fn empty_and_oversized_images_are_errors() {
        assert!(rgba_len(0, 4, 100).is_err());
        assert!(rgba_len(4, 0, 100).is_err());
        assert!(rgba_len(usize::MAX, 2, 100).is_err());
        assert!(rgba_len(4, 4, 10).is_err());
        assert_eq!(rgba_len(2, 2, 16), Ok(16));
        assert!(png(&[], 0, 4, "rgba", 0).is_err());
        assert_eq!(half(&[], 0, 0, Kind::Linear).1, 1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chain_reaches_one_pixel() {
        let img = vec![200u8; 16 * 8 * 4];
        let c = mip_chain(&img, 16, 8, Kind::Srgb, 0, 0.0);
        assert_eq!(c.len(), 5);
        assert_eq!((c[4].0, c[4].1), (1, 1));
        assert_eq!(c[4].2[0], 200);
    }

    #[test]
    fn normal_mips_stay_unit() {
        let mut img = vec![0u8; 4 * 4 * 4];
        for (i, p) in img.chunks_exact_mut(4).enumerate() {
            p.copy_from_slice(&if i % 2 == 0 { [200, 128, 200, 255] } else { [56, 128, 200, 255] });
        }
        let c = mip_chain(&img, 4, 4, Kind::Normal, 0, 0.0);
        let p = &c[1].2[..4];
        let v: Vec<f32> = p[..3].iter().map(|&x| x as f32 / 127.5 - 1.0).collect();
        let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        assert!((n - 1.0).abs() < 0.05, "{n}");
    }
}
