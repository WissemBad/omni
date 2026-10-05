//! WebAssembly entry points (wasm32-unknown-unknown): the same core as the CPython module, called through
//! wasmtime by `omni/native.py`.
//!
//! ABI: the host allocates input with `omni_alloc`, calls `omni_call(op, in_ptr, in_len)` and gets back a
//! packed `(ptr << 32) | len` buffer it reads then releases with `omni_free`. Arguments and results are a flat
//! list of fields: `[u8 tag][u32 length][payload]`, tag 0 = bytes, 1 = UTF-8 string, 2 = i64, 3 = f64.
//! The first result field is a status string: "" when the call succeeded, else the error message.
//! No file system, no threads: the host passes bytes in, writes files out, and runs one instance per thread.

use crate::{aloc, audio, texture, vtf, wwise};
use std::collections::HashSet;

enum V {
    B(Vec<u8>),
    S(String),
    I(i64),
    F(f64),
}

fn parse(mut d: &[u8]) -> Result<Vec<V>, String> {
    let mut out = Vec::new();
    while !d.is_empty() {
        if d.len() < 5 {
            return Err("truncated argument".into());
        }
        let tag = d[0];
        let n = u32::from_le_bytes([d[1], d[2], d[3], d[4]]) as usize;
        let p = d.get(5..5 + n).ok_or("truncated argument")?;
        out.push(match tag {
            0 => V::B(p.to_vec()),
            1 => V::S(String::from_utf8_lossy(p).into_owned()),
            2 => V::I(i64::from_le_bytes(p.try_into().map_err(|_| "bad i64")?)),
            3 => V::F(f64::from_le_bytes(p.try_into().map_err(|_| "bad f64")?)),
            _ => return Err("bad tag".into()),
        });
        d = &d[5 + n..];
    }
    Ok(out)
}

struct Out(Vec<u8>);

impl Out {
    fn field(&mut self, tag: u8, p: &[u8]) -> &mut Self {
        self.0.push(tag);
        self.0.extend((p.len() as u32).to_le_bytes());
        self.0.extend(p);
        self
    }
    fn b(&mut self, p: &[u8]) -> &mut Self {
        self.field(0, p)
    }
    fn s(&mut self, s: &str) -> &mut Self {
        self.field(1, s.as_bytes())
    }
    fn i(&mut self, v: i64) -> &mut Self {
        self.field(2, &v.to_le_bytes())
    }
    fn f(&mut self, v: f64) -> &mut Self {
        self.field(3, &v.to_le_bytes())
    }
}

struct Args(Vec<V>, usize);

impl Args {
    fn next(&mut self) -> Result<&V, String> {
        self.1 += 1;
        self.0.get(self.1 - 1).ok_or_else(|| format!("missing argument {}", self.1))
    }
    fn bytes(&mut self) -> Result<Vec<u8>, String> {
        match self.next()? {
            V::B(b) => Ok(b.clone()),
            V::S(s) => Ok(s.as_bytes().to_vec()),
            _ => Err("expected bytes".into()),
        }
    }
    fn str(&mut self) -> Result<String, String> {
        match self.next()? {
            V::S(s) => Ok(s.clone()),
            _ => Err("expected a string".into()),
        }
    }
    fn int(&mut self) -> Result<i64, String> {
        match self.next()? {
            V::I(i) => Ok(*i),
            V::F(f) => Ok(*f as i64),
            _ => Err("expected an integer".into()),
        }
    }
    fn float(&mut self) -> Result<f64, String> {
        match self.next()? {
            V::F(f) => Ok(*f),
            V::I(i) => Ok(*i as f64),
            _ => Err("expected a number".into()),
        }
    }
}

fn tags(s: &str) -> Vec<(String, String)> {
    s.lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.to_string(), v.to_string())).collect()
}

fn info_fields(o: &mut Out, i: &audio::WemInfo) {
    o.i(i.codec as i64).i(i.channels as i64).i(i.rate as i64).i(i.samples.map(|s| s as i64).unwrap_or(-1)).s(&i.label).i(i.data_size as i64).i(i.block_align as i64);
}


fn dispatch(op: u32, a: &mut Args, o: &mut Out) -> Result<(), String> {
    match op {
        // version
        0 => {
            o.s(env!("CARGO_PKG_VERSION"));
        }
        // wem_info(data) -> codec, channels, rate, samples, label, data_size, block_align | (nothing)
        1 => {
            if let Some(i) = audio::wem_info(&a.bytes()?) {
                info_fields(o, &i);
            }
        }
        // convert_wem(data, fmt, tags "k=v\n") -> ext, bytes, info...
        2 => {
            let (data, fmt, t) = (a.bytes()?, a.str()?, a.str()?);
            let c = audio::convert(&data, &fmt, &tags(&t))?;
            o.s(c.ext).b(&c.bytes);
            info_fields(o, &c.info);
        }
        // sha1(data) -> hex
        3 => {
            o.s(&sha1_smol::Sha1::from(a.bytes()?).digest().to_string());
        }
        // bank_links(known ids (u32 LE bytes), n, [hash, bank bytes] * n) -> JSON
        4 => {
            let known: HashSet<u32> = a.bytes()?.chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
            let n = a.int()? as usize;
            let mut banks = Vec::new();
            let mut heads = Vec::new();
            for _ in 0..n {
                let h = a.int()? as u64;
                if let Some(b) = wwise::parse_bank(&a.bytes()?) {
                    heads.push((h, b.bank_id, b.media.clone()));
                    banks.push(b);
                }
            }
            let l = wwise::link(&banks, &known);
            let mut j = String::from("{\"banks\":[");
            for (k, (h, id, media)) in heads.iter().enumerate() {
                if k > 0 {
                    j.push(',');
                }
                j.push_str(&format!("[\"{h:016X}\",{id},{:?}]", media));
            }
            j.push_str("],\"media\":{");
            for (k, (m, evs)) in l.media.iter().enumerate() {
                if k > 0 {
                    j.push(',');
                }
                j.push_str(&format!("\"{m}\":["));
                for (q, (e, path)) in evs.iter().enumerate() {
                    if q > 0 {
                        j.push(',');
                    }
                    j.push_str(&format!("[{e},{path:?}]"));
                }
                j.push(']');
            }
            let sourced: Vec<u32> = l.sourced.into_iter().collect();
            j.push_str(&format!("}},\"sourced\":{:?},\"events\":{}}}", sourced, l.events));
            o.s(&j);
        }
        // encode_vtf(rgba, w, h, format, kind, max_size, flags, quality, coverage) -> vtf bytes, w, h
        5 => {
            let rgba = a.bytes()?;
            let (w, h) = (a.int()? as usize, a.int()? as usize);
            let (format, kind) = (a.str()?, vtf::Kind::parse(&a.str()?)?);
            let (max_size, flags, quality, coverage) = (a.int()? as usize, a.int()? as u32, a.int()? as u8, a.float()? as f32);
            let (data, tw, th) = vtf::encode_vtf_bytes(&rgba, w, h, &format, kind, max_size, flags, quality, coverage)?;
            o.b(&data).i(tw as i64).i(th as i64);
        }
        // decode_vtf(vtf bytes, max_dim) -> rgba, w, h
        6 => {
            let (d, m) = (a.bytes()?, a.int()? as usize);
            let (w, h, px) = vtf::decode_vtf(&d, m)?;
            o.b(&px).i(w as i64).i(h as i64);
        }
        // png_rgba(rgba, w, h, channel, max_dim) -> png
        7 => {
            let px = a.bytes()?;
            let (w, h) = (a.int()? as usize, a.int()? as usize);
            let (ch, m) = (a.str()?, a.int()? as usize);
            o.b(&vtf::png(&px, w, h, &ch, m)?);
        }
        // vtf_png(vtf bytes, channel, max_dim) -> png
        8 => {
            let (d, ch, m) = (a.bytes()?, a.str()?, a.int()? as usize);
            let (w, h, px) = vtf::decode_vtf(&d, m)?;
            o.b(&vtf::png(&px, w, h, &ch, 0)?);
        }
        // texture_rgba(text, texd (empty = none), max_dim, normal) -> rgba, w, h
        9 => {
            let (t, td, m, n) = (a.bytes()?, a.bytes()?, a.int()? as usize, a.int()? != 0);
            let (w, h, px) = texture::top_rgba(&t, if td.is_empty() { None } else { Some(&td) }, m, n)?;
            o.b(&px).i(w as i64).i(h as i64);
        }
        // texture_png(text, texd, channel, max_dim, normal) -> png, w, h
        10 => {
            let (t, td, ch, m, n) = (a.bytes()?, a.bytes()?, a.str()?, a.int()? as usize, a.int()? != 0);
            let (w, h, px) = texture::top_rgba(&t, if td.is_empty() { None } else { Some(&td) }, m, n)?;
            o.b(&vtf::png(&px, w, h, &ch, m)?).i(w as i64).i(h as i64);
        }
        // texture_header(text) -> w, h, format, mips, first_text_mip
        11 => {
            let hd = texture::parse_header(&a.bytes()?)?;
            o.i(hd.width as i64).i(hd.height as i64).s(texture::format_name(hd.fmt).unwrap_or("?")).i(hd.mips as i64).i(hd.first_text_mip as i64);
        }
        // decode_texture(text, texd) -> format, then (w, h, raw) per mip
        12 => {
            let (t, td) = (a.bytes()?, a.bytes()?);
            let (hd, mips) = texture::decode_mips(&t, if td.is_empty() { None } else { Some(&td) })?;
            o.s(texture::format_name(hd.fmt).unwrap_or("?"));
            for (w, h, raw) in mips {
                o.i(w as i64).i(h as i64).b(&raw);
            }
        }
        // to_rgba(format, w, h, data) -> rgba
        13 => {
            let (f, w, h, d) = (a.str()?, a.int()? as u32, a.int()? as u32, a.bytes()?);
            o.b(&texture::to_rgba(&f, w, h, &d)?);
        }
        // encode_dxt(rgba, w, h, alpha, quality, normal) -> blocks
        14 => {
            let (px, w, h) = (a.bytes()?, a.int()? as usize, a.int()? as usize);
            let (alpha, q, n) = (a.int()? != 0, a.int()? as u8, a.int()? != 0);
            o.b(&texture::encode_dxt(&px, w, h, alpha, q, n));
        }
        // parse_collision(data) -> JSON
        15 => {
            let c = aloc::parse(&a.bytes()?)?;
            o.s(&aloc::to_json(&c));
        }
        // wem_pcm(data) -> pcm (i16 LE interleaved), channels, rate
        16 => {
            let d = a.bytes()?;
            let i = audio::wem_info(&d).ok_or("not a RIFF/WAVE .wem")?;
            let pcm = audio::wem_pcm(&d, &i)?;
            let mut b = Vec::with_capacity(pcm.len() * 2);
            for s in pcm {
                b.extend(s.to_le_bytes());
            }
            o.b(&b).i(i.channels as i64).i(i.rate as i64);
        }
        // mip_chain(rgba, w, h, kind, max_size, coverage) -> reflectivity r, g, b, then (w, h, rgba) per level
        17 => {
            let rgba = a.bytes()?;
            let (w, h) = (a.int()? as usize, a.int()? as usize);
            let kind = vtf::Kind::parse(&a.str()?)?;
            let (max_size, coverage) = (a.int()? as usize, a.float()? as f32);
            if rgba.len() < w * h * 4 {
                return Err("image buffer too small".into());
            }
            let chain = vtf::mip_chain(&rgba[..w * h * 4], w, h, kind, max_size, coverage);
            let refl = vtf::reflectivity(&chain[chain.len().saturating_sub(4).min(chain.len() - 1)].2, kind);
            o.f(refl[0] as f64).f(refl[1] as f64).f(refl[2] as f64);
            for (mw, mh, px) in &chain {
                o.i(*mw as i64).i(*mh as i64).b(px);
            }
        }
        _ => return Err(format!("unknown op {op}")),
    }
    Ok(())
}

static PANIC: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

/// Message of the last panic (wasm32 aborts on panic: the host reads it after the trap).
#[no_mangle]
pub extern "C" fn omni_last_panic() -> u64 {
    let msg = PANIC.lock().map(|m| m.clone()).unwrap_or_default();
    leak(msg.into_bytes())
}

fn leak(v: Vec<u8>) -> u64 {
    let mut v = v.into_boxed_slice();
    let (ptr, len) = (v.as_mut_ptr() as u64, v.len() as u64);
    std::mem::forget(v);
    (ptr << 32) | len
}

#[no_mangle]
pub extern "C" fn omni_alloc(len: u32) -> *mut u8 {
    let mut v = vec![0u8; len as usize].into_boxed_slice();
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

/// # Safety
/// `ptr`/`len` must come from `omni_alloc` or an `omni_call` result.
#[no_mangle]
pub unsafe extern "C" fn omni_free(ptr: *mut u8, len: u32) {
    if !ptr.is_null() {
        drop(Box::from_raw(std::slice::from_raw_parts_mut(ptr, len as usize)));
    }
}

/// # Safety
/// `in_ptr`/`in_len` must describe a buffer from `omni_alloc`; it is released by this call.
#[no_mangle]
pub unsafe extern "C" fn omni_call(op: u32, in_ptr: *mut u8, in_len: u32) -> u64 {
    std::panic::set_hook(Box::new(|info| {
        if let Ok(mut m) = PANIC.lock() {
            *m = info.to_string();
        }
    }));
    let input = Box::from_raw(std::slice::from_raw_parts_mut(in_ptr, in_len as usize));
    let mut out = Out(Vec::new());
    let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<Out, String> {
        let mut a = Args(parse(&input)?, 0);
        let mut body = Out(Vec::new());
        dispatch(op, &mut a, &mut body)?;
        Ok(body)
    }));
    match res {
        Ok(Ok(body)) => {
            out.s("");
            out.0.extend(body.0);
        }
        Ok(Err(e)) => {
            out.s(if e.is_empty() { "error" } else { &e });
        }
        Err(_) => {
            out.s("internal error (panic)");
        }
    }
    leak(out.0)
}
