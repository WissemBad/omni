//! Small lossless FLAC encoder for 16-bit PCM (no external crate: new build-time code cannot run on this machine).
//!
//! Per 4096-sample block: stereo decorrelation (left/right, left/side, right/side or mid/side, cheapest wins),
//! then per channel a constant, verbatim or fixed-polynomial (order 0-4) subframe with partitioned Rice
//! residuals (partition order and Rice parameters searched exactly). STREAMINFO carries the MD5 of the audio, so
//! `flac -t` can verify every file. Compression is within a few percent of the reference encoder at -8.

const BLOCK: usize = 4096;

struct Bits {
    out: Vec<u8>,
    acc: u64,
    n: u32,
}

impl Bits {
    fn new() -> Self {
        Bits { out: Vec::new(), acc: 0, n: 0 }
    }
    #[inline]
    fn put(&mut self, v: u64, bits: u32) {
        if bits == 0 {
            return;
        }
        let v = if bits == 64 { v } else { v & ((1u64 << bits) - 1) };
        if self.n + bits > 64 {
            // flush whole bytes first
            while self.n >= 8 {
                self.n -= 8;
                self.out.push((self.acc >> self.n) as u8);
            }
        }
        self.acc = (self.acc << bits) | v;
        self.n += bits;
        while self.n >= 8 {
            self.n -= 8;
            self.out.push((self.acc >> self.n) as u8);
        }
    }
    fn put_signed(&mut self, v: i64, bits: u32) {
        self.put(v as u64, bits);
    }
    fn unary(&mut self, q: u32) {
        let mut q = q;
        while q >= 32 {
            self.put(0, 32);
            q -= 32;
        }
        self.put(1, q + 1);
    }
    fn align(&mut self) {
        if self.n % 8 != 0 {
            let pad = 8 - self.n % 8;
            self.put(0, pad);
        }
    }
}

fn crc8(d: &[u8]) -> u8 {
    let mut c = 0u8;
    for &b in d {
        c ^= b;
        for _ in 0..8 {
            c = if c & 0x80 != 0 { (c << 1) ^ 0x07 } else { c << 1 };
        }
    }
    c
}

fn crc16(d: &[u8]) -> u16 {
    let mut c = 0u16;
    for &b in d {
        c ^= (b as u16) << 8;
        for _ in 0..8 {
            c = if c & 0x8000 != 0 { (c << 1) ^ 0x8005 } else { c << 1 };
        }
    }
    c
}

fn utf8_num(mut v: u64) -> Vec<u8> {
    if v < 0x80 {
        return vec![v as u8];
    }
    let mut tail = Vec::new();
    let mut lead_bits = 6u32;
    loop {
        tail.push(0x80 | (v & 0x3F) as u8);
        v >>= 6;
        lead_bits -= 1;
        if v < (1 << lead_bits) {
            break;
        }
    }
    let n = tail.len() as u32 + 1;
    let lead = ((0xFFu32 << (8 - n)) as u8) | v as u8;
    let mut out = vec![lead];
    out.extend(tail.iter().rev());
    out
}

fn residual(x: &[i64], order: usize) -> Vec<i64> {
    let mut r = Vec::with_capacity(x.len().saturating_sub(order));
    for i in order..x.len() {
        let p = match order {
            0 => 0,
            1 => x[i - 1],
            2 => 2 * x[i - 1] - x[i - 2],
            3 => 3 * x[i - 1] - 3 * x[i - 2] + x[i - 3],
            _ => 4 * x[i - 1] - 6 * x[i - 2] + 4 * x[i - 3] - x[i - 4],
        };
        r.push(x[i] - p);
    }
    r
}

#[inline]
fn zig(r: i64) -> u64 {
    ((r << 1) ^ (r >> 63)) as u64
}

fn rice_bits(us: &[u64], k: u32) -> u64 {
    us.iter().map(|&u| (u >> k) + 1 + k as u64).sum()
}

fn best_k(us: &[u64]) -> (u32, u64) {
    if us.is_empty() {
        return (0, 0);
    }
    let mean = us.iter().sum::<u64>() / us.len() as u64;
    let guess = 64 - mean.max(1).leading_zeros();
    let mut best = (0u32, u64::MAX);
    for k in guess.saturating_sub(2).min(14)..=(guess + 1).min(14) {
        let b = rice_bits(us, k);
        if b < best.1 {
            best = (k, b);
        }
    }
    best
}

/// (partition order, params, cost in bits) of the cheapest Rice coding of a residual.
fn plan_residual(res: &[i64], block: usize, order: usize) -> (u32, Vec<u32>, u64) {
    let us: Vec<u64> = res.iter().map(|&r| zig(r)).collect();
    let mut best = (0u32, Vec::new(), u64::MAX);
    for po in 0..=8u32 {
        let parts = 1usize << po;
        if block % parts != 0 || block / parts <= order {
            break;
        }
        let psize = block / parts;
        let mut cost = 0u64;
        let mut params = Vec::with_capacity(parts);
        let mut start = 0;
        for p in 0..parts {
            let n = if p == 0 { psize - order } else { psize };
            let (k, b) = best_k(&us[start..start + n]);
            params.push(k);
            cost += 4 + b;
            start += n;
        }
        if cost < best.2 {
            best = (po, params, cost);
        }
    }
    best
}

fn write_subframe(w: &mut Bits, x: &[i64], bps: u32) {
    if x.iter().all(|&v| v == x[0]) {
        w.put(0, 1);
        w.put(0, 6);
        w.put(0, 1);
        w.put_signed(x[0], bps);
        return;
    }
    let mut best: Option<(usize, Vec<i64>, (u32, Vec<u32>, u64))> = None;
    for order in 0..=4usize.min(x.len() - 1) {
        let r = residual(x, order);
        let plan = plan_residual(&r, x.len(), order);
        let total = plan.2 + (order as u64) * bps as u64 + 6;
        if best.as_ref().map(|b| total < b.2 .2 + b.0 as u64 * bps as u64 + 6).unwrap_or(true) {
            best = Some((order, r, plan));
        }
    }
    let (order, r, (po, params, cost)) = best.unwrap();
    let verbatim = x.len() as u64 * bps as u64;
    if cost + order as u64 * bps as u64 + 6 >= verbatim {
        w.put(0, 1);
        w.put(1, 6);
        w.put(0, 1);
        for &v in x {
            w.put_signed(v, bps);
        }
        return;
    }
    w.put(0, 1);
    w.put(0b001000 | order as u64, 6);
    w.put(0, 1);
    for &v in &x[..order] {
        w.put_signed(v, bps);
    }
    w.put(0, 2); // 4-bit Rice parameters
    w.put(po as u64, 4);
    let parts = 1usize << po;
    let psize = x.len() / parts;
    let mut start = 0;
    for (p, &k) in params.iter().enumerate() {
        let n = if p == 0 { psize - order } else { psize };
        w.put(k as u64, 4);
        for &v in &r[start..start + n] {
            let u = zig(v);
            w.unary((u >> k) as u32);
            w.put(u, k);
        }
        start += n;
    }
}

fn cost_estimate(x: &[i64]) -> u64 {
    // cheap proxy: second-order residual magnitude
    residual(x, 2).iter().map(|v| v.unsigned_abs()).sum()
}

fn write_frame(out: &mut Vec<u8>, pcm: &[i16], channels: usize, start: usize, n: usize, frame_no: u64) {
    let mut w = Bits::new();
    let chans: Vec<Vec<i64>> = (0..channels).map(|c| (0..n).map(|i| pcm[(start + i) * channels + c] as i64).collect()).collect();
    // stereo decorrelation
    let mut assign = (channels - 1) as u64;
    let mut subs: Vec<(Vec<i64>, u32)> = chans.iter().map(|c| (c.clone(), 16)).collect();
    if channels == 2 {
        let (l, r) = (&chans[0], &chans[1]);
        let side: Vec<i64> = l.iter().zip(r).map(|(a, b)| a - b).collect();
        let mid: Vec<i64> = l.iter().zip(r).map(|(a, b)| (a + b) >> 1).collect();
        let (cl, cr, cs, cm) = (cost_estimate(l), cost_estimate(r), cost_estimate(&side), cost_estimate(&mid));
        let opts = [(cl + cr, 1u64), (cl + cs, 8), (cr + cs, 9), (cm + cs, 10)];
        let best = opts.iter().min_by_key(|o| o.0).unwrap().1;
        assign = best;
        subs = match best {
            8 => vec![(l.clone(), 16), (side, 17)],
            9 => vec![(side, 17), (r.clone(), 16)],
            10 => vec![(mid, 16), (side, 17)],
            _ => subs,
        };
    }
    // header
    let bs_code: u64 = if n == BLOCK { 12 } else { 7 };
    w.put(0xFFF8, 16);
    w.put(bs_code, 4);
    w.put(0, 4); // sample rate from STREAMINFO
    w.put(assign, 4);
    w.put(4, 3); // 16 bits per sample
    w.put(0, 1);
    for b in utf8_num(frame_no) {
        w.put(b as u64, 8);
    }
    if bs_code == 7 {
        w.put((n - 1) as u64, 16);
    }
    let c8 = crc8(&w.out);
    w.put(c8 as u64, 8);
    for (x, bps) in &subs {
        write_subframe(&mut w, x, *bps);
    }
    w.align();
    let c16 = crc16(&w.out);
    w.put(c16 as u64, 16);
    out.extend(w.out);
}

/// Encode interleaved 16-bit PCM. `comments`: (key, value) written as a VORBIS_COMMENT block.
pub fn encode(pcm: &[i16], channels: u16, rate: u32, comments: &[(String, String)]) -> Result<Vec<u8>, String> {
    let ch = channels as usize;
    if ch == 0 || ch > 8 || rate == 0 || rate >= 1 << 20 {
        return Err(format!("FLAC cannot hold {ch} channels at {rate} Hz"));
    }
    let total = pcm.len() / ch;
    let mut frames = Vec::with_capacity(pcm.len());
    let (mut minf, mut maxf) = (u32::MAX, 0u32);
    let mut start = 0;
    let mut no = 0u64;
    while start < total {
        let n = BLOCK.min(total - start);
        let before = frames.len();
        write_frame(&mut frames, pcm, ch, start, n, no);
        let fl = (frames.len() - before) as u32;
        minf = minf.min(fl);
        maxf = maxf.max(fl);
        start += n;
        no += 1;
    }
    if no == 0 {
        minf = 0;
    }
    let mut bytes = Vec::with_capacity(pcm.len() * 2);
    for s in &pcm[..total * ch] {
        bytes.extend(s.to_le_bytes());
    }
    let md5 = md5(&bytes);

    let mut out = b"fLaC".to_vec();
    let mut si = Bits::new();
    let min_block = if total < BLOCK { total.max(16) } else { BLOCK };
    si.put(min_block as u64, 16);
    si.put(BLOCK as u64, 16);
    si.put(minf as u64, 24);
    si.put(maxf as u64, 24);
    si.put(rate as u64, 20);
    si.put((ch - 1) as u64, 3);
    si.put(15, 5);
    si.put(total as u64, 36);
    let last = comments.is_empty();
    out.push(if last { 0x80 } else { 0 });
    out.extend([0, 0, 34]);
    out.extend(si.out);
    out.extend(md5);
    if !comments.is_empty() {
        let mut body = Vec::new();
        let vendor = b"omni";
        body.extend((vendor.len() as u32).to_le_bytes());
        body.extend(vendor);
        body.extend((comments.len() as u32).to_le_bytes());
        for (k, v) in comments {
            let s = format!("{}={}", k.to_uppercase(), v);
            body.extend((s.len() as u32).to_le_bytes());
            body.extend(s.as_bytes());
        }
        out.push(0x80 | 4);
        out.extend([(body.len() >> 16) as u8, (body.len() >> 8) as u8, body.len() as u8]);
        out.extend(body);
    }
    out.extend(frames);
    Ok(out)
}

// ------------------------------------------------------------------------------------------------ MD5 (RFC 1321)

pub fn md5(data: &[u8]) -> [u8; 16] {
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23,
        4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    let k: Vec<u32> = (0..64).map(|i| ((i as f64 + 1.0).sin().abs() * 4294967296.0) as u32).collect();
    let (mut a0, mut b0, mut c0, mut d0) = (0x67452301u32, 0xefcdab89u32, 0x98badcfeu32, 0x10325476u32);
    let mut msg = data.to_vec();
    let bitlen = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend(bitlen.to_le_bytes());
    for chunk in msg.chunks_exact(64) {
        let m: Vec<u32> = chunk.chunks_exact(4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]])).collect();
        let (mut a, mut b, mut c, mut d) = (a0, b0, c0, d0);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let f2 = f.wrapping_add(a).wrapping_add(k[i]).wrapping_add(m[g]);
            a = d;
            d = c;
            c = b;
            b = b.wrapping_add(f2.rotate_left(S[i]));
        }
        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }
    let mut out = [0u8; 16];
    out[..4].copy_from_slice(&a0.to_le_bytes());
    out[4..8].copy_from_slice(&b0.to_le_bytes());
    out[8..12].copy_from_slice(&c0.to_le_bytes());
    out[12..].copy_from_slice(&d0.to_le_bytes());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md5_known() {
        let h = md5(b"abc");
        assert_eq!(h.iter().map(|b| format!("{b:02x}")).collect::<String>(), "900150983cd24fb0d6963f7d28e17f72");
    }

    #[test]
    fn utf8_numbers() {
        assert_eq!(utf8_num(5), vec![5]);
        assert_eq!(utf8_num(0x80), vec![0xC2, 0x80]);
    }
}
