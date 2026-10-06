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

// ------------------------------------------------------------------------------------------------ Unreal
fn empty_game() -> crate::unreal::Game {
    use crate::unreal::iostore::MAGIC;
    let dir = std::env::temp_dir().join(format!("omni-fuzz-ue-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut toc = MAGIC.to_vec();
    toc.push(5);
    toc.extend([0u8; 3]);
    toc.extend(0x90u32.to_le_bytes()); // header size
    toc.extend(0u32.to_le_bytes()); // entries
    toc.extend(0u32.to_le_bytes()); // blocks
    toc.extend(12u32.to_le_bytes()); // block entry size
    toc.extend(0u32.to_le_bytes()); // methods
    toc.extend(32u32.to_le_bytes()); // method length
    toc.extend(65536u32.to_le_bytes()); // block size
    toc.resize(0x90, 0);
    std::fs::write(dir.join("fuzz.utoc"), &toc).unwrap();
    std::fs::write(dir.join("fuzz.ucas"), b"").unwrap();
    let g = crate::unreal::Game::open(&dir, "5.1", None).unwrap();
    std::fs::remove_dir_all(&dir).ok();
    g
}

fn fake_package(data: &[u8], names: Vec<String>) -> crate::unreal::zen::Package {
    use crate::unreal::zen::{Export, Package, INDEX_NULL};
    Package {
        data: data.to_vec(),
        names,
        name: "/Game/Fuzz".into(),
        flags: 0,
        header_size: 0,
        cooked_header_size: 0,
        ue5_version: 1008,
        imported_hashes: vec![],
        imports: vec![1 << 62],
        exports: vec![Export {
            name: "Fuzz".into(),
            outer: INDEX_NULL,
            class: INDEX_NULL,
            super_index: INDEX_NULL,
            template: INDEX_NULL,
            public_hash: 0,
            flags: 0,
            serial_offset: 0,
            serial_size: data.len() as u64,
            start: 0,
            end: data.len(),
        }],
        bulk: vec![],
        imported_package_names: vec![],
        exports_end: data.len(),
    }
}

const SCHEMA: &str = "omni-mappings 1\nS Object -\nS StaticMesh Object\nP 1 StaticMaterials Array<Struct<StaticMaterial>>\nP 1 LightMapResolution Int\nP 2 Flags Bool\nP 1 Bounds Struct<BoxSphereBounds>\nP 1 Tags Map<Name,Str>\nP 1 Mode Enum<EMode,Byte>\nS StaticMaterial -\nP 1 MaterialInterface Object\nP 1 MaterialSlotName Name\nP 1 UVChannelData Struct<MeshUVChannelInfo>\nS MeshUVChannelInfo -\nP 1 bInitialized Bool\nP 4 LocalUVDensities Float\nS BoxSphereBounds -\nP 1 Origin Struct<Vector>\nP 1 SphereRadius Double\nS SkeletalMesh Object\nP 1 bHasVertexColors Bool\nS Texture2D Object\nS SoundWave Object\nE EMode A=0 B=1\n";

#[test]
fn unreal_parsers_survive_garbage() {
    use crate::unreal::{iostore, props, reflect, zen};
    let dir = std::env::temp_dir().join(format!("omni-fuzz-toc-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("t.ucas"), vec![0u8; 256]).unwrap();
    let mut toc = iostore::MAGIC.to_vec();
    toc.push(5);
    toc.extend([0u8; 3]);
    for v in [0x90u32, 1, 1, 12, 1, 32, 64] {
        toc.extend(v.to_le_bytes());
    }
    toc.resize(0x90, 0);
    toc.extend([1u8; 12]); // chunk id
    toc.extend([0u8, 0, 0, 0, 0, 0, 0, 0, 0, 16]); // offset/length
    toc.extend([0u8, 0, 0, 0, 0, 16, 0, 0, 16, 0, 0, 0]); // block
    let mut method = b"Zlib".to_vec();
    method.resize(32, 0);
    toc.extend(method);
    fuzz("iostore::Container", &toc, |b| {
        let p = dir.join("t.utoc");
        std::fs::write(&p, b).unwrap();
        if let Ok(c) = iostore::Container::open(&p, None) {
            for i in 0..c.chunk_ids().len().min(4) as u32 {
                let _ = c.read_index(i);
                let _ = c.read_partial(i, 3, 5);
            }
        }
    });
    std::fs::remove_dir_all(&dir).ok();
    fuzz("iostore::container_header", b"nCoI\x02\0\0\0\0\0\0\0\0\0\0\0\x01\0\0\0\x01\0\0\0\0\0\0\0\0\0\0\0\x18\0\0\0", |b| {
        let _ = iostore::parse_container_header(b);
    });
    fuzz("iostore::directory_index", b"\x05\0\0\0../\0\x01\0\0\0\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\0\0\0\0\x01\0\0\0\0\0\0\0\xff\xff\xff\xff\0\0\0\0\x01\0\0\0\x02\0\0\0a\0", |b| {
        let _ = iostore::parse_directory_index(b);
    });
    let mut pkg = vec![0u8; 64];
    pkg[4..8].copy_from_slice(&64u32.to_le_bytes());
    for (i, off) in [(24usize, 60u32), (28, 60), (32, 60), (36, 60), (40, 60)] {
        pkg[i..i + 4].copy_from_slice(&off.to_le_bytes());
    }
    fuzz("zen::Package::parse", &pkg, |b| {
        for hv in [1, 2, 3, 5] {
            let _ = zen::Package::parse(b.to_vec(), hv, 1008);
            let _ = zen::Package::parse(b.to_vec(), hv, 1012);
        }
    });
    fuzz("zen::ScriptObjects::parse", b"\x01\0\0\0\x04\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\x04abcd\x01\0\0\0", |b| {
        let _ = zen::ScriptObjects::parse(b);
    });
    let mut pe = vec![0u8; 0x400];
    pe[0..2].copy_from_slice(b"MZ");
    pe[0x3C..0x40].copy_from_slice(&0x80u32.to_le_bytes());
    pe[0x80..0x84].copy_from_slice(b"PE\0\0");
    pe[0x86..0x88].copy_from_slice(&1u16.to_le_bytes());
    pe[0x94..0x96].copy_from_slice(&0xF0u16.to_le_bytes());
    pe[0x98..0x9A].copy_from_slice(&0x20Bu16.to_le_bytes());
    fuzz("reflect::extract", &pe, |b| {
        let _ = reflect::Image::parse(b);
        let _ = reflect::extract(b);
    });
    fuzz("reflect::Schema::from_text", SCHEMA.as_bytes(), |b| {
        let _ = reflect::Schema::from_text(&String::from_utf8_lossy(b));
    });
    let schema = reflect::Schema::from_text(SCHEMA).unwrap();
    let names: Vec<String> = ["None", "A", "B"].iter().map(|s| s.to_string()).collect();
    let game = empty_game();
    let mut seed = vec![0x02u8, 0x7F, 0xFF];
    seed.extend(vec![1u8; 200]);
    fuzz("props + assets", &seed, |b| {
        let ctx = props::Ctx { schema: &schema, names: &names, ue5: 1008 };
        let _ = props::read_object(&ctx, "StaticMesh", b, false);
        let mut r = crate::unreal::reader::Reader::new(b);
        let _ = ctx.properties("StaticMesh", &mut r);
        let p = fake_package(b, names.clone());
        let _ = crate::unreal::assets::read_texture(&game, &ctx, &p, 0, "Texture2D");
        let _ = crate::unreal::assets::read_sound(&game, &ctx, &p, 0, "SoundWave");
        let _ = crate::unreal::mesh::read_static_mesh(&game, &ctx, &p, 0, "Object", 2);
        let _ = crate::unreal::skel::read_skeletal_mesh(&game, &ctx, &p, 0, "Object", 2);
        for pf in ["PF_DXT1", "PF_B8G8R8A8", "PF_FloatRGBA", "PF_BC6H", "PF_G8"] {
            let _ = crate::unreal::assets::convert_mip(pf, 4, 4, b);
        }
    });
    let mut abeu = b"ABEU\x01\x02\0\0\x80\xbb\0\0\x00\x10\0\0\0\x10\0\0\x01\0\0\0\0\0\0\0\0\0".to_vec();
    abeu.extend([0x99, 0x99, 0x20, 0]);
    abeu.extend(vec![0x5Au8; 32]);
    fuzz("binka::decode", &abeu, |b| {
        let _ = crate::binka::decode(b);
        let _ = audio::wem_info(b);
    });
}
