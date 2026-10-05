//! Glacier BIN1 entity resources (TEMP / TBLU) and `.meta` reference lists. Port of omni/sources/glacier/entity.py
//! (same layouts, same tolerance: out-of-range reads give empty values, never a panic).

use crate::par::*;
use std::collections::HashSet;

const TYPE_TABLE: u32 = 0x3989BF9F;

fn u32_at(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o + 4).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}
fn i32_at(d: &[u8], o: usize) -> Option<i32> {
    u32_at(d, o).map(|v| v as i32)
}
fn u64_at(d: &[u8], o: usize) -> Option<u64> {
    d.get(o..o + 8).map(|b| u64::from_le_bytes(b.try_into().unwrap()))
}
fn i64_at(d: &[u8], o: usize) -> Option<i64> {
    u64_at(d, o).map(|v| v as i64)
}
fn f32_at(d: &[u8], o: usize) -> Option<f32> {
    u32_at(d, o).map(f32::from_bits)
}
fn f32s(d: &[u8], o: usize, n: usize) -> Option<Vec<f32>> {
    (0..n).map(|i| f32_at(d, o + 4 * i)).collect()
}

/// (BIN1 data section, type names)
pub fn parse_bin1(d: &[u8]) -> Result<(&[u8], Vec<String>), String> {
    if d.len() < 16 || &d[..4] != b"BIN1" {
        return Err("not a BIN1 resource".into());
    }
    let size = u32::from_be_bytes(d[8..12].try_into().unwrap()) as usize;
    let end = (16 + size).min(d.len());
    let data = &d[16..end];
    let mut types = Vec::new();
    let mut o = 16 + size;
    while o + 8 <= d.len() {
        let sid = u32_at(d, o).unwrap();
        let ssz = u32_at(d, o + 4).unwrap() as usize;
        let seg = &d[(o + 8).min(d.len())..(o + 8 + ssz).min(d.len())];
        if sid == TYPE_TABLE {
            let n = u32_at(seg, 0).ok_or("bad type table")? as usize;
            let mut q = 4 + 4 * n;
            let cnt = u32_at(seg, q).ok_or("bad type table")? as usize;
            q += 4;
            for _ in 0..cnt {
                let ln = u32_at(seg, q + 8).ok_or("bad type table")? as usize;
                q += 12;
                let s = seg.get(q..q + ln.saturating_sub(1)).ok_or("bad type table")?;
                types.push(s.iter().map(|&c| c as char).collect());
                q = (q + ln + 3) & !3;
            }
        }
        o += 8 + ssz;
    }
    Ok((data, types))
}

pub fn zstring(d: &[u8], o: usize) -> String {
    let (Some(ln), Some(ptr)) = (u32_at(d, o), i64_at(d, o + 8)) else { return String::new() };
    let ln = (ln & 0x3FFF_FFFF) as usize;
    if ptr < 0 || ln == 0 {
        return String::new();
    }
    let p = ptr as usize;
    String::from_utf8_lossy(d.get(p..(p + ln).min(d.len())).unwrap_or(&[])).into_owned()
}

fn array(d: &[u8], o: usize) -> (usize, usize) {
    match (i64_at(d, o), i64_at(d, o + 8)) {
        (Some(b), Some(e)) if 0 <= b && b <= e => (b as usize, e as usize),
        _ => (0, 0),
    }
}

#[derive(Clone, Debug)]
pub struct ERef {
    pub index: i64,
    pub entity_id: i64,
    pub exposed: String,
    pub external_scene: i32,
}

fn eref(d: &[u8], o: usize) -> Result<ERef, String> {
    let (idx, eid, ext) = (i64_at(d, o), i64_at(d, o + 8), i32_at(d, o + 32));
    let (Some(mut idx), Some(eid), Some(ext)) = (idx, eid, ext) else { return Err("entity ref out of range".into()) };
    if idx >= 0x8000_0000 {
        idx = -1;
    }
    Ok(ERef { index: idx, entity_id: eid, exposed: zstring(d, o + 16), external_scene: ext })
}

#[derive(Clone, Debug)]
pub enum Value {
    None,
    Floats(Vec<f32>),
    F32(f32),
    F64(f64),
    Bool(bool),
    Int(i64),
    UInt(u64),
    Str(String),
    Refs(Vec<ERef>),
}

fn value(d: &[u8], types: &[String], tidx: i64, ptr: i64, refs: &[u64]) -> (String, Value) {
    let t = if tidx >= 0 && (tidx as usize) < types.len() { types[tidx as usize].clone() } else { format!("?{tidx}") };
    if ptr < 0 {
        return (t, Value::None);
    }
    let p = ptr as usize;
    let v = (|| -> Option<Value> {
        Some(match t.as_str() {
            "SColorRGB" | "SVector3" => Value::Floats(f32s(d, p, 3)?),
            "SColorRGBA" | "SVector4" => Value::Floats(f32s(d, p, 4)?),
            "SVector2" => Value::Floats(f32s(d, p, 2)?),
            "SMatrix43" => Value::Floats(f32s(d, p, 12)?),
            "float32" => Value::F32(f32_at(d, p)?),
            "float64" => Value::F64(f64::from_bits(u64_at(d, p)?)),
            "bool" => Value::Bool(*d.get(p)? != 0),
            "int8" => Value::Int(*d.get(p)? as i8 as i64),
            "uint8" => Value::Int(*d.get(p)? as i64),
            "int16" => Value::Int(i16::from_le_bytes(d.get(p..p + 2)?.try_into().ok()?) as i64),
            "uint16" => Value::Int(u16::from_le_bytes(d.get(p..p + 2)?.try_into().ok()?) as i64),
            "int32" => Value::Int(i32_at(d, p)? as i64),
            "uint32" => Value::Int(u32_at(d, p)? as i64),
            "int64" => Value::Int(i64_at(d, p)?),
            "uint64" => Value::UInt(u64_at(d, p)?),
            "ZString" => Value::Str(zstring(d, p)),
            "ZRuntimeResourceID" => {
                let (hi, lo) = (u32_at(d, p)?, u32_at(d, p + 4)?);
                if hi == 0xFFFF_FFFF && lo == 0xFFFF_FFFF {
                    Value::None
                } else if (lo as usize) < refs.len() {
                    Value::UInt(refs[lo as usize])
                } else {
                    Value::UInt(((hi as u64) << 32) | lo as u64)
                }
            }
            "TArray<SEntityTemplateReference>" => {
                let (b, e) = array(d, p);
                let mut v = Vec::new();
                let mut x = b;
                while x < e {
                    v.push(eref(d, x).ok()?);
                    x += 40;
                }
                Value::Refs(v)
            }
            s if s.starts_with("TArray<") => Value::None,
            s if s.len() > 1 && s.as_bytes()[0] == b'E' && s.as_bytes()[1].is_ascii_uppercase() => Value::Int(i32_at(d, p)? as i64),
            _ => Value::None,
        })
    })();
    (t, v.unwrap_or(Value::None))
}

pub type RawProp = (u32, String, Value);

fn props(d: &[u8], types: &[String], o: usize, refs: &[u64]) -> Result<Vec<RawProp>, String> {
    let (b, e) = array(d, o);
    let mut out = Vec::new();
    let mut x = b;
    while x < e {
        let (Some(pid), Some(tidx), Some(ptr)) = (u32_at(d, x), i64_at(d, x + 8), i64_at(d, x + 16)) else {
            return Err("property out of range".into());
        };
        let (t, v) = value(d, types, tidx, ptr, refs);
        out.push((pid, t, v));
        x += 24;
    }
    Ok(out)
}

pub struct RawSub {
    pub parent: ERef,
    pub type_ref: u64,
    pub props: Vec<RawProp>,
    pub post: Vec<RawProp>,
}

pub struct RawTemplate {
    pub blueprint_index: i32,
    pub subs: Vec<RawSub>,
    pub overrides: Vec<(ERef, RawProp)>,
}

pub fn parse_temp(bin: &[u8], refs: &[u64]) -> Result<RawTemplate, String> {
    let (data, types) = parse_bin1(bin)?;
    let bp = i32_at(data, 4).ok_or("short TEMP")?;
    let mut subs = Vec::new();
    let (b, e) = array(data, 0x20);
    let mut o = b;
    while o < e {
        let t = i32_at(data, o + 0x28).ok_or("sub out of range")?;
        subs.push(RawSub {
            parent: eref(data, o)?,
            type_ref: if t >= 0 && (t as usize) < refs.len() { refs[t as usize] } else { 0 },
            props: props(data, &types, o + 0x30, refs)?,
            post: props(data, &types, o + 0x48, refs)?,
        });
        o += 0x78;
    }
    let mut overrides = Vec::new();
    let (b, e) = array(data, 0x38);
    let mut o = b;
    while o < e {
        let owner = eref(data, o)?;
        let (Some(pid), Some(tidx), Some(ptr)) = (u32_at(data, o + 40), i64_at(data, o + 48), i64_at(data, o + 56)) else {
            return Err("override out of range".into());
        };
        let (t, v) = value(data, &types, tidx, ptr, refs);
        overrides.push((owner, (pid, t, v)));
        o += 64;
    }
    Ok(RawTemplate { blueprint_index: bp, subs, overrides })
}

pub struct RawBlueprintSub {
    pub entity_id: u64,
    pub name: String,
    pub aliases: Vec<(String, i64, String)>,
}

pub fn parse_tblu(bin: &[u8]) -> Result<(i32, Vec<RawBlueprintSub>), String> {
    let (data, _types) = parse_bin1(bin)?;
    let root = i32_at(data, 4).ok_or("short TBLU")?;
    let (b, e) = array(data, 0x18);
    let mut out = Vec::new();
    let mut o = b;
    while o < e {
        let entity_id = u64_at(data, o + 0x30).ok_or("blueprint sub out of range")?;
        let name = zstring(data, o + 0x58);
        let (ab, ae) = array(data, o + 0x68);
        let mut aliases = Vec::new();
        let mut x = ab;
        while x < ae {
            aliases.push((zstring(data, x), i64_at(data, x + 16).ok_or("alias out of range")?, zstring(data, x + 24)));
            x += 40;
        }
        out.push(RawBlueprintSub { entity_id, name, aliases });
        o += 0xB0;
    }
    Ok((root, out))
}

/// Reference hashes listed in a `.meta` file (None: unreadable / absent).
pub fn meta_refs(data: &[u8]) -> Option<Vec<u64>> {
    let refs_size = u32_at(data, 24)?;
    let mut out = Vec::new();
    if refs_size > 0 {
        let cnt = u16::from_le_bytes(data.get(40..42)?.try_into().ok()?) as usize;
        let base = 44 + cnt;
        for i in 0..cnt {
            out.push(u64_at(data, base + i * 8)?);
        }
    }
    Some(out)
}

/// Rows (prim, template) for templates referencing 1..=max_prims of `prims` (larger ones are level scenes).
/// `temps`: (template hash, path of the TEMP resource); the `.meta` files are read in parallel.
pub fn template_index(temps: &[(u64, String)], prims: &HashSet<u64>, max_prims: usize) -> Vec<(u64, u64)> {
    temps
        .par_iter()
        .flat_map_iter(|(h, p)| {
            let refs = std::fs::read(format!("{p}.meta")).ok().and_then(|d| meta_refs(&d)).unwrap_or_default();
            let pr: Vec<u64> = refs.into_iter().filter(|r| prims.contains(r)).collect();
            let rows: Vec<(u64, u64)> = if !pr.is_empty() && pr.len() <= max_prims { pr.into_iter().map(|r| (r, *h)).collect() } else { vec![] };
            rows.into_iter()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn meta_roundtrip() {
        let mut d = vec![0u8; 44];
        d[24] = 1;
        d[40] = 2;
        d.extend([0u8, 0]);
        d.extend(7u64.to_le_bytes());
        d.extend(9u64.to_le_bytes());
        assert_eq!(meta_refs(&d), Some(vec![7, 9]));
        assert_eq!(meta_refs(&d[..30]), None);
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse_temp(b"nope", &[]).is_err());
        assert!(parse_bin1(&[0u8; 8]).is_err());
    }
}
