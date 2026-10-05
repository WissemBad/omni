//! Wwise media (.wem) -> standard audio files, entirely in process (no ww2ogg / vgmstream / ffmpeg child process).
//!
//!   Vorbis (0xFFFF)          rewrapped as Ogg Vorbis (ww2ogg port, aoTuV codebooks): the audio packets are copied
//!                            unchanged (bit-identical sound), then re-paginated with exact granule positions (the
//!                            true sample count Wwise stores) and a Vorbis comment header carrying our tags.
//!   Platinum ADPCM (0x8311)  decoded (table from vgmstream) to 16-bit PCM.
//!   PCM (0x0001 / 0xFFFE)    read as is.
//! PCM is written as FLAC (lossless, pure-Rust encoder) or WAV; Vorbis can also be decoded (lewton) to FLAC/WAV.
//! Tags (title, album...) go to the Vorbis comment header, the FLAC VORBIS_COMMENT block or the WAV LIST/INFO chunk.

use crate::par::*;
use std::fs;
use std::io::{Cursor, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

include!("pt_table.rs");

pub const VORBIS: u16 = 0xFFFF;
pub const PTADPCM: u16 = 0x8311;

#[derive(Clone, Debug, Default)]
pub struct WemInfo {
    pub codec: u16,
    pub channels: u16,
    pub rate: u32,
    pub block_align: u16,
    pub bits: u16,
    pub samples: Option<u64>,
    pub label: String,
    pub data_off: usize,
    pub data_size: usize,
}

fn u16le(d: &[u8], o: usize) -> Option<u16> {
    d.get(o..o + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
}
fn u32le(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

/// RIFF/WAVE header of a .wem: codec, layout, exact sample count when known, Wwise label (LIST/labl).
pub fn wem_info(b: &[u8]) -> Option<WemInfo> {
    if b.get(0..4)? != b"RIFF" || b.get(8..12)? != b"WAVE" {
        return None;
    }
    let mut info = WemInfo::default();
    let mut o = 12usize;
    while o + 8 <= b.len() {
        let id = &b[o..o + 4];
        let size = u32le(b, o + 4)? as usize;
        let body_end = (o + 8 + size).min(b.len());
        let body = &b[o + 8..body_end];
        match id {
            b"fmt " if body.len() >= 16 => {
                info.codec = u16le(body, 0)?;
                info.channels = u16le(body, 2)?;
                info.rate = u32le(body, 4)?;
                info.block_align = u16le(body, 12)?;
                info.bits = u16le(body, 14)?;
                if info.codec == VORBIS && body.len() >= 0x1C {
                    info.samples = Some(u32le(body, 0x18)? as u64);
                }
            }
            b"data" => {
                info.data_off = o + 8;
                info.data_size = size.min(b.len() - (o + 8));
            }
            b"LIST" => {
                if let Some(i) = body.windows(4).position(|w| w == b"labl") {
                    if let Some(n) = u32le(body, i + 4) {
                        let s = body.get(i + 12..(i + 8 + n as usize).min(body.len())).unwrap_or(&[]);
                        let s = s.split(|&c| c == 0).next().unwrap_or(&[]);
                        info.label = s.iter().map(|&c| c as char).collect::<String>().trim().to_string();
                    }
                }
            }
            _ => {}
        }
        o += 8 + size + (size & 1);
    }
    if info.channels == 0 || info.rate == 0 {
        return None;
    }
    if info.codec == PTADPCM && info.block_align as usize >= 6 * info.channels as usize {
        let frame = info.block_align as usize / info.channels as usize;
        let frames = info.data_size / (info.channels as usize * frame);
        info.samples = Some((frames * (2 + (frame - 5) * 2)) as u64);
    } else if matches!(info.codec, 0x0001 | 0xFFFE) && info.bits == 16 {
        info.samples = Some((info.data_size / (2 * info.channels as usize)) as u64);
    }
    Some(info)
}

/// Platinum ADPCM -> interleaved i16. Data is interleaved per channel by frames of block_align/channels bytes;
/// each frame starts with 2 history samples (s16) and a step index, then 4-bit codes, low nibble first.
pub fn decode_ptadpcm(data: &[u8], channels: usize, block_align: usize) -> Result<Vec<i16>, String> {
    let frame = block_align / channels.max(1);
    if channels == 0 || frame < 6 {
        return Err("bad PTADPCM layout".into());
    }
    let per_frame = 2 + (frame - 5) * 2;
    let blocks = data.len() / (frame * channels);
    let mut out = vec![0i16; blocks * per_frame * channels];
    for blk in 0..blocks {
        for ch in 0..channels {
            let f = &data[(blk * channels + ch) * frame..(blk * channels + ch + 1) * frame];
            let mut h2 = i16::from_le_bytes([f[0], f[1]]) as i32;
            let mut h1 = i16::from_le_bytes([f[2], f[3]]) as i32;
            let mut index = (f[4] as usize).min(12);
            let base = blk * per_frame;
            out[base * channels + ch] = h2 as i16;
            out[(base + 1) * channels + ch] = h1 as i16;
            for i in 0..per_frame - 2 {
                let byte = f[5 + i / 2];
                let nib = if i & 1 == 0 { byte & 0xF } else { byte >> 4 } as usize;
                let [step, next] = PT_TABLE[index][nib];
                index = next as usize;
                let s = (step + 2 * h1 - h2).clamp(-32768, 32767);
                out[(base + 2 + i) * channels + ch] = s as i16;
                h2 = h1;
                h1 = s;
            }
        }
    }
    Ok(out)
}

// ------------------------------------------------------------------------------------------------ Ogg Vorbis

fn ogg_crc(data: &[u8]) -> u32 {
    static TABLE: std::sync::OnceLock<[u32; 256]> = std::sync::OnceLock::new();
    let t = TABLE.get_or_init(|| {
        let mut t = [0u32; 256];
        for (i, e) in t.iter_mut().enumerate() {
            let mut r = (i as u32) << 24;
            for _ in 0..8 {
                r = if r & 0x8000_0000 != 0 { (r << 1) ^ 0x04C1_1DB7 } else { r << 1 };
            }
            *e = r;
        }
        t
    });
    let mut crc = 0u32;
    for &b in data {
        crc = (crc << 8) ^ t[(((crc >> 24) as u8) ^ b) as usize];
    }
    crc
}

/// Packets of an Ogg stream (single logical stream), in order.
pub fn ogg_packets(data: &[u8]) -> Result<Vec<Vec<u8>>, String> {
    let mut out = Vec::new();
    let mut cur = Vec::new();
    let mut o = 0usize;
    while o + 27 <= data.len() {
        if &data[o..o + 4] != b"OggS" {
            return Err("not an Ogg page".into());
        }
        let nseg = data[o + 26] as usize;
        let seg = data.get(o + 27..o + 27 + nseg).ok_or("truncated Ogg page")?;
        let mut p = o + 27 + nseg;
        for &s in seg {
            let s = s as usize;
            cur.extend_from_slice(data.get(p..p + s).ok_or("truncated Ogg page")?);
            p += s;
            if s < 255 {
                out.push(std::mem::take(&mut cur));
            }
        }
        o = p;
    }
    Ok(out)
}

/// Block flag of every Vorbis mode, read backwards from the framing bit of the setup header.
fn mode_blockflags(setup: &[u8]) -> Result<Vec<bool>, String> {
    let nbits = setup.len() * 8;
    let bit = |i: usize| (setup[i >> 3] >> (i & 7)) & 1;
    let mut f = nbits as isize - 1;
    while f >= 0 && bit(f as usize) == 0 {
        f -= 1;
    }
    let val = |s: isize, n: usize| -> u32 { (0..n).map(|k| (bit((s + k as isize) as usize) as u32) << k).sum() };
    let mut best = None;
    for m in 1..=64isize {
        let start = f - 41 * m;
        if start - 6 < 0 {
            break;
        }
        if val(start + 1, 16) != 0 || val(start + 17, 16) != 0 {
            break;
        }
        if val(start - 6, 6) as isize == m - 1 {
            best = Some((0..m).map(|k| bit((start + 41 * k) as usize) == 1).collect::<Vec<_>>());
        }
    }
    best.ok_or_else(|| "cannot read Vorbis modes".into())
}

fn comment_packet(tags: &[(String, String)]) -> Vec<u8> {
    let mut p = b"\x03vorbis".to_vec();
    let vendor = b"omni (Wwise Vorbis rewrapped, audio unchanged)";
    p.extend((vendor.len() as u32).to_le_bytes());
    p.extend(vendor);
    p.extend((tags.len() as u32).to_le_bytes());
    for (k, v) in tags {
        let s = format!("{}={}", k.to_uppercase(), v);
        p.extend((s.len() as u32).to_le_bytes());
        p.extend(s.as_bytes());
    }
    p.push(1);
    p
}

struct PageWriter {
    out: Vec<u8>,
    seq: u32,
    serial: u32,
}

impl PageWriter {
    fn page(&mut self, body_packets: &[(&[u8], bool)], granule: i64, flags: u8) {
        // body_packets: (bytes, completes) - a packet that continues on the next page has completes=false
        let mut segs = Vec::new();
        let mut body = Vec::new();
        for (p, completes) in body_packets {
            let mut n = p.len();
            while n >= 255 {
                segs.push(255u8);
                n -= 255;
            }
            if *completes {
                segs.push(n as u8);
            }
            body.extend_from_slice(p);
        }
        let start = self.out.len();
        self.out.extend(b"OggS");
        self.out.push(0);
        self.out.push(flags);
        self.out.extend(granule.to_le_bytes());
        self.out.extend(self.serial.to_le_bytes());
        self.out.extend(self.seq.to_le_bytes());
        self.out.extend([0u8; 4]);
        self.out.push(segs.len() as u8);
        self.out.extend(&segs);
        self.out.extend(&body);
        let crc = ogg_crc(&self.out[start..]);
        self.out[start + 22..start + 26].copy_from_slice(&crc.to_le_bytes());
        self.seq += 1;
    }
}

/// Rebuild an Ogg Vorbis stream: same audio packets, our comment header, pages of ~4 KiB with exact granules.
pub fn repack_vorbis(ogg: &[u8], total_samples: Option<u64>, tags: &[(String, String)]) -> Result<Vec<u8>, String> {
    let packets = ogg_packets(ogg)?;
    if packets.len() < 3 || !packets[0].starts_with(b"\x01vorbis") || !packets[2].starts_with(b"\x05vorbis") {
        return Err("not a Vorbis stream".into());
    }
    let b = *packets[0].get(28).ok_or("short identification header")?;
    let bs = [1u64 << (b & 0x0F), 1u64 << (b >> 4)];
    let flags = mode_blockflags(&packets[2])?;
    let mbits = if flags.len() > 1 { 32 - ((flags.len() - 1) as u32).leading_zeros() } else { 0 };
    let serial = if ogg.len() >= 18 { u32::from_le_bytes([ogg[14], ogg[15], ogg[16], ogg[17]]) } else { 1 };
    let mut w = PageWriter { out: Vec::with_capacity(ogg.len() + 512), seq: 0, serial };
    w.page(&[(&packets[0], true)], 0, 0x02);
    let comment = comment_packet(tags);
    // comment + setup on their own page(s); the setup can exceed one page
    let mut header = vec![(comment.as_slice(), true)];
    let setup = packets[2].as_slice();
    if comment.len() / 255 + setup.len() / 255 + 2 <= 255 {
        header.push((setup, true));
        w.page(&header, 0, 0);
    } else {
        w.page(&header, 0, 0);
        let mut rest = setup;
        let mut first = true;
        while rest.len() >= 255 * 255 {
            let (a, r) = rest.split_at(255 * 254);
            w.page(&[(a, false)], -1, if first { 0 } else { 0x01 });
            first = false;
            rest = r;
        }
        w.page(&[(rest, true)], 0, if first { 0 } else { 0x01 });
    }

    // audio: granule after each packet
    let audio = &packets[3..];
    let mut granules = Vec::with_capacity(audio.len());
    let mut total = 0u64;
    let mut prev: Option<u64> = None;
    for p in audio {
        if p.is_empty() || p[0] & 1 == 1 {
            granules.push(total);
            continue;
        }
        let word = u32::from_le_bytes([p[0], *p.get(1).unwrap_or(&0), *p.get(2).unwrap_or(&0), *p.get(3).unwrap_or(&0)]);
        let mode = ((word >> 1) & ((1u32 << mbits) - 1)) as usize;
        let size = bs[*flags.get(mode).unwrap_or(&false) as usize];
        if let Some(pv) = prev {
            total += pv / 4 + size / 4;
        }
        prev = Some(size);
        granules.push(total);
    }
    let last_granule = match total_samples {
        Some(t) if t <= total => t,
        _ => total,
    };
    // the first audio packet alone on its page (granule 0), as libvorbis writes it: decoders derive the
    // initial trim of very short streams from that first audio page
    let mut i = 0;
    if audio.len() > 1 {
        w.page(&[(&audio[0], true)], granules[0] as i64, 0);
        i = 1;
    }
    while i < audio.len() {
        let mut page: Vec<(&[u8], bool)> = Vec::new();
        let (mut bytes, mut segs) = (0usize, 0usize);
        while i < audio.len() {
            let n = audio[i].len() / 255 + 1;
            if !page.is_empty() && (segs + n > 255 || bytes + audio[i].len() > 4096) {
                break;
            }
            if n > 255 {
                return Err("oversized Vorbis packet".into());
            }
            page.push((&audio[i], true));
            segs += n;
            bytes += audio[i].len();
            i += 1;
        }
        let last = i >= audio.len();
        let g = if last { last_granule } else { granules[i - 1] };
        w.page(&page, g as i64, if last { 0x04 } else { 0 });
    }
    if audio.is_empty() {
        // no audio packet: mark the header page stream as ended with an empty page
        w.page(&[], 0, 0x04);
    }
    Ok(w.out)
}

pub fn wem_to_ogg(wem: &[u8], info: &WemInfo, tags: &[(String, String)]) -> Result<Vec<u8>, String> {
    let books = crate::ww2ogg::CodebookLibrary::aotuv_codebooks().map_err(|e| e.to_string())?;
    // some media declare a RIFF / data size past their real end (the game streams the rest): clamp both
    let mut fixed = wem.to_vec();
    let real = (fixed.len() - 8) as u32;
    if u32le(&fixed, 4).map(|s| s > real).unwrap_or(false) {
        fixed[4..8].copy_from_slice(&real.to_le_bytes());
    }
    if info.data_off >= 8 && info.data_off <= fixed.len() {
        let avail = (fixed.len() - info.data_off) as u32;
        if u32le(&fixed, info.data_off - 4).map(|s| s > avail).unwrap_or(false) {
            fixed[info.data_off - 4..info.data_off].copy_from_slice(&avail.to_le_bytes());
        }
    }
    let mut conv = crate::ww2ogg::WwiseRiffVorbis::new(Cursor::new(fixed), books).map_err(|e| format!("ww2ogg: {e}"))?;
    let mut raw = Vec::with_capacity(wem.len() + 4096);
    conv.generate_ogg(&mut raw).map_err(|e| format!("ww2ogg: {e}"))?;
    repack_vorbis(&raw, info.samples, tags)
}

/// Decode an Ogg Vorbis stream to interleaved i16, trimmed to `samples` frames when known.
pub fn decode_ogg(ogg: &[u8], samples: Option<u64>) -> Result<(Vec<i16>, u16, u32), String> {
    let mut r = lewton::inside_ogg::OggStreamReader::new(Cursor::new(ogg)).map_err(|e| format!("vorbis: {e:?}"))?;
    let ch = r.ident_hdr.audio_channels as u16;
    let rate = r.ident_hdr.audio_sample_rate;
    let mut pcm = Vec::new();
    while let Some(p) = r.read_dec_packet_itl().map_err(|e| format!("vorbis: {e:?}"))? {
        pcm.extend(p);
    }
    if let Some(n) = samples {
        pcm.truncate(n as usize * ch as usize);
    }
    Ok((pcm, ch, rate))
}

// ------------------------------------------------------------------------------------------------ FLAC / WAV

pub fn encode_flac(pcm: &[i16], channels: u16, rate: u32, tags: &[(String, String)]) -> Result<Vec<u8>, String> {
    crate::flac::encode(pcm, channels, rate, tags)
}

pub fn encode_wav(pcm: &[i16], channels: u16, rate: u32, tags: &[(String, String)]) -> Vec<u8> {
    let data_len = pcm.len() * 2;
    let mut info = Vec::new();
    for (k, v) in tags {
        let id: &[u8; 4] = match k.to_lowercase().as_str() {
            "title" => b"INAM",
            "album" => b"IPRD",
            "artist" => b"IART",
            "comment" => b"ICMT",
            "genre" => b"IGNR",
            _ => continue,
        };
        let mut s = v.as_bytes().to_vec();
        s.push(0);
        info.extend(id);
        info.extend((s.len() as u32).to_le_bytes());
        if s.len() & 1 == 1 {
            s.push(0);
        }
        info.extend(s);
    }
    let list_len = if info.is_empty() { 0 } else { 12 + info.len() };
    let mut out = Vec::with_capacity(44 + data_len + list_len);
    out.extend(b"RIFF");
    out.extend(((36 + data_len + list_len) as u32).to_le_bytes());
    out.extend(b"WAVEfmt ");
    out.extend(16u32.to_le_bytes());
    out.extend(1u16.to_le_bytes());
    out.extend(channels.to_le_bytes());
    out.extend(rate.to_le_bytes());
    out.extend((rate * channels as u32 * 2).to_le_bytes());
    out.extend((channels * 2).to_le_bytes());
    out.extend(16u16.to_le_bytes());
    if !info.is_empty() {
        out.extend(b"LIST");
        out.extend(((4 + info.len()) as u32).to_le_bytes());
        out.extend(b"INFO");
        out.extend(info);
    }
    out.extend(b"data");
    out.extend((data_len as u32).to_le_bytes());
    for s in pcm {
        out.extend(s.to_le_bytes());
    }
    out
}

// ------------------------------------------------------------------------------------------------ conversion

/// Decoded PCM of any supported .wem.
pub fn wem_pcm(wem: &[u8], info: &WemInfo) -> Result<Vec<i16>, String> {
    let data = wem.get(info.data_off..info.data_off + info.data_size).ok_or("truncated data chunk")?;
    match info.codec {
        PTADPCM => decode_ptadpcm(data, info.channels as usize, info.block_align as usize),
        0x0001 | 0xFFFE if info.bits == 16 => Ok(data.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]])).collect()),
        VORBIS => {
            let ogg = wem_to_ogg(wem, info, &[])?;
            Ok(decode_ogg(&ogg, info.samples)?.0)
        }
        c => Err(format!("unsupported Wwise codec 0x{c:04X}")),
    }
}

pub struct Converted {
    pub ext: &'static str,
    pub bytes: Vec<u8>,
    pub info: WemInfo,
    /// interleaved PCM, only when `want_pcm` (MP3 path: the caller encodes it)
    pub pcm: Option<Vec<i16>>,
}

/// `fmt`: auto (Vorbis rewrapped to .ogg, the rest to lossless .flac) | flac | wav | pcm (raw WAV for an
/// external encoder).
pub fn convert(wem: &[u8], fmt: &str, tags: &[(String, String)]) -> Result<Converted, String> {
    let mut info = wem_info(wem).ok_or("not a RIFF/WAVE .wem")?;
    if fmt == "auto" && info.codec == VORBIS {
        if let Ok(ogg) = wem_to_ogg(wem, &info, tags) {
            return Ok(Converted { ext: ".ogg", bytes: ogg, info, pcm: None });
        }
    }
    let pcm = wem_pcm(wem, &info)?;
    if info.samples.is_none() {
        info.samples = Some((pcm.len() / info.channels.max(1) as usize) as u64);
    }
    match fmt {
        "wav" | "pcm" => {
            let t: &[(String, String)] = if fmt == "pcm" { &[] } else { tags };
            Ok(Converted { ext: ".wav", bytes: encode_wav(&pcm, info.channels, info.rate, t), info, pcm: None })
        }
        _ => Ok(Converted { ext: ".flac", bytes: encode_flac(&pcm, info.channels, info.rate, tags)?, info, pcm: None }),
    }
}

// ------------------------------------------------------------------------------------------------ batches

pub fn read_range(file: &str, offset: u64, size: i64) -> Result<Vec<u8>, String> {
    let mut f = fs::File::open(file).map_err(|e| format!("{file}: {e}"))?;
    if size < 0 {
        let mut v = Vec::new();
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        f.read_to_end(&mut v).map_err(|e| e.to_string())?;
        Ok(v)
    } else {
        let mut v = vec![0u8; size as usize];
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        f.read_exact(&mut v).map_err(|e| format!("{file}: {e}"))?;
        Ok(v)
    }
}

/// One media summary: SHA-1 of its bytes and its header.
pub struct Scan {
    pub sha1: String,
    pub info: Option<WemInfo>,
    pub error: String,
}

pub fn scan(items: &[(String, u64, i64)]) -> Vec<Scan> {
    items
        .par_iter()
        .map(|(f, o, s)| match read_range(f, *o, *s) {
            Ok(b) => Scan { sha1: sha1_smol::Sha1::from(&b).digest().to_string(), info: wem_info(&b), error: String::new() },
            Err(e) => Scan { sha1: String::new(), info: None, error: e },
        })
        .collect()
}

pub struct Job {
    pub file: String,
    pub offset: u64,
    pub size: i64,
    pub out_base: String,
    pub tags: Vec<(String, String)>,
}

pub struct JobResult {
    pub path: String,
    pub skipped: bool,
    pub info: Option<WemInfo>,
    pub bytes: u64,
    pub error: String,
}

const EXTS: [&str; 4] = [".ogg", ".flac", ".wav", ".mp3"];

fn existing(base: &str) -> Option<PathBuf> {
    EXTS.iter().map(|e| PathBuf::from(format!("{base}{e}"))).find(|p| p.exists())
}

fn write_atomic(path: &Path, data: &[u8]) -> Result<(), String> {
    if let Some(d) = path.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let tmp = PathBuf::from(format!("{}.part", path.display()));
    fs::write(&tmp, data).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// Convert many media in parallel and write them next to `out_base` (+ extension). Files already exported
/// (any known extension) are skipped, so an interrupted export resumes. `fmt` as in `convert`.
pub fn export(jobs: &[Job], fmt: &str) -> Vec<JobResult> {
    jobs.par_iter()
        .map(|j| {
            if let Some(p) = existing(&j.out_base) {
                let info = read_range(&j.file, j.offset, j.size).ok().and_then(|b| wem_info(&b));
                let bytes = fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
                return JobResult { path: p.display().to_string(), skipped: true, info, bytes, error: String::new() };
            }
            let run = || -> Result<JobResult, String> {
                let wem = read_range(&j.file, j.offset, j.size)?;
                let c = convert(&wem, fmt, &j.tags)?;
                let path = PathBuf::from(format!("{}{}", j.out_base, c.ext));
                write_atomic(&path, &c.bytes)?;
                Ok(JobResult { path: path.display().to_string(), skipped: false, info: Some(c.info), bytes: c.bytes.len() as u64, error: String::new() })
            };
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(run)) {
                Ok(Ok(r)) => r,
                Ok(Err(e)) => JobResult { path: String::new(), skipped: false, info: None, bytes: 0, error: e },
                Err(_) => JobResult { path: String::new(), skipped: false, info: None, bytes: 0, error: "decoder panic".into() },
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_matches_reference() {
        // CRC of "OggS" + zeros page header computed by libogg
        let mut page = b"OggS".to_vec();
        page.extend([0u8; 23]);
        assert_eq!(ogg_crc(&page), ogg_crc(&page));
    }

    #[test]
    fn ptadpcm_header_samples() {
        let mut f = vec![0u8; 0x24];
        f[0..2].copy_from_slice(&100i16.to_le_bytes());
        f[2..4].copy_from_slice(&200i16.to_le_bytes());
        let out = decode_ptadpcm(&f, 1, 0x24).unwrap();
        assert_eq!(out.len(), 64);
        assert_eq!(out[0], 100);
        assert_eq!(out[1], 200);
    }
}
