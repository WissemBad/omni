//! Bink Audio 2 as cooked by Unreal Engine 5 (`BINKA` sound waves, "ABEU" container) -> 16-bit PCM.
//!
//! Port of vgmstream's reverse-engineered decoder (`src/coding/libs/binka_dec.c`, `src/meta/ueba.c`, ISC-style
//! licence, (c) the vgmstream authors): DCT mode, Bink Audio 2 coefficient packing. The original's hand-unrolled
//! transform is replaced by an FFT-based DCT-III computing the same sum
//! (`out[n] = c[0] + sum_k c[k] cos(pi k (2n + 1) / 2N)`), checked against the direct formula in the tests.
//!
//! Container: "ABEU", u8 version (1), u8 channels, u16 pad, i32 sample rate, i32 samples, u32 max frame size,
//! u16 flags, u32 size, u16 seek entries, u16 frames per seek block, seek table (u16 each), then packets
//! {u16 0x9999, u16 size (0xFFFF: u16 size + u16 samples follow), data} and optional "SEEK" chunks.

const MAX_BANDS: usize = 26;

static CUTOFF: [u32; 25] = [
    0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300, 6400,
    7700, 9500, 12000, 15500,
];
static RLE: [usize; 16] = [2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 32, 64];
static SCALE: [f32; 96] = [
    1.0, 1.1651987, 1.3576881, 1.5819764, 1.8433169, 2.1478305, 2.5026493, 2.9160838, 3.3978171, 3.9591322, 4.6131759,
    5.3752666, 6.2632537, 7.297935, 8.5035448, 9.9083195, 11.545161, 13.452407, 15.674727, 18.264172, 21.281391,
    24.797049, 28.89349, 33.666656, 39.228348, 45.70882, 53.259857, 62.058319, 72.310272, 84.255836, 98.174797,
    114.39314, 133.29074, 155.31021, 180.96725, 210.86281, 245.69708, 286.28592, 333.57999, 388.68698, 452.89758,
    527.7157, 614.89362, 716.47327, 834.83374, 972.74725, 1133.4438, 1320.6873, 1538.8632, 1793.0814, 2089.2961,
    2434.4451, 2836.6123, 3305.2173, 3851.2349, 4487.4541, 5228.7754, 6092.5625, 7099.0464, 8271.7998, 9638.29,
    11230.523, 13085.792, 15247.548, 17766.424, 20701.414, 24121.26, 28106.062, 32749.148, 38159.266, 44463.125,
    51808.379, 60367.055, 70339.617, 81959.633, 95499.258, 111275.62, 129658.2, 151077.58, 176035.39, 205116.22,
    239001.16, 278483.84, 324489.03, 378094.19, 440554.88, 513333.97, 598136.06, 696947.38, 812082.19, 946237.19,
    1102554.4, 1284694.9, 1496924.9, 1744214.9, 2032357.0,
];

struct Bits<'a> {
    d: &'a [u8],
    pos: usize,
    error: bool,
}

impl<'a> Bits<'a> {
    fn read(&mut self, n: u32) -> u32 {
        if n == 0 {
            return 0;
        }
        if self.pos + n as usize > self.d.len() * 8 {
            self.error = true;
            self.pos += n as usize;
            return 0;
        }
        let mut v: u64 = 0;
        let mut got = 0u32;
        let mut p = self.pos;
        while got < n {
            let byte = self.d[p / 8] as u64;
            let off = (p % 8) as u32;
            let take = (8 - off).min(n - got);
            v |= ((byte >> off) & ((1 << take) - 1)) << got;
            got += take;
            p += take as usize;
        }
        self.pos = p;
        v as u32
    }
    fn float29(&mut self) -> f32 {
        let code = self.read(29);
        let power = (code & 0x1F) as i32;
        let mantissa = ((code >> 5) & 0x77F_FFFF) as f32;
        let v = if power < 24 { mantissa * 2f32.powi(power - 23) } else { 0.0 };
        if (code >> 28) & 1 != 0 { -v } else { v }
    }
}

// ---------------------------------------------------------------------------------------------- transform
#[derive(Clone, Copy)]
struct C(f32, f32);

fn fft(a: &mut [C], tw: &[C]) {
    let n = a.len();
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            a.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let step = n / len;
        for s in (0..n).step_by(len) {
            for k in 0..len / 2 {
                let w = tw[k * step];
                let u = a[s + k];
                let x = a[s + k + len / 2];
                let v = C(x.0 * w.0 - x.1 * w.1, x.0 * w.1 + x.1 * w.0);
                a[s + k] = C(u.0 + v.0, u.1 + v.1);
                a[s + k + len / 2] = C(u.0 - v.0, u.1 - v.1);
            }
        }
        len <<= 1;
    }
}

/// DCT-III without halving the first coefficient, via an N-point complex inverse FFT.
pub struct Dct {
    n: usize,
    tw: Vec<C>,
    rot: Vec<C>,
    buf: Vec<C>,
}

impl Dct {
    pub fn new(n: usize) -> Dct {
        let tw = (0..n / 2.max(1)).map(|k| {
            let a = 2.0 * std::f64::consts::PI * k as f64 / n as f64;
            C(a.cos() as f32, a.sin() as f32) // inverse transform: e^{+i 2 pi k / n}
        }).collect();
        let rot = (0..n).map(|k| {
            let a = std::f64::consts::PI * k as f64 / (2.0 * n as f64);
            C(a.cos() as f32, a.sin() as f32)
        }).collect();
        Dct { n, tw, rot, buf: vec![C(0.0, 0.0); n] }
    }

    pub fn run(&mut self, x: &mut [f32]) {
        let n = self.n;
        // V[k] = e^{i pi k / 2N} (X[k] - i X[N-k]), V[0] = 2 X[0]; y[2n] = Re v[n] / 2, y[2n+1] = Re v[N-1-n] / 2
        for k in 0..n {
            let a = if k == 0 { 2.0 * x[0] } else { x[k] };
            let b = if k == 0 { 0.0 } else { x[n - k] };
            let r = self.rot[k];
            // (a - i b) * (r.0 + i r.1)
            self.buf[k] = C(a * r.0 + b * r.1, a * r.1 - b * r.0);
        }
        fft(&mut self.buf, &self.tw);
        for i in 0..n / 2 {
            x[2 * i] = 0.5 * self.buf[i].0;
            x[2 * i + 1] = 0.5 * self.buf[n - 1 - i].0;
        }
    }
}

// ------------------------------------------------------------------------------------------------ decoder
struct Dec {
    frame_samples: usize,
    frame_channels: usize,
    scale: f32,
    overlap_samples: usize,
    overlap_bits: u32,
    first: bool,
    band_count: usize,
    thresholds: [usize; MAX_BANDS],
    dct: Dct,
}

impl Dec {
    fn new(sample_rate: u32, frame_channels: usize) -> Dec {
        let frame_samples = if sample_rate < 22050 { 512 } else if sample_rate < 44100 { 1024 } else { 2048 };
        let half = frame_samples >> 1;
        let sr_half = (sample_rate + 1) >> 1;
        let mut band_count = 0;
        while band_count < MAX_BANDS - 1 && CUTOFF[band_count] < sr_half {
            band_count += 1;
        }
        let mut thresholds = [0usize; MAX_BANDS];
        for (i, t) in thresholds.iter_mut().enumerate().take(band_count) {
            let v = half * CUTOFF[i] as usize / sr_half as usize;
            *t = v.max(1);
        }
        thresholds[band_count] = half;
        let overlap_samples = frame_samples >> 4;
        Dec {
            frame_samples,
            frame_channels,
            scale: 2.0 / (frame_samples as f32).sqrt(),
            overlap_samples,
            overlap_bits: match overlap_samples { 32 => 5, 64 => 6, 128 => 7, 256 => 8, _ => 0 },
            first: true,
            band_count,
            thresholds,
            dct: Dct::new(frame_samples),
        }
    }

    fn unpack(&self, c: &mut [f32], bs: &mut Bits) -> bool {
        let n = self.frame_samples;
        c[0] = bs.float29();
        c[1] = bs.float29();
        let mut sf = [0f32; MAX_BANDS];
        for s in sf.iter_mut().take(self.band_count) {
            *s = SCALE[(bs.read(7) as usize).min(95)];
        }
        let mut band_sf = 0f32;
        let mut band = 0usize;
        let mut pos = 2;
        while pos < n {
            let end = if bs.read(1) != 0 { pos + 8 * RLE[bs.read(4) as usize] } else { pos + 8 }.min(n);
            let q = bs.read(4);
            if q > 0 {
                for v in c.iter_mut().take(end).skip(pos) {
                    *v = bs.read(q) as f32;
                }
                for v in c.iter_mut().take(end).skip(pos) {
                    if *v != 0.0 && bs.read(1) != 0 {
                        *v = -*v;
                    }
                }
                while pos < end {
                    if band <= self.band_count && pos == self.thresholds[band] * 2 {
                        band_sf = sf[band.min(MAX_BANDS - 1)];
                        band += 1;
                    }
                    c[pos] *= band_sf;
                    pos += 1;
                }
            } else {
                for v in c.iter_mut().take(end).skip(pos) {
                    *v = 0.0;
                }
                pos = end;
                while band <= self.band_count && end > self.thresholds[band] * 2 {
                    band_sf = sf[band.min(MAX_BANDS - 1)];
                    band += 1;
                }
            }
            if bs.error {
                return false;
            }
        }
        !bs.error
    }

    /// Decode one frame (1 or 2 channels) from `src`; coefficients land in `out` (frame_channels x frame_samples).
    fn frame(&mut self, src: &[u8], out: &mut [f32], overlap: &mut [f32]) -> Option<usize> {
        let mut bs = Bits { d: src, pos: 0, error: false };
        bs.read(2);
        let n = self.frame_samples;
        for ch in 0..self.frame_channels {
            let c = &mut out[ch * n..(ch + 1) * n];
            if !self.unpack(c, &mut bs) {
                return None;
            }
            self.dct.run(c);
        }
        for v in out.iter_mut().take(n * self.frame_channels) {
            *v *= self.scale;
        }
        let out_samples = n - self.overlap_samples;
        let bits = if self.first { 0 } else { self.overlap_bits };
        self.first = false;
        for ch in 0..self.frame_channels {
            let c = &mut out[ch * n..(ch + 1) * n];
            let o = &mut overlap[ch * self.overlap_samples..(ch + 1) * self.overlap_samples];
            if bits != 0 {
                for i in 0..self.overlap_samples {
                    let s1 = o[i];
                    c[i] = s1 + (i as f32 * (c[i] - s1)) / self.overlap_samples as f32;
                }
            }
            o.copy_from_slice(&c[out_samples..out_samples + self.overlap_samples]);
        }
        let bitpos = bs.pos.div_ceil(32) * 32;
        Some(bitpos / 8)
    }
}

pub struct Decoded {
    pub channels: u16,
    pub sample_rate: u32,
    pub pcm: Vec<i16>,
}

/// Decode a whole "ABEU" stream to interleaved 16-bit PCM.
pub fn decode(data: &[u8]) -> Result<Decoded, String> {
    if data.len() < 0x1C || &data[0..4] != b"ABEU" {
        return Err("not a UE Bink Audio stream (ABEU)".into());
    }
    let rd16 = |o: usize| u16::from_le_bytes([data[o], data[o + 1]]);
    let rd32 = |o: usize| u32::from_le_bytes([data[o], data[o + 1], data[o + 2], data[o + 3]]);
    let channels = data[5] as usize;
    let sample_rate = rd32(8);
    let total = rd32(0x0C) as usize;
    if !(1..=8).contains(&channels) || !(300..=96000).contains(&sample_rate) {
        return Err(format!("unsupported Bink Audio stream ({channels} channels, {sample_rate} Hz)"));
    }
    let seek_entries = rd16(0x18) as usize;
    let mut pos = 0x1C + seek_entries * 2;
    let ndec = channels.div_ceil(2);
    let mut decs: Vec<Dec> = (0..ndec).map(|i| Dec::new(sample_rate, if channels - 2 * i >= 2 { 2 } else { 1 })).collect();
    let frame_samples = decs[0].frame_samples;
    let out_per_frame = if sample_rate < 22050 { 480 } else if sample_rate < 44100 { 960 } else { 1920 };
    let mut coefs = vec![0f32; frame_samples * channels + 2 * frame_samples];
    let mut overlap = vec![0f32; 256 * channels];
    let mut pcm: Vec<i16> = Vec::with_capacity(total.min(1 << 28) * channels);
    while pos + 4 <= data.len() && pcm.len() < total * channels {
        if &data[pos..pos + 4] == b"SEEK" {
            if pos + 0x0F > data.len() {
                break;
            }
            let entries = rd32(pos + 0x0B) as usize;
            pos += 0x0F + entries * 2;
            continue;
        }
        let sync = rd16(pos);
        let mut size = rd16(pos + 2) as usize;
        pos += 4;
        if sync != 0x9999 {
            break;
        }
        let mut limit = 0usize;
        if size == 0xFFFF {
            if pos + 4 > data.len() {
                break;
            }
            size = rd16(pos) as usize;
            limit = rd16(pos + 2) as usize;
            pos += 4;
        }
        if pos + size > data.len() {
            break;
        }
        let mut src = &data[pos..pos + size];
        pos += size;
        let mut co = 0usize;
        let mut oo = 0usize;
        let mut failed = false;
        for d in decs.iter_mut() {
            let len = d.frame_samples * d.frame_channels;
            let olen = d.overlap_samples * d.frame_channels;
            match d.frame(src, &mut coefs[co..co + len], &mut overlap[oo..oo + olen]) {
                Some(used) => src = &src[used.min(src.len())..],
                None => {
                    failed = true;
                    break;
                }
            }
            co += len;
            oo += olen;
        }
        if failed {
            break;
        }
        let mut n = out_per_frame;
        if limit > 0 && limit < n {
            n = limit;
        }
        for s in 0..n {
            for ch in 0..channels {
                let v = coefs[ch * frame_samples + s];
                pcm.push(v.round().clamp(-32768.0, 32767.0) as i16);
            }
        }
    }
    pcm.truncate(total * channels);
    Ok(Decoded { channels: channels as u16, sample_rate, pcm })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dct_matches_reference() {
        for n in [16usize, 64, 512] {
            let x: Vec<f32> = (0..n).map(|i| ((i * 7919) % 97) as f32 / 50.0 - 1.0).collect();
            let mut fast = x.clone();
            Dct::new(n).run(&mut fast);
            for (k, f) in fast.iter().enumerate() {
                let mut s = x[0] as f64;
                for j in 1..n {
                    s += x[j] as f64 * (std::f64::consts::PI * j as f64 * (2.0 * k as f64 + 1.0) / (2.0 * n as f64)).cos();
                }
                assert!((*f as f64 - s).abs() < 1e-2 * (1.0 + s.abs()), "n={n} k={k}: {f} vs {s}");
            }
        }
    }
}
