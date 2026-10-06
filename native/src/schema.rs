//! Type descriptions of Glacier entities: CPPT (class property schema), CBLU (class blueprint) and ENUM.
//!
//! * CPPT `[modules:/<class>.class].entitytype`: BIN1 whose data section starts with a `TArray` at +8 of 24-byte
//!   records `{u32 CRC32(property name), u32 pad, u64 type index (BIN1 type table), i64 pointer to the default value
//!   (-1: none)}`. The names are not stored, only their CRC32 and the type: that is enough to say which class has
//!   which property and of which type (an entity template stores the same ids, see `entity.rs`).
//! * CBLU `[modules:/<class>.class].entityblueprint`: nothing but the class name, as the only entry of the type table.
//! * ENUM: `{ZString name, TArray<ZString> legacy names @+0x10, TArray<ZString> names @+0x28, TArray<i32> values
//!   @+0x40}`; `names[i]` is the member whose value is `values[i]`, `legacy` keeps the former spellings (sorted).

use crate::entity::{parse_bin1, zstring};

#[derive(Clone, Debug, PartialEq)]
pub struct PropDef {
    pub crc: u32,
    pub ty: String,
    /// the class stores a default value for the property
    pub default: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EnumDef {
    pub name: String,
    pub members: Vec<(String, i32)>,
    pub legacy: Vec<String>,
}

fn u32_at(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o.checked_add(4)?).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}
fn i64_at(d: &[u8], o: usize) -> Option<i64> {
    d.get(o..o.checked_add(8)?).map(|b| i64::from_le_bytes(b.try_into().unwrap()))
}

/// `TArray` at `o` -> (begin, element count); `elem` is the element size. Anything out of range is an error: a
/// schema read half-way would hand wrong types to the callers.
fn array(d: &[u8], o: usize, elem: usize) -> Result<(usize, usize), String> {
    let (b, e) = (i64_at(d, o).ok_or("array out of range")?, i64_at(d, o + 8).ok_or("array out of range")?);
    if b < 0 || e < b {
        return Ok((0, 0)); // empty arrays are stored as -1/-1
    }
    let (b, e) = (b as usize, e as usize);
    if e > d.len() || (e - b) % elem != 0 {
        return Err("array exceeds the data section".into());
    }
    Ok((b, (e - b) / elem))
}

/// Properties of an entity class (CPPT).
pub fn class_schema(d: &[u8]) -> Result<Vec<PropDef>, String> {
    let (data, types) = parse_bin1(d)?;
    let (b, n) = array(data, 8, 24)?;
    let mut out = Vec::with_capacity(n);
    for k in 0..n {
        let o = b + 24 * k;
        let crc = u32_at(data, o).ok_or("truncated property")?;
        let tidx = i64_at(data, o + 8).ok_or("truncated property")?;
        let ptr = i64_at(data, o + 16).ok_or("truncated property")?;
        let ty = usize::try_from(tidx).ok().and_then(|i| types.get(i)).ok_or("property type out of range")?;
        out.push(PropDef { crc, ty: ty.clone(), default: ptr >= 0 });
    }
    Ok(out)
}

/// Class name of a blueprint (CBLU).
pub fn blueprint_class(d: &[u8]) -> Result<String, String> {
    let (_data, types) = parse_bin1(d)?;
    types.into_iter().next().ok_or_else(|| "blueprint without class name".to_string())
}

pub fn enum_def(d: &[u8]) -> Result<EnumDef, String> {
    let (data, _types) = parse_bin1(d)?;
    let name = zstring(data, 0);
    if name.is_empty() {
        return Err("enum without name".into());
    }
    let (lb, ln) = array(data, 0x10, 16)?;
    let (nb, nn) = array(data, 0x28, 16)?;
    let (vb, vn) = array(data, 0x40, 4)?;
    if nn != vn {
        return Err("enum names and values differ in count".into());
    }
    let names: Vec<String> = (0..nn).map(|k| zstring(data, nb + 16 * k)).collect();
    let mut members = Vec::with_capacity(nn);
    for (k, n) in names.iter().enumerate() {
        members.push((n.clone(), u32_at(data, vb + 4 * k).ok_or("truncated enum value")? as i32));
    }
    let legacy = (0..ln).map(|k| zstring(data, lb + 16 * k)).filter(|s| !s.is_empty() && !names.contains(s)).collect();
    Ok(EnumDef { name, members, legacy })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A BIN1 with `data` and a type table of `types` (the layout of the game files).
    pub(crate) fn bin1(data: &[u8], types: &[&str]) -> Vec<u8> {
        let mut out = b"BIN1".to_vec();
        out.extend([0, 8, 1, 0]);
        out.extend((data.len() as u32).to_be_bytes());
        out.extend([0; 4]);
        out.extend(data);
        let mut seg = Vec::new();
        seg.extend((types.len() as u32).to_le_bytes());
        seg.extend(vec![0u8; 4 * types.len()]);
        seg.extend((types.len() as u32).to_le_bytes());
        for (i, t) in types.iter().enumerate() {
            seg.extend((i as u32).to_le_bytes());
            seg.extend((-1i32).to_le_bytes());
            seg.extend((t.len() as u32 + 1).to_le_bytes());
            seg.extend(t.as_bytes());
            seg.push(0);
            while seg.len() % 4 != 0 {
                seg.push(0);
            }
        }
        out.extend(0x3989BF9Fu32.to_le_bytes());
        out.extend((seg.len() as u32).to_le_bytes());
        out.extend(seg);
        out
    }

    fn zs(buf: &mut Vec<u8>, at: usize, s: &str, ptr: usize) {
        buf[at..at + 4].copy_from_slice(&(s.len() as u32 | 0x4000_0000).to_le_bytes());
        buf[at + 8..at + 16].copy_from_slice(&(ptr as u64).to_le_bytes());
    }

    #[test]
    fn reads_the_properties_of_a_class() {
        let mut data = vec![0u8; 8 + 24 + 8 + 4 * 24];
        let begin = 40usize;
        data[8..16].copy_from_slice(&(begin as u64).to_le_bytes());
        data[16..24].copy_from_slice(&((begin + 48) as u64).to_le_bytes());
        data[24..32].copy_from_slice(&((begin + 48) as u64).to_le_bytes());
        for (k, (crc, tidx, ptr)) in [(0xDC933FABu32, 1i64, -1i64), (0x1234, 0, 100)].iter().enumerate() {
            let o = begin + 24 * k;
            data[o..o + 4].copy_from_slice(&crc.to_le_bytes());
            data[o + 8..o + 16].copy_from_slice(&tidx.to_le_bytes());
            data[o + 16..o + 24].copy_from_slice(&ptr.to_le_bytes());
        }
        let f = bin1(&data, &["float32", "TArray<ZEntityReference>"]);
        let s = class_schema(&f).unwrap();
        assert_eq!(s, vec![
            PropDef { crc: 0xDC933FAB, ty: "TArray<ZEntityReference>".into(), default: false },
            PropDef { crc: 0x1234, ty: "float32".into(), default: true },
        ]);
        assert!(class_schema(&bin1(&data, &["float32"])).is_err(), "a type index out of the table is an error");
    }

    #[test]
    fn reads_the_class_name_of_a_blueprint() {
        assert_eq!(blueprint_class(&bin1(&[0; 16], &["zbodypartentity"])).unwrap(), "zbodypartentity");
        assert!(blueprint_class(&bin1(&[0; 16], &[])).is_err());
        assert!(blueprint_class(b"nope").is_err());
    }

    #[test]
    fn reads_an_enum_with_its_former_names() {
        // layout: name, 3 arrays, strings
        let mut data = vec![0u8; 0x58 + 16 * 2 + 16 * 2 + 8 + 64];
        let names_at = 0x58;
        let legacy_at = names_at + 32;
        let values_at = legacy_at + 32;
        let strs = values_at + 8;
        let mut put = |data: &mut Vec<u8>, at: usize, s: &str, p: usize| {
            data[p..p + s.len()].copy_from_slice(s.as_bytes());
            zs(data, at, s, p);
        };
        put(&mut data, 0, "grade", strs);
        put(&mut data, names_at, "Low", strs + 8);
        put(&mut data, names_at + 16, "High", strs + 16);
        put(&mut data, legacy_at, "High", strs + 16);
        put(&mut data, legacy_at + 16, "Old", strs + 24);
        for (o, b, n, sz) in [(0x10usize, legacy_at, 2usize, 16usize), (0x28, names_at, 2, 16), (0x40, values_at, 2, 4)] {
            data[o..o + 8].copy_from_slice(&(b as u64).to_le_bytes());
            data[o + 8..o + 16].copy_from_slice(&((b + n * sz) as u64).to_le_bytes());
        }
        data[values_at..values_at + 4].copy_from_slice(&0i32.to_le_bytes());
        data[values_at + 4..values_at + 8].copy_from_slice(&5i32.to_le_bytes());
        let e = enum_def(&bin1(&data, &[])).unwrap();
        assert_eq!(e.name, "grade");
        assert_eq!(e.members, vec![("Low".to_string(), 0), ("High".to_string(), 5)]);
        assert_eq!(e.legacy, vec!["Old".to_string()]);
    }

    #[test]
    fn garbage_is_an_error_or_something_but_never_a_panic() {
        for d in [&b""[..], b"BIN1", &[0xFFu8; 80]] {
            let _ = class_schema(d);
            let _ = blueprint_class(d);
            let _ = enum_def(d);
        }
    }
}
