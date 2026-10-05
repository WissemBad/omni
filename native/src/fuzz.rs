//! Malformed-input tests: every parser the game files go through must answer random and mutated bytes with an error
//! (or nothing), never a panic. Deterministic (fixed seeds), quick enough for every `cargo test`.

use crate::{aloc, audio, entity, texture, vtf, wwise};
use std::panic::{catch_unwind, AssertUnwindSafe};

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64*
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545F4914F6CDD1D)
    }
    fn bytes(&mut self, n: usize) -> Vec<u8> {
        (0..n).map(|_| self.next() as u8).collect()
    }
}

/// Run `f` on random buffers of many sizes, and on mutations (flipped bytes, stretched fields, truncation) of `seed`.
fn fuzz(name: &str, seed: &[u8], f: impl Fn(&[u8])) {
    let mut r = Rng(0x9E3779B97F4A7C15);
    let mut inputs: Vec<Vec<u8>> = vec![vec![], vec![0], vec![0xFF; 64], vec![0; 4096]];
    for n in [3, 12, 16, 40, 100, 300, 1000, 5000] {
        for _ in 0..40 {
            inputs.push(r.bytes(n));
        }
    }
    for _ in 0..600 {
        let mut b = seed.to_vec();
        if b.is_empty() {
            break;
        }
        for _ in 0..(1 + r.next() % 6) {
            let i = (r.next() as usize) % b.len();
            b[i] = match r.next() % 4 {
                0 => 0xFF,
                1 => 0,
                2 => b[i] ^ (1 << (r.next() % 8)),
                _ => r.next() as u8,
            };
        }
        if r.next() % 5 == 0 {
            b.truncate((r.next() as usize) % b.len());
        }
        inputs.push(b);
    }
    for (k, input) in inputs.iter().enumerate() {
        let res = catch_unwind(AssertUnwindSafe(|| f(input)));
        assert!(res.is_ok(), "{name} panicked on input #{k} ({} bytes): {:02x?}", input.len(), &input[..input.len().min(48)]);
    }
}

fn riff(codec: u16) -> Vec<u8> {
    let mut b = b"RIFF".to_vec();
    b.extend(60u32.to_le_bytes());
    b.extend(b"WAVEfmt ");
    b.extend(24u32.to_le_bytes());
    b.extend(codec.to_le_bytes());
    b.extend(2u16.to_le_bytes());
    b.extend(48000u32.to_le_bytes());
    b.extend(192000u32.to_le_bytes());
    b.extend([4, 0, 16, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    b.extend(b"data");
    b.extend(8u32.to_le_bytes());
    b.extend([0u8; 8]);
    b
}

#[test]
fn texture_parsers_survive_garbage() {
    let mut seed = vec![0u8; 0x98 + 64];
    seed[0x0C..0x0E].copy_from_slice(&64u16.to_le_bytes());
    seed[0x0E..0x10].copy_from_slice(&64u16.to_le_bytes());
    seed[0x10..0x12].copy_from_slice(&0x4Cu16.to_le_bytes());
    seed[0x12..0x14].copy_from_slice(&3u16.to_le_bytes());
    fuzz("texture::parse_header", &seed, |b| {
        let _ = texture::parse_header(b);
    });
    fuzz("texture::decode_mips", &seed, |b| {
        let _ = texture::decode_mips(b, None);
        let _ = texture::decode_mips(b, Some(&b[b.len() / 2..]));
    });
    fuzz("texture::top_rgba", &seed, |b| {
        let _ = texture::top_rgba(b, None, 128, true);
    });
}

#[test]
fn vtf_audio_bank_collision_entity_survive_garbage() {
    let rgba = vec![200u8; 8 * 8 * 4];
    let dir = std::env::temp_dir().join(format!("omni-fuzz-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("t.vtf");
    vtf::encode_vtf(&file, &rgba, 8, 8, "dxt1", vtf::Kind::Srgb, 0, 0, 1, 0.0).unwrap();
    let vtf_bytes = std::fs::read(&file).unwrap();
    std::fs::remove_dir_all(&dir).ok();
    fuzz("vtf::decode_vtf", &vtf_bytes, |b| {
        let _ = vtf::decode_vtf(b, 64);
    });
    fuzz("audio::wem_info", &riff(0xFFFF), |b| {
        let _ = audio::wem_info(b);
    });
    fuzz("audio::convert", &riff(0x8311), |b| {
        if let Some(i) = audio::wem_info(b) {
            let _ = audio::wem_pcm(b, &i);
        }
    });
    fuzz("wwise::parse_bank", b"BKHD\x08\0\0\0\x96\0\0\0\x01\0\0\0HIRC\x04\0\0\0\0\0\0\0", |b| {
        let _ = wwise::parse_bank(b);
    });
    fuzz("aloc::parse", b"\x08\0\0\0NXSBODY\0\0\0\0\0", |b| {
        let _ = aloc::parse(b);
    });
    fuzz("entity::bin1", b"BIN1\0\x08\x01\x01\0\0\0\0\0\0\0\0", |b| {
        let _ = entity::parse_bin1(b);
        let _ = entity::parse_temp(b, &[1, 2]);
        let _ = entity::parse_tblu(b);
        let _ = entity::meta_refs(b);
    });
}
