//! Native data of the asset types omni converts: Texture2D, SoundWave, StaticMesh, SkeletalMesh (see mesh.rs),
//! plus bulk data (payloads stored inline, at the end of the package or in the `.ubulk` / `.uptnl` chunks).
//!
//! Layouts follow Epic's serializers (as documented by the MIT CUE4Parse project) for UE 5.0 - 5.x cooked data.

use super::iostore::chunk;
use super::props::{self, Ctx, Props};
use super::reader::{err, Error, Reader, Result};
use super::zen::Package;
use super::Game;

pub mod flags {
    pub const PAYLOAD_AT_END: u32 = 0x1;
    pub const UNUSED: u32 = 0x20;
    pub const FORCE_INLINE: u32 = 0x40;
    pub const SEPARATE_FILE: u32 = 0x100;
    pub const OPTIONAL: u32 = 0x800;
    pub const MEMORY_MAPPED: u32 = 0x1000;
    pub const SIZE64: u32 = 0x2000;
    pub const DUPLICATE_NON_OPTIONAL: u32 = 0x4000;
    pub const BAD_DATA_VERSION: u32 = 0x8000;
    pub const NO_OFFSET_FIXUP: u32 = 0x10000;
}

#[derive(Clone, Debug)]
pub struct Bulk {
    pub flags: u32,
    pub count: i64,
    pub size: i64,
    pub offset: i64,
    /// absolute position in the package of an inline payload
    pub inline_at: Option<usize>,
}

/// FByteBulkData header (+ inline payload skipped) at `r` (absolute positions in `pkg.data`).
pub fn bulk_header(pkg: &Package, r: &mut Reader) -> Result<Bulk> {
    if !pkg.bulk.is_empty() {
        // 5.2+: index into the package's bulk data map
        let i = r.i32()?;
        let e = pkg.bulk.get(i as usize).ok_or_else(|| Error(format!("bulk data index {i} out of range")))?;
        return Ok(Bulk { flags: e.flags, count: e.size, size: e.size, offset: e.offset, inline_at: None }).map(|mut b| {
            if b.flags & flags::FORCE_INLINE != 0 {
                b.inline_at = Some(pkg.exports_end + e.offset as usize);
            }
            b
        });
    }
    let f = r.u32()?;
    let (count, size) = if f & flags::SIZE64 != 0 { (r.i64()?, r.i64()?) } else { (r.i32()? as i64, r.u32()? as i64) };
    let offset = r.i64()?;
    if f & flags::BAD_DATA_VERSION != 0 {
        r.skip(2)?;
    }
    if f & flags::DUPLICATE_NON_OPTIONAL != 0 {
        r.skip(4 + if f & flags::SIZE64 != 0 { 8 } else { 4 } + 8)?;
    }
    let mut b = Bulk { flags: f, count, size, offset, inline_at: None };
    if f & flags::FORCE_INLINE != 0 && f & flags::SEPARATE_FILE == 0 {
        b.inline_at = Some(r.pos);
        r.skip(size.max(0) as usize)?;
    }
    Ok(b)
}

impl Game {
    /// Payload of a bulk data header; None when the payload is empty or not stored for this platform.
    pub fn bulk_bytes(&self, pkg: &Package, package_id: u64, b: &Bulk) -> Result<Option<Vec<u8>>> {
        if b.flags & flags::UNUSED != 0 || b.size <= 0 {
            return Ok(None);
        }
        let size = b.size as usize;
        if let Some(at) = b.inline_at {
            return pkg.data.get(at..at + size).map(|s| Some(s.to_vec())).ok_or_else(|| Error("inline bulk data outside the package".into()));
        }
        if b.flags & flags::SEPARATE_FILE != 0 {
            let kind = if b.flags & flags::OPTIONAL != 0 {
                chunk::OPTIONAL_BULK_DATA
            } else if b.flags & flags::MEMORY_MAPPED != 0 {
                chunk::MEMORY_MAPPED_BULK_DATA
            } else {
                chunk::BULK_DATA
            };
            return match self.read_bulk(package_id, kind, b.offset as u64, size as u64) {
                Ok(v) => Ok(Some(v)),
                Err(_) if kind == chunk::OPTIONAL_BULK_DATA => Ok(None),
                Err(e) => Err(e),
            };
        }
        // payload at the end of the package data
        let start = if b.flags & flags::NO_OFFSET_FIXUP != 0 {
            (b.offset as usize).checked_sub(pkg.cooked_header_size).map(|x| x + pkg.header_size)
        } else {
            Some(pkg.exports_end + b.offset as usize)
        };
        match start.and_then(|s| pkg.data.get(s..s + size)) {
            Some(s) => Ok(Some(s.to_vec())),
            None => err("end-of-package bulk data outside the package"),
        }
    }
}

// ------------------------------------------------------------------------------------------------ texture
pub struct Mip {
    pub width: u32,
    pub height: u32,
    pub depth: u32,
    pub data: Option<Vec<u8>>,
}

pub struct Texture {
    pub format: String,
    pub width: u32,
    pub height: u32,
    pub slices: u32,
    pub mips: Vec<Mip>,
    pub props: Props,
}

/// UE pixel format -> omni format name; formats omni does not keep as blocks are converted to RGBA8.
pub fn omni_format(pf: &str) -> &'static str {
    match pf {
        "PF_DXT1" => "BC1",
        "PF_DXT3" => "BC2",
        "PF_DXT5" => "BC3",
        "PF_BC4" => "BC4",
        "PF_BC5" => "BC5",
        "PF_BC7" => "BC7",
        "PF_R8G8B8A8" => "RGBA8",
        _ => "RGBA8",
    }
}

fn half(h: u16) -> f32 {
    let s = ((h >> 15) & 1) as u32;
    let e = ((h >> 10) & 0x1F) as u32;
    let m = (h & 0x3FF) as u32;
    let bits = if e == 0 {
        if m == 0 { s << 31 } else {
            let mut e2 = 127 - 15 + 1;
            let mut m2 = m;
            while m2 & 0x400 == 0 {
                m2 <<= 1;
                e2 -= 1;
            }
            (s << 31) | ((e2 as u32) << 23) | ((m2 & 0x3FF) << 13)
        }
    } else if e == 31 {
        (s << 31) | 0x7F80_0000 | (m << 13)
    } else {
        (s << 31) | ((e + 127 - 15) << 23) | (m << 13)
    };
    f32::from_bits(bits)
}

fn to8(x: f32) -> u8 {
    // simple Reinhard tone map for HDR values, then sRGB-ish gamma
    let v = (x / (1.0 + x.max(0.0))).clamp(0.0, 1.0) * 2.0;
    (v.min(1.0).powf(1.0 / 2.2) * 255.0 + 0.5) as u8
}

/// Mip bytes in `pf` -> bytes in `omni_format(pf)` (unchanged for the block formats omni keeps).
pub fn convert_mip(pf: &str, w: u32, h: u32, data: &[u8]) -> Result<Vec<u8>> {
    let n = (w as usize) * (h as usize);
    let need = |bpp: usize| -> Result<&[u8]> { data.get(..n * bpp).ok_or_else(|| Error(format!("{pf} mip truncated"))) };
    Ok(match pf {
        "PF_DXT1" | "PF_DXT3" | "PF_DXT5" | "PF_BC4" | "PF_BC5" | "PF_BC7" | "PF_R8G8B8A8" => data.to_vec(),
        "PF_B8G8R8A8" => need(4)?.chunks_exact(4).flat_map(|p| [p[2], p[1], p[0], p[3]]).collect(),
        "PF_G8" | "PF_L8" | "PF_A8" | "PF_R8" => need(1)?.iter().flat_map(|&g| [g, g, g, 255]).collect(),
        "PF_R8G8" | "PF_V8U8" => need(2)?.chunks_exact(2).flat_map(|p| [p[0], p[1], 0, 255]).collect(),
        "PF_G16" | "PF_R16_UINT" => need(2)?.chunks_exact(2).flat_map(|p| { let g = p[1]; [g, g, g, 255] }).collect(),
        "PF_FloatRGBA" => need(8)?
            .chunks_exact(8)
            .flat_map(|p| {
                let c = |i: usize| half(u16::from_le_bytes([p[i], p[i + 1]]));
                [to8(c(0)), to8(c(2)), to8(c(4)), (c(6).clamp(0.0, 1.0) * 255.0) as u8]
            })
            .collect(),
        "PF_R16F" => need(2)?.chunks_exact(2).flat_map(|p| { let g = to8(half(u16::from_le_bytes([p[0], p[1]]))); [g, g, g, 255] }).collect(),
        "PF_BC6H" => {
            let bw = (w as usize).div_ceil(4);
            let bh = (h as usize).div_ceil(4);
            let mut px = vec![0u32; bw * 4 * bh * 4];
            texture2ddecoder::decode_bc6(data, bw * 4, bh * 4, &mut px, false).map_err(|e| Error(format!("BC6H: {e}")))?;
            let mut out = Vec::with_capacity(n * 4);
            for y in 0..h as usize {
                for x in 0..w as usize {
                    let p = px[y * bw * 4 + x];
                    out.extend_from_slice(&[(p >> 16) as u8, (p >> 8) as u8, p as u8, 255]);
                }
            }
            out
        }
        other => return err(format!("pixel format {other} not supported")),
    })
}

fn platform_data(game: &Game, pkg: &Package, pid: u64, r: &mut Reader, with_data: bool) -> Result<(String, u32, u32, u32, Vec<Mip>)> {
    if game.ue5_version >= 1009 {
        let using_derived = r.u8()?;
        if using_derived != 0 {
            return err("texture uses derived data references (not supported)");
        }
        r.skip(15)?;
    } else {
        r.skip(16)?;
    }
    let sx = r.i32()?;
    let sy = r.i32()?;
    let packed = r.u32()?;
    let pf = r.fstring()?;
    if packed & (1 << 30) != 0 {
        r.skip(8)?; // FOptTexturePlatformData
    }
    if packed & (1 << 29) != 0 {
        // CPU copy (5.4+): sizes, format, gamma, raw data
        r.skip(4 * 3 + 1 + 1)?;
        let n = r.i64()?;
        r.skip(n.max(0) as usize)?;
    }
    let _first = r.i32()?;
    let nmips = r.count(16)?;
    let mut mips = Vec::with_capacity(nmips);
    for _ in 0..nmips {
        let b = if with_data { Some(bulk_header(pkg, r)?) } else { None };
        let w = r.i32()?.max(1) as u32;
        let h = r.i32()?.max(1) as u32;
        let d = r.i32()?.max(1) as u32;
        let data = match &b {
            Some(b) => game.bulk_bytes(pkg, pid, b).ok().flatten(),
            None => None,
        };
        mips.push(Mip { width: w, height: h, depth: d, data });
    }
    let slices = (packed & ((1 << 30) - 1)).max(1);
    Ok((pf, sx.max(0) as u32, sy.max(0) as u32, slices, mips))
}

/// Texture2D (and the other texture classes sharing its layout) at export `index`.
pub fn read_texture(game: &Game, ctx: &Ctx, pkg: &Package, index: usize, class: &str) -> Result<Texture> {
    let pid = game.package_id_of(&pkg.name).unwrap_or(0);
    let e = &pkg.exports[index];
    let data = pkg.export_data(index);
    let (props, off) = props::read_object(ctx, class, data, e.flags & 0x10 != 0)?;
    let mut r = Reader::at(&pkg.data, e.start + off);
    r.skip(2)?; // UTexture strip flags
    r.skip(2)?; // UTexture2D strip flags
    let cooked = r.u32()? != 0;
    if !cooked {
        return err("texture is not cooked");
    }
    let mut with_data = true;
    if game.ue5_version >= 1010 {
        with_data = r.u32()? != 0;
    }
    loop {
        let fmt = ctx.fname(&mut r)?;
        if fmt == "None" {
            break;
        }
        let at = r.pos;
        let skip = r.i64()?;
        let end = at as i64 + skip;
        match platform_data(game, pkg, pid, &mut r, with_data) {
            Ok((pf, w, h, slices, mips)) if !mips.is_empty() => {
                return Ok(Texture { format: pf, width: w, height: h, slices, mips, props });
            }
            Ok(_) => {}
            Err(e) => return Err(e),
        }
        if end <= 0 || end as usize > pkg.data.len() {
            break;
        }
        r.seek(end as usize)?;
    }
    err("texture has no platform data")
}

// -------------------------------------------------------------------------------------------------- sound
pub struct Sound {
    pub format: String,
    pub data: Vec<u8>,
    pub props: Props,
}

fn sound_inline(game: &Game, ctx: &Ctx, pkg: &Package, pid: u64, r: &mut Reader) -> Result<Option<(String, Vec<u8>)>> {
    let n = r.count(4)?;
    let mut best = None;
    for _ in 0..n {
        let fmt = ctx.fname(r)?;
        let b = bulk_header(pkg, r)?;
        if best.is_none() {
            if let Some(d) = game.bulk_bytes(pkg, pid, &b)? {
                best = Some((fmt, d));
            }
        }
    }
    r.skip(16)?; // CompressedDataGuid
    Ok(best)
}

fn sound_streamed(game: &Game, ctx: &Ctx, pkg: &Package, pid: u64, r: &mut Reader) -> Result<Option<(String, Vec<u8>)>> {
    r.skip(16)?; // CompressedDataGuid
    let n = r.i32()?;
    let n = r.check_count(n as i64, 4)?;
    let fmt = ctx.fname(r)?;
    let mut out = Vec::new();
    for _ in 0..n {
        let f = r.u32()?;
        let b = bulk_header(pkg, r)?;
        let _data_size = r.i32()?;
        let audio_size = r.i32()?;
        if f & 2 != 0 {
            r.u32()?;
        }
        if let Some(mut d) = game.bulk_bytes(pkg, pid, &b)? {
            if audio_size > 0 && (audio_size as usize) < d.len() {
                d.truncate(audio_size as usize);
            }
            out.extend_from_slice(&d);
        }
    }
    Ok(if out.is_empty() { None } else { Some((fmt, out)) })
}

pub fn read_sound(game: &Game, ctx: &Ctx, pkg: &Package, index: usize, class: &str) -> Result<Sound> {
    let pid = game.package_id_of(&pkg.name).unwrap_or(0);
    let e = &pkg.exports[index];
    let data = pkg.export_data(index);
    let (props, off) = props::read_object(ctx, class, data, e.flags & 0x10 != 0)?;
    let start = e.start + off;
    let mut r = Reader::at(&pkg.data, start);
    let wflags = r.u32()?;
    let cooked = wflags & 1 != 0;
    if !cooked {
        return err("sound is not cooked");
    }
    if game.ue5_version >= 1012 {
        // cue points (5.4+)
        let n = r.count(4)?;
        for _ in 0..n {
            r.i32()?;
            r.fstring()?;
            r.i32()?;
            r.i32()?;
            if game.ue5_version >= 1013 {
                r.u32()?;
            }
        }
    }
    let streaming_hint = match props::get(&props, "bStreaming") {
        Some(v) => v.as_i64() == Some(1),
        None => props::get(&props, "LoadingBehavior").and_then(|v| v.as_str()).map_or(true, |s| !s.ends_with("ForceInline")),
    };
    let body = r.pos;
    let order: [bool; 2] = if streaming_hint { [true, false] } else { [false, true] };
    let mut last = Error("sound has no audio data".into());
    for streamed in order {
        let mut rr = Reader::at(&pkg.data, body);
        let got = if streamed { sound_streamed(game, ctx, pkg, pid, &mut rr) } else { sound_inline(game, ctx, pkg, pid, &mut rr) };
        match got {
            Ok(Some((format, data))) => return Ok(Sound { format, data, props }),
            Ok(None) => {}
            Err(e) => last = e,
        }
    }
    Err(last)
}
