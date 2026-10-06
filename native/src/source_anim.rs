//! Source engine animations (compiled .mdl v44-49 with the optional .ani file): the skeleton, the sequences and the
//! bone transforms of every frame of an animation.
//!
//! Layouts follow the Source SDK (`studio.h`, `bone_setup.cpp`): `mstudiobone_t` (216 bytes), `mstudioanimdesc_t`
//! (100), `mstudioseqdesc_t` (212), `mstudioanim_t` chains (one record per animated bone: raw 48/64-bit
//! quaternions, 16-bit vectors, or run-length encoded streams scaled per bone), optional sections and external
//! animation blocks. Every read is bounds checked: a corrupt file is an error, never a panic.

const MAX_BONES: usize = 1024;
const MAX_FRAMES: usize = 20_000;
const MAX_FLOATS: usize = 48_000_000;

const RAWPOS: u8 = 0x01;
const RAWROT: u8 = 0x02;
const ANIMPOS: u8 = 0x04;
const ANIMROT: u8 = 0x08;
const DELTA: u8 = 0x10;
const RAWROT2: u8 = 0x20;

pub const ANIM_DELTA: i32 = 0x0004;
const ANIM_ALLZEROS: i32 = 0x0020;
const ANIM_FRAMEANIM: i32 = 0x0040;

type R<T> = Result<T, String>;

fn rd<const N: usize>(d: &[u8], o: usize) -> R<[u8; N]> {
    d.get(o..o.checked_add(N).ok_or("offset overflow")?)
        .and_then(|s| s.try_into().ok())
        .ok_or_else(|| format!("read past the end at {o}"))
}
fn u8_at(d: &[u8], o: usize) -> R<u8> {
    Ok(rd::<1>(d, o)?[0])
}
fn i16_at(d: &[u8], o: usize) -> R<i16> {
    Ok(i16::from_le_bytes(rd(d, o)?))
}
fn u16_at(d: &[u8], o: usize) -> R<u16> {
    Ok(u16::from_le_bytes(rd(d, o)?))
}
fn i32_at(d: &[u8], o: usize) -> R<i32> {
    Ok(i32::from_le_bytes(rd(d, o)?))
}
fn f32_at(d: &[u8], o: usize) -> R<f32> {
    Ok(f32::from_le_bytes(rd(d, o)?))
}
fn v3_at(d: &[u8], o: usize) -> R<[f32; 3]> {
    Ok([f32_at(d, o)?, f32_at(d, o + 4)?, f32_at(d, o + 8)?])
}
/// Offset `base + rel` (rel may be negative: strings and indexes are relative to their structure).
fn at(base: usize, rel: i32) -> R<usize> {
    let v = base as i64 + rel as i64;
    if v < 0 {
        Err("negative offset".into())
    } else {
        Ok(v as usize)
    }
}
fn cstr(d: &[u8], o: usize) -> R<String> {
    let s = d.get(o..).ok_or("string past the end")?;
    let end = s.iter().take(256).position(|&c| c == 0).unwrap_or(s.len().min(256));
    Ok(s[..end].iter().map(|&c| c as char).collect())
}
fn count(d: &[u8], o: usize, max: usize) -> R<usize> {
    let n = i32_at(d, o)?;
    if n < 0 || n as usize > max {
        return Err(format!("implausible count {n}"));
    }
    Ok(n as usize)
}

#[derive(Clone, Debug)]
pub struct Bone {
    pub name: String,
    pub parent: i32,
    pub pos: [f32; 3],
    pub quat: [f32; 4],
    pub rot: [f32; 3],
    pub posscale: [f32; 3],
    pub rotscale: [f32; 3],
}

#[derive(Clone, Debug)]
pub struct Seq {
    pub name: String,
    pub activity: String,
    pub flags: i32,
    /// animation (index in the model's own list) of the central blend
    pub anim: i32,
    pub blends: i32,
    /// every blend's animation (a movement sequence blends several directions or speeds)
    pub blend_anims: Vec<i32>,
    pub fps: f32,
    pub frames: i32,
    pub anim_flags: i32,
}

#[derive(Clone, Debug, Default)]
pub struct Info {
    pub name: String,
    pub bones: Vec<Bone>,
    pub seqs: Vec<Seq>,
    pub includes: Vec<String>,
    pub anims: usize,
    pub needs_ani: bool,
}

fn header(d: &[u8]) -> R<()> {
    if d.get(..4) != Some(b"IDST") {
        return Err("not a Source model".into());
    }
    let v = i32_at(d, 4)?;
    if !(44..=49).contains(&v) {
        return Err(format!("unsupported model version {v}"));
    }
    Ok(())
}

fn bones(d: &[u8]) -> R<Vec<Bone>> {
    let n = count(d, 156, MAX_BONES)?;
    let base = i32_at(d, 160)?;
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let o = at(0, base)? + i * 216;
        out.push(Bone {
            name: cstr(d, at(o, i32_at(d, o)?)?)?,
            parent: i32_at(d, o + 4)?,
            pos: v3_at(d, o + 32)?,
            quat: [f32_at(d, o + 44)?, f32_at(d, o + 48)?, f32_at(d, o + 52)?, f32_at(d, o + 56)?],
            rot: v3_at(d, o + 60)?,
            posscale: v3_at(d, o + 72)?,
            rotscale: v3_at(d, o + 84)?,
        });
    }
    Ok(out)
}

/// Offset of the animation description `i`.
fn desc_off(d: &[u8], i: usize) -> R<usize> {
    let n = count(d, 180, 1 << 20)?;
    if i >= n {
        return Err(format!("animation {i} of {n}"));
    }
    Ok(at(0, i32_at(d, 184)?)? + i * 100)
}

pub fn parse(d: &[u8]) -> R<Info> {
    header(d)?;
    let mut info = Info { name: cstr(d, 12)?, bones: bones(d)?, ..Default::default() };
    let nanim = count(d, 180, 1 << 20)?;
    info.anims = nanim;
    let nseq = count(d, 188, 1 << 20)?;
    let seqbase = at(0, i32_at(d, 192)?)?;
    for i in 0..nseq {
        let o = seqbase + i * 212;
        let blends = i32_at(d, o + 56)?;
        let ai = at(o, i32_at(d, o + 60)?)?;
        let (gx, gy) = (i32_at(d, o + 68)?.max(1), i32_at(d, o + 72)?.max(1));
        // the central blend: a walk cycle's blends span left to right, the middle one runs straight
        let blend = if blends > 0 { ((gy as i64 / 2) * gx as i64 + gx as i64 / 2).clamp(0, blends as i64 - 1) as i32 } else { 0 };
        let anim = if blends > 0 { i16_at(d, ai + 2 * blend as usize).map(|v| v as i32).unwrap_or(-1) } else { -1 };
        let blend_anims: Vec<i32> = (0..blends.clamp(0, 64) as usize).filter_map(|k| i16_at(d, ai + 2 * k).ok().map(|v| v as i32)).collect();
        let (fps, frames, anim_flags) = if anim >= 0 && (anim as usize) < nanim {
            let a = desc_off(d, anim as usize)?;
            if i32_at(d, a + 52)? > 0 || i32_at(d, a + 80)? != 0 {
                info.needs_ani = true;
            }
            (f32_at(d, a + 8)?, i32_at(d, a + 16)?, i32_at(d, a + 12)?)
        } else {
            (0.0, 0, 0)
        };
        info.seqs.push(Seq {
            name: cstr(d, at(o, i32_at(d, o + 4)?)?)?,
            activity: match i32_at(d, o + 8)? {
                0 => String::new(),
                r => cstr(d, at(o, r)?).unwrap_or_default(),
            },
            flags: i32_at(d, o + 12)?,
            anim,
            blends,
            blend_anims,
            fps,
            frames,
            anim_flags,
        });
    }
    let ninc = count(d, 336, 4096)?;
    let incbase = at(0, i32_at(d, 340)?)?;
    for i in 0..ninc {
        let o = incbase + i * 8;
        info.includes.push(cstr(d, at(o, i32_at(d, o + 4)?)?)?);
    }
    Ok(info)
}

pub struct Sampled {
    pub frames: usize,
    pub bones: usize,
    pub fps: f32,
    pub flags: i32,
    /// frames x bones x 7: position (3, model units) then quaternion (x, y, z, w), local to the parent bone
    pub data: Vec<f32>,
}

fn half(h: u16) -> f32 {
    let (s, e, m) = ((h >> 15) as u32, ((h >> 10) & 0x1F) as u32, (h & 0x3FF) as u32);
    let bits = if e == 0 {
        if m == 0 {
            s << 31
        } else {
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

fn angle_quat(a: [f32; 3]) -> [f32; 4] {
    let (sy, cy) = (a[2] * 0.5).sin_cos();
    let (sp, cp) = (a[1] * 0.5).sin_cos();
    let (sr, cr) = (a[0] * 0.5).sin_cos();
    let (sr_cp, cr_sp) = (sr * cp, cr * sp);
    let (cr_cp, sr_sp) = (cr * cp, sr * sp);
    [sr_cp * cy - cr_sp * sy, cr_sp * cy + sr_cp * sy, cr_cp * sy - sr_sp * cy, cr_cp * cy + sr_sp * sy]
}

fn quat48(d: &[u8], o: usize) -> R<[f32; 4]> {
    let (x, y, z) = (u16_at(d, o)?, u16_at(d, o + 2)?, u16_at(d, o + 4)?);
    let q = [(x as i32 - 32768) as f32 / 32768.0, (y as i32 - 32768) as f32 / 32768.0, ((z & 0x7FFF) as i32 - 16384) as f32 / 16384.0];
    let w = (1.0 - q[0] * q[0] - q[1] * q[1] - q[2] * q[2]).max(0.0).sqrt();
    Ok([q[0], q[1], q[2], if z & 0x8000 != 0 { -w } else { w }])
}

fn quat64(d: &[u8], o: usize) -> R<[f32; 4]> {
    let v = u64::from_le_bytes(rd(d, o)?);
    let f = |s: u32| (((v >> s) & 0x1F_FFFF) as i64 - 1_048_576) as f32 / 1_048_576.5;
    let q = [f(0), f(21), f(42)];
    let w = (1.0 - q[0] * q[0] - q[1] * q[1] - q[2] * q[2]).max(0.0).sqrt();
    Ok([q[0], q[1], q[2], if v >> 63 != 0 { -w } else { w }])
}

/// One value of a run-length stream at `frame` (`ExtractAnimValue` of the SDK).
fn rle(d: &[u8], mut p: usize, frame: i32, scale: f32) -> R<f32> {
    let mut k = frame;
    loop {
        let (valid, total) = (u8_at(d, p)? as i32, u8_at(d, p + 1)? as i32);
        if total > k {
            let idx = if valid > k { 2 * (k as usize + 1) } else { 2 * valid as usize };
            return Ok(i16_at(d, p + idx)? as f32 * scale);
        }
        k -= total;
        p += (valid as usize + 1) * 2;
        if u8_at(d, p + 1)? == 0 {
            return Ok(0.0);
        }
    }
}

struct Channel {
    flags: u8,
    at: usize,
}

/// The chain of `mstudioanim_t` records starting at `p` as (bone -> record).
fn chain(buf: &[u8], mut p: usize, nbones: usize) -> R<Vec<Option<Channel>>> {
    let mut out: Vec<Option<Channel>> = (0..nbones).map(|_| None).collect();
    for _ in 0..nbones * 2 + 4 {
        let (bone, flags, next) = (u8_at(buf, p)? as usize, u8_at(buf, p + 1)?, i16_at(buf, p + 2)?);
        if bone < nbones {
            out[bone] = Some(Channel { flags, at: p });
        }
        if next <= 0 {
            break;
        }
        p += next as usize;
    }
    Ok(out)
}

/// Bone transforms of every frame of animation `anim` (``ani``: the external animation file, if the model has one).
pub fn sample(d: &[u8], ani: Option<&[u8]>, anim: usize) -> R<Sampled> {
    header(d)?;
    let bs = bones(d)?;
    let a = desc_off(d, anim)?;
    let frames = i32_at(d, a + 16)?;
    if frames < 1 || frames as usize > MAX_FRAMES || bs.len() * frames as usize * 7 > MAX_FLOATS {
        return Err(format!("implausible animation size ({frames} frames, {} bones)", bs.len()));
    }
    let frames = frames as usize;
    let (fps, flags) = (f32_at(d, a + 8)?, i32_at(d, a + 12)?);
    let delta_anim = flags & ANIM_DELTA != 0;
    let nb = bs.len();
    let mut data = vec![0f32; frames * nb * 7];
    let rest = |b: &Bone| -> ([f32; 3], [f32; 4]) { if delta_anim { ([0.0; 3], [0.0, 0.0, 0.0, 1.0]) } else { (b.pos, b.quat) } };
    if flags & (ANIM_ALLZEROS | ANIM_FRAMEANIM) != 0 {
        for f in 0..frames {
            for (i, b) in bs.iter().enumerate() {
                let (p, q) = rest(b);
                data[(f * nb + i) * 7..][..3].copy_from_slice(&p);
                data[(f * nb + i) * 7 + 3..][..4].copy_from_slice(&q);
            }
        }
        return Ok(Sampled { frames, bones: nb, fps, flags, data });
    }
    let (block, index) = (i32_at(d, a + 52)?, i32_at(d, a + 56)?);
    let (section_index, section_frames) = (i32_at(d, a + 80)?, i32_at(d, a + 84)?);
    let nblocks = count(d, 352, 1 << 16)?;
    let blocks_at = at(0, i32_at(d, 356)?)?;
    // (buffer, offset of the chain) of a (block, index) pair
    let locate = |block: i32, index: i32| -> R<Option<(&[u8], usize)>> {
        if block < 0 {
            return Ok(None);
        }
        if block == 0 {
            return Ok(Some((d, at(a, index)?)));
        }
        if block as usize >= nblocks {
            return Err(format!("animation block {block} of {nblocks}"));
        }
        let ani = ani.ok_or("this animation lives in the .ani file, which is missing")?;
        Ok(Some((ani, at(0, i32_at(d, blocks_at + block as usize * 8)?)? + index.max(0) as usize)))
    };
    // chains per section (one entry when the animation is not split in sections), built when first needed
    let mut chains: std::collections::HashMap<usize, (Vec<Option<Channel>>, &[u8])> = std::collections::HashMap::new();
    for f in 0..frames {
        let (key, frame) = if section_frames > 0 {
            let (sf, nf) = (section_frames, frames as i32);
            if nf > sf && f as i32 == nf - 1 { ((nf / sf) as usize + 1, 0) } else { ((f as i32 / sf) as usize, f as i32 % sf) }
        } else {
            (0, f as i32)
        };
        if !chains.contains_key(&key) {
            let (blk, idx) = if section_frames > 0 {
                let e = at(a, section_index)? + key * 8;
                (i32_at(d, e)?, i32_at(d, e + 4)?)
            } else {
                (block, index)
            };
            let entry = match locate(blk, idx)? {
                Some((b, p)) => (chain(b, p, nb)?, b),
                None => ((0..nb).map(|_| None).collect(), d),
            };
            chains.insert(key, entry);
        }
        let (chn, buf) = &chains[&key];
        for (i, b) in bs.iter().enumerate() {
            let (pos, quat) = channel_value(buf, chn[i].as_ref(), b, delta_anim, frame)?;
            data[(f * nb + i) * 7..][..3].copy_from_slice(&pos);
            data[(f * nb + i) * 7 + 3..][..4].copy_from_slice(&quat);
        }
    }
    Ok(Sampled { frames, bones: nb, fps, flags, data })
}

fn channel_value(buf: &[u8], c: Option<&Channel>, b: &Bone, delta_anim: bool, frame: i32) -> R<([f32; 3], [f32; 4])> {
    let Some(c) = c else {
        return Ok(if delta_anim { ([0.0; 3], [0.0, 0.0, 0.0, 1.0]) } else { (b.pos, b.quat) });
    };
    let delta = c.flags & DELTA != 0 || delta_anim;
    let base = c.at + 4;
    let quat = if c.flags & RAWROT != 0 {
        quat48(buf, base)?
    } else if c.flags & RAWROT2 != 0 {
        quat64(buf, base)?
    } else if c.flags & ANIMROT != 0 {
        let mut ang = [0f32; 3];
        for (j, a) in ang.iter_mut().enumerate() {
            let off = i16_at(buf, base + 2 * j)?;
            let v = if off == 0 { 0.0 } else { rle(buf, at(base, off as i32)?, frame, b.rotscale[j])? };
            *a = if delta { v } else { v + b.rot[j] };
        }
        angle_quat(ang)
    } else if delta {
        [0.0, 0.0, 0.0, 1.0]
    } else {
        b.quat
    };
    // the SDK finds the position data after the raw rotation (raw positions) or after the rotation value pointers
    let pos = if c.flags & RAWPOS != 0 {
        let o = base + if c.flags & RAWROT != 0 { 6 } else { 0 } + if c.flags & RAWROT2 != 0 { 8 } else { 0 };
        [half(u16_at(buf, o)?), half(u16_at(buf, o + 2)?), half(u16_at(buf, o + 4)?)]
    } else if c.flags & ANIMPOS != 0 {
        let ptr = base + if c.flags & ANIMROT != 0 { 6 } else { 0 };
        let mut p = [0f32; 3];
        for (j, v) in p.iter_mut().enumerate() {
            let off = i16_at(buf, ptr + 2 * j)?;
            let x = if off == 0 { 0.0 } else { rle(buf, at(ptr, off as i32)?, frame, b.posscale[j])? };
            *v = if delta { x } else { x + b.pos[j] };
        }
        p
    } else if delta {
        [0.0; 3]
    } else {
        b.pos
    };
    Ok((pos, quat))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    fn put(d: &mut Vec<u8>, o: usize, bytes: &[u8]) {
        if d.len() < o + bytes.len() {
            d.resize(o + bytes.len(), 0);
        }
        d[o..o + bytes.len()].copy_from_slice(bytes);
    }

    /// A model with two bones (root, child), one 4-frame animation and one sequence "idle":
    /// the root has a raw rotation and a raw position, the child a run-length encoded Z rotation (angle = 0.1 * value).
    pub(crate) fn model() -> Vec<u8> {
        let mut d = vec![0u8; 0x200];
        put(&mut d, 0, b"IDST");
        put(&mut d, 4, &48i32.to_le_bytes());
        put(&mut d, 12, b"test.mdl\0");
        let (bones_at, anim_at, seq_at, data_at, strings) = (0x200usize, 0x400usize, 0x500usize, 0x600usize, 0x800usize);
        put(&mut d, 156, &2i32.to_le_bytes());
        put(&mut d, 160, &(bones_at as i32).to_le_bytes());
        put(&mut d, 180, &1i32.to_le_bytes());
        put(&mut d, 184, &(anim_at as i32).to_le_bytes());
        put(&mut d, 188, &1i32.to_le_bytes());
        put(&mut d, 192, &(seq_at as i32).to_le_bytes());
        put(&mut d, strings, b"root\0child\0idle\0");
        for (i, (name, parent, pos)) in [(strings, -1i32, [1.0f32, 2.0, 3.0]), (strings + 5, 0, [0.0, 0.0, 10.0])].into_iter().enumerate() {
            let o = bones_at + i * 216;
            put(&mut d, o, &((name - o) as i32).to_le_bytes());
            put(&mut d, o + 4, &parent.to_le_bytes());
            for (k, v) in pos.iter().enumerate() {
                put(&mut d, o + 32 + 4 * k, &v.to_le_bytes());
            }
            put(&mut d, o + 56, &1.0f32.to_le_bytes()); // quaternion w
            for k in 0..3 {
                put(&mut d, o + 84 + 4 * k, &0.1f32.to_le_bytes()); // rotscale
                put(&mut d, o + 72 + 4 * k, &1.0f32.to_le_bytes()); // posscale
            }
        }
        // animation description: 4 frames at 30 fps, data in this file (block 0) at data_at
        put(&mut d, anim_at + 8, &30f32.to_le_bytes());
        put(&mut d, anim_at + 16, &4i32.to_le_bytes());
        put(&mut d, anim_at + 56, &((data_at - anim_at) as i32).to_le_bytes());
        // record 0: bone 0, raw rotation and raw position, the next record 16 bytes further
        put(&mut d, data_at, &[0, RAWROT | RAWPOS]);
        put(&mut d, data_at + 2, &16i16.to_le_bytes());
        put(&mut d, data_at + 4, &[0, 0x80, 0, 0x80, 0, 0x40]); // x = y = z = 0 (w comes out as 1)
        put(&mut d, data_at + 10, &[0x00, 0x3C, 0x00, 0x40, 0x00, 0x42]); // halves 1.0, 2.0, 3.0
        // record 1: bone 1, run-length rotation, last record
        let r1 = data_at + 16;
        put(&mut d, r1, &[1, ANIMROT]);
        put(&mut d, r1 + 4, &0i16.to_le_bytes());
        put(&mut d, r1 + 6, &0i16.to_le_bytes());
        put(&mut d, r1 + 8, &6i16.to_le_bytes()); // the Z stream starts 6 bytes after the pointers' start + 4
        let z = r1 + 4 + 6;
        put(&mut d, z, &[4, 4]);
        for (k, v) in [0i16, 5, 10, 15].iter().enumerate() {
            put(&mut d, z + 2 + 2 * k, &v.to_le_bytes());
        }
        // sequence "idle": one blend, animation 0, looping
        put(&mut d, seq_at + 4, &((strings + 11 - seq_at) as i32).to_le_bytes());
        put(&mut d, seq_at + 12, &1i32.to_le_bytes());
        put(&mut d, seq_at + 56, &1i32.to_le_bytes());
        put(&mut d, seq_at + 60, &0x40i32.to_le_bytes());
        put(&mut d, seq_at + 68, &1i32.to_le_bytes());
        put(&mut d, seq_at + 72, &1i32.to_le_bytes());
        put(&mut d, seq_at + 0x40, &0i16.to_le_bytes());
        d
    }

    #[test]
    fn reads_bones_sequences_and_frames() {
        let d = model();
        let info = parse(&d).unwrap();
        assert_eq!((info.bones.len(), info.bones[1].parent, info.bones[1].name.as_str()), (2, 0, "child"));
        assert_eq!((info.seqs[0].name.as_str(), info.seqs[0].frames, info.seqs[0].anim, info.seqs[0].flags & 1), ("idle", 4, 0, 1));
        let s = sample(&d, None, 0).unwrap();
        assert_eq!((s.frames, s.bones, s.fps), (4, 2, 30.0));
        let at = |f: usize, b: usize| s.data[(f * 2 + b) * 7..][..7].to_vec();
        assert_eq!(&at(2, 0)[..3], &[1.0, 2.0, 3.0]);
        assert!((at(0, 0)[6] - 1.0).abs() < 1e-3);
        // child: Z angle = 0.1 * value on top of the (zero) rest rotation, position at rest
        assert_eq!(&at(3, 1)[..3], &[0.0, 0.0, 10.0]);
        let ang = 1.5f32;
        assert!((at(3, 1)[5] - (ang * 0.5).sin()).abs() < 1e-4 && (at(3, 1)[6] - (ang * 0.5).cos()).abs() < 1e-4);
        assert!(at(0, 1)[5].abs() < 1e-6);
    }

    #[test]
    fn corrupt_files_are_errors() {
        let d = model();
        assert!(parse(&d[..100]).is_err());
        assert!(sample(&d, None, 5).is_err());
        assert!(sample(&d[..0x405], None, 0).is_err());
        let mut bad = d.clone();
        bad[0] = b'X';
        assert!(parse(&bad).is_err());
    }

    #[test]
    fn halves_decode() {
        assert_eq!(half(0x3C00), 1.0);
        assert_eq!(half(0xC000), -2.0);
        assert_eq!(half(0), 0.0);
        assert!((half(0x3555) - 0.333).abs() < 1e-3);
    }
}
