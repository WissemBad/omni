//! 007 First Light textures: TEXT (+TEXD) containers -> raw BCn mips -> RGBA, and RGBA -> DXT1/DXT5.
//!
//! Container (TextureMapHeaderV4, verified): u16 width/height/format/mips @0x0C, 14 x u32 uncompressed
//! sizes @0x18, 14 x u32 cumulative compressed sizes @0x50, u32 atlas size @0x88, first mip stored in the
//! TEXT @0x91, data @0x98 + atlas. Each mip is an independent LZ4 block (raw when LZ4 did not shrink it);
//! mips before `first_text_mip` live in the TEXD.

use crate::par::*;

#[derive(Clone, Debug)]
pub struct Header {
    pub width: u32,
    pub height: u32,
    pub fmt: u16,
    pub mips: u32,
    pub first_text_mip: u32,
    pub atlas: u32,
    pub comp: [u32; 14],
}

pub fn format_name(fmt: u16) -> Option<&'static str> {
    Some(match fmt {
        0x4C => "BC1",
        0x4F => "BC2",
        0x52 => "BC3",
        0x55 => "BC4",
        0x58 => "BC5",
        0x5E => "BC7",
        0x1C => "RGBA8",
        0x37 => "RG8",
        0x45 => "A8",
        _ => return None,
    })
}

fn rd_u16(d: &[u8], o: usize) -> Result<u16, String> {
    d.get(o..o + 2).map(|b| u16::from_le_bytes([b[0], b[1]])).ok_or_else(|| "truncated".to_string())
}
fn rd_u32(d: &[u8], o: usize) -> Result<u32, String> {
    d.get(o..o + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]])).ok_or_else(|| "truncated".to_string())
}

pub fn parse_header(d: &[u8]) -> Result<Header, String> {
    if d.len() < 0x98 {
        return Err("TEXT too small".into());
    }
    let width = rd_u16(d, 0x0C)? as u32;
    let height = rd_u16(d, 0x0E)? as u32;
    let fmt = rd_u16(d, 0x10)?;
    let mips = rd_u16(d, 0x12)? as u32;
    if !(1..=16384).contains(&width) || !(1..=16384).contains(&height) || !(1..=14).contains(&mips) {
        return Err("invalid TEXT header".into());
    }
    let mut comp = [0u32; 14];
    for (i, c) in comp.iter_mut().enumerate() {
        *c = rd_u32(d, 0x50 + 4 * i)?;
    }
    Ok(Header { width, height, fmt, mips, first_text_mip: d[0x91] as u32, atlas: rd_u32(d, 0x88)?, comp })
}

pub fn mip_nbytes(name: &str, w: u32, h: u32) -> usize {
    let (w, h) = (w as usize, h as usize);
    match name {
        "RGBA8" => w * h * 4,
        "RG8" => w * h * 2,
        "A8" => w * h,
        _ => {
            let blk = if matches!(name, "BC1" | "BC4") { 8 } else { 16 };
            w.div_ceil(4).max(1) * h.div_ceil(4).max(1) * blk
        }
    }
}

/// All decodable mips, largest first: (width, height, raw bytes in the source format).
pub fn decode_mips(text: &[u8], texd: Option<&[u8]>) -> Result<(Header, Vec<(u32, u32, Vec<u8>)>), String> {
    let hd = parse_header(text)?;
    let name = format_name(hd.fmt).ok_or_else(|| format!("unsupported texture format 0x{:02X}", hd.fmt))?;
    let start = 0x98usize + hd.atlas as usize;
    let mut out = Vec::new();
    let mut prev = 0u32;
    let ftm = hd.first_text_mip as usize;
    for i in 0..hd.mips as usize {
        let size_c = hd.comp[i].saturating_sub(prev) as usize;
        prev = hd.comp[i];
        let (w, h) = ((hd.width >> i).max(1), (hd.height >> i).max(1));
        let nb = mip_nbytes(name, w, h);
        let chunk: Option<&[u8]> = if i < ftm {
            match texd {
                None => None,
                Some(td) => {
                    let off = if i > 0 { hd.comp[i - 1] as usize } else { 0 };
                    td.get(off..(off + size_c).min(td.len()))
                }
            }
        } else {
            let base = if ftm > 0 { hd.comp[ftm - 1] as usize } else { 0 };
            let off = start + if i > 0 { (hd.comp[i - 1] as usize).saturating_sub(base) } else { 0 };
            text.get(off.min(text.len())..(off + size_c).min(text.len()))
        };
        let chunk = match chunk {
            Some(c) if size_c > 0 && !c.is_empty() => c,
            _ => continue,
        };
        let mut raw = if chunk.len() >= nb {
            chunk[..nb].to_vec()
        } else {
            match lz4_flex::block::decompress(chunk, nb) {
                Ok(v) => v,
                Err(_) => continue,
            }
        };
        raw.resize(nb, 0);
        out.push((w, h, raw));
    }
    if out.is_empty() {
        return Err("no decodable mip".into());
    }
    Ok((hd, out))
}

type BlockFn = fn(&[u8], usize, usize, &mut [u32]) -> Result<(), &'static str>;

/// Decode one mip to RGBA8 (h*w*4). BC4 -> R replicated, BC5 -> RG (Z left to the caller), like the Python
/// reference implementation. Block rows are decoded in parallel.
pub fn to_rgba(name: &str, w: u32, h: u32, data: &[u8]) -> Result<Vec<u8>, String> {
    let (w, h) = (w as usize, h as usize);
    match name {
        "RGBA8" => return Ok(data.get(..w * h * 4).ok_or("truncated")?.to_vec()),
        "RG8" => {
            let src = data.get(..w * h * 2).ok_or("truncated")?;
            let mut out = vec![0u8; w * h * 4];
            for (o, s) in out.chunks_exact_mut(4).zip(src.chunks_exact(2)) {
                o.copy_from_slice(&[s[0], s[1], 0, 255]);
            }
            return Ok(out);
        }
        "A8" => {
            let src = data.get(..w * h).ok_or("truncated")?;
            let mut out = vec![0u8; w * h * 4];
            for (o, &a) in out.chunks_exact_mut(4).zip(src.iter()) {
                o.copy_from_slice(&[a, a, a, 255]);
            }
            return Ok(out);
        }
        _ => {}
    }
    let (f, blk): (BlockFn, usize) = match name {
        "BC1" => (texture2ddecoder::decode_bc1, 8),
        "BC2" => (texture2ddecoder::decode_bc2, 16),
        "BC3" => (texture2ddecoder::decode_bc3, 16),
        "BC4" => (texture2ddecoder::decode_bc4, 8),
        "BC5" => (texture2ddecoder::decode_bc5, 16),
        "BC7" => (texture2ddecoder::decode_bc7, 16),
        _ => return Err(format!("unsupported format {name}")),
    };
    if w == 0 || h == 0 || w.checked_mul(h).map_or(true, |n| n > 1 << 29) {
        return Err(format!("invalid texture size {w}x{h}"));
    }
    let bw = w.div_ceil(4);
    let row_bytes = bw * blk;
    let rows = h.div_ceil(4);
    if data.len() < rows * row_bytes {
        return Err("not enough block data".into());
    }
    let mut out = vec![0u8; w * h * 4];
    out.par_chunks_mut(w * 4 * 4).enumerate().try_for_each(|(by, dst)| -> Result<(), String> {
        let rh = (h - by * 4).min(4);
        let mut px = vec![0u32; w * rh];
        f(&data[by * row_bytes..(by + 1) * row_bytes], w, rh, &mut px).map_err(|e| e.to_string())?;
        for (o, p) in dst.chunks_exact_mut(4).zip(px.iter()) {
            let [b, g, r, a] = p.to_le_bytes();
            o.copy_from_slice(&[r, g, b, a]);
        }
        Ok(())
    })?;
    Ok(out)
}

/// RGBA8 -> DXT1 (alpha = false) or DXT5. `quality`: 0 = range fit (fast), 1 = cluster fit, 2 = iterative
/// cluster fit (best). Perceptual colour weights for colour maps, uniform ones for normal maps.
pub fn encode_dxt(rgba: &[u8], w: usize, h: usize, alpha: bool, quality: u8, normal: bool) -> Vec<u8> {
    let fmt = if alpha { texpresso::Format::Bc3 } else { texpresso::Format::Bc1 };
    let params = texpresso::Params {
        algorithm: match quality {
            0 => texpresso::Algorithm::RangeFit,
            1 => texpresso::Algorithm::ClusterFit,
            _ => texpresso::Algorithm::IterativeClusterFit,
        },
        weights: if normal { texpresso::COLOUR_WEIGHTS_UNIFORM } else { texpresso::COLOUR_WEIGHTS_PERCEPTUAL },
        weigh_colour_by_alpha: false,
    };
    let mut out = vec![0u8; fmt.compressed_size(w, h)];
    if alpha {
        fmt.compress(rgba, w, h, params, &mut out);
    } else {
        // DXT1 punch-through: the encoder would turn pixels with alpha < 128 into transparent black, but an
        // opaque map's alpha carries other data (AO, masks) -> encode it as fully opaque
        let mut opaque = rgba.to_vec();
        opaque.par_chunks_mut(4).for_each(|p| p[3] = 255);
        fmt.compress(&opaque, w, h, params, &mut out);
    }
    out
}

/// The largest mip not larger than `max_dim` (0 = full size) as RGBA8; BC5/BC4/RG8 normals get Z rebuilt when
/// `normal`.
pub fn top_rgba(text: &[u8], texd: Option<&[u8]>, max_dim: usize, normal: bool) -> Result<(usize, usize, Vec<u8>), String> {
    let (hd, mips) = decode_mips(text, texd)?;
    let name = format_name(hd.fmt).unwrap_or("?");
    let lim = if max_dim == 0 { usize::MAX } else { max_dim };
    let (w, h, raw) = mips.iter().find(|m| m.0.max(m.1) as usize <= lim).unwrap_or(mips.last().unwrap());
    let mut px = to_rgba(name, *w, *h, raw)?;
    if normal && matches!(name, "BC5" | "BC4" | "RG8") {
        for p in px.chunks_exact_mut(4) {
            let x = p[0] as f32 / 127.5 - 1.0;
            let y = p[1] as f32 / 127.5 - 1.0;
            let z = (1.0 - x * x - y * y).max(0.0).sqrt();
            p[2] = ((z * 0.5 + 0.5) * 255.0 + 0.5) as u8;
            p[3] = 255;
        }
    }
    Ok((*w as usize, *h as usize, px))
}
