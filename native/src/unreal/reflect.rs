//! Property layouts of an Unreal game read statically from its shipping executable ("mappings").
//!
//! Cooked UE5 packages store properties unversioned: values follow each other without names or types, in the
//! order of the class's property list. That list is compiled into the executable as UHT-generated tables:
//!
//!   FClassRegisterCompiledInInfo { Z_Construct_UClass_X, X::StaticClass, TEXT("UX"), info, version }  (.data)
//!   FStructRegisterCompiledInInfo { Z_Construct_UScriptStruct_FX, struct ops, TEXT("X"), info, version }
//!   FEnumRegisterCompiledInInfo { Z_Construct_UEnum_X, TEXT("X"), info, version }
//!   Z_Construct_* = `if (!singleton) ConstructUClass(singleton, Params)`: the `lea rdx, [rip+Params]`
//!   FClassParams { NoRegister, config name, cpp info, dependency singletons (super first), functions,
//!                  PropertyArray, interfaces, i32 counts (deps, functions, properties, interfaces), flags }
//!   FStructParams { outer, super, struct ops, name, size, align, PropertyArray, i32 count, flags }
//!   FEnumParams { outer, display name fn, enumerators {name, i64}, name, cpp type, i32 count, ... }
//!   property params { name, rep notify, u64 flags, u8 EPropertyGenFlags, i32 object flags, array dim, ... ,
//!                     type-specific pointer (struct / enum / class function) }
//! Container properties are preceded by their inner properties (array, set, enum underlying: 1; map: key then
//! value, i.e. map at i, key at i-1, value at i-2). The type-specific pointer offset moved between engine
//! releases, so it is calibrated on the struct properties of the executable itself.
//!
//! Unversioned property indices: a struct's own properties first (array dims expanded), then its super's.

use std::collections::HashMap;

use super::reader::{err, Result};

#[derive(Clone, Debug, PartialEq)]
pub enum Ty {
    Byte(Option<String>),
    Bool,
    Int8,
    Int16,
    Int,
    Int64,
    UInt16,
    UInt32,
    UInt64,
    Float,
    Double,
    Name,
    Str,
    Text,
    Object,
    WeakObject,
    LazyObject,
    SoftObject,
    Interface,
    Delegate,
    MulticastInline,
    MulticastSparse,
    FieldPath,
    Struct(String),
    Enum(String, Box<Ty>),
    Array(Box<Ty>),
    Set(Box<Ty>),
    Map(Box<Ty>, Box<Ty>),
    Optional(Box<Ty>),
    Unknown(u8),
}

#[derive(Clone, Debug)]
pub struct Prop {
    pub name: String,
    pub array_dim: u16,
    pub ty: Ty,
}

#[derive(Clone, Debug, Default)]
pub struct StructDef {
    pub name: String,
    pub super_name: Option<String>,
    pub props: Vec<Prop>,
}

impl StructDef {
    pub fn own_count(&self) -> usize {
        self.props.iter().map(|p| p.array_dim.max(1) as usize).sum()
    }
}

#[derive(Default)]
pub struct Schema {
    pub structs: HashMap<String, StructDef>,
    pub enums: HashMap<String, Vec<(String, i64)>>,
}

impl Schema {
    /// Property at unversioned index `i` of `name` (own properties, then the super chain).
    pub fn prop_at(&self, name: &str, mut i: usize) -> Option<&Prop> {
        let mut cur = self.structs.get(name)?;
        for _ in 0..64 {
            let own = cur.own_count();
            if i < own {
                let mut k = 0;
                for p in &cur.props {
                    let n = p.array_dim.max(1) as usize;
                    if i < k + n {
                        return Some(p);
                    }
                    k += n;
                }
                return None;
            }
            i -= own;
            cur = self.structs.get(cur.super_name.as_deref()?)?;
        }
        None
    }

    pub fn has(&self, name: &str) -> bool {
        self.structs.contains_key(name)
    }

    pub fn enum_name(&self, e: &str, v: i64) -> Option<&str> {
        self.enums.get(e)?.iter().find(|x| x.1 == v).map(|x| x.0.as_str())
    }

    // ---- text cache ---------------------------------------------------------------------------------------
    pub fn to_text(&self) -> String {
        let mut out = String::from("omni-mappings 1\n");
        let mut names: Vec<_> = self.structs.keys().collect();
        names.sort();
        for n in names {
            let s = &self.structs[n];
            out.push_str(&format!("S {} {}\n", s.name, s.super_name.as_deref().unwrap_or("-")));
            for p in &s.props {
                out.push_str(&format!("P {} {} {}\n", p.array_dim, p.name, ty_text(&p.ty)));
            }
        }
        let mut en: Vec<_> = self.enums.keys().collect();
        en.sort();
        for n in en {
            out.push_str(&format!("E {}", n));
            for (k, v) in &self.enums[n] {
                out.push_str(&format!(" {}={}", k, v));
            }
            out.push('\n');
        }
        out
    }

    pub fn from_text(text: &str) -> Result<Schema> {
        let mut s = Schema::default();
        let mut cur: Option<StructDef> = None;
        let mut lines = text.lines();
        if lines.next() != Some("omni-mappings 1") {
            return err("not an omni mappings file");
        }
        for line in lines {
            let mut it = line.splitn(4, ' ');
            match it.next() {
                Some("S") => {
                    if let Some(c) = cur.take() {
                        s.structs.insert(c.name.clone(), c);
                    }
                    let name = it.next().unwrap_or("").to_string();
                    let sup = it.next().unwrap_or("-");
                    cur = Some(StructDef { name, super_name: if sup == "-" { None } else { Some(sup.to_string()) }, props: vec![] });
                }
                Some("P") => {
                    let dim: u16 = it.next().and_then(|x| x.parse().ok()).unwrap_or(1);
                    let name = it.next().unwrap_or("").to_string();
                    let ty = parse_ty(it.next().unwrap_or("?"));
                    if let Some(c) = cur.as_mut() {
                        c.props.push(Prop { name, array_dim: dim, ty });
                    }
                }
                Some("E") => {
                    let rest: Vec<&str> = line[2..].split(' ').collect();
                    let name = rest[0].to_string();
                    let vals = rest[1..]
                        .iter()
                        .filter_map(|kv| kv.rsplit_once('=').and_then(|(k, v)| v.parse().ok().map(|v| (k.to_string(), v))))
                        .collect();
                    s.enums.insert(name, vals);
                }
                _ => {}
            }
        }
        if let Some(c) = cur.take() {
            s.structs.insert(c.name.clone(), c);
        }
        Ok(s)
    }
}

pub fn ty_text(t: &Ty) -> String {
    match t {
        Ty::Byte(Some(e)) => format!("Byte<{e}>"),
        Ty::Byte(None) => "Byte".into(),
        Ty::Struct(n) => format!("Struct<{n}>"),
        Ty::Enum(n, u) => format!("Enum<{n},{}>", ty_text(u)),
        Ty::Array(i) => format!("Array<{}>", ty_text(i)),
        Ty::Set(i) => format!("Set<{}>", ty_text(i)),
        Ty::Optional(i) => format!("Optional<{}>", ty_text(i)),
        Ty::Map(k, v) => format!("Map<{},{}>", ty_text(k), ty_text(v)),
        Ty::Unknown(x) => format!("Unknown{x}"),
        other => format!("{other:?}"),
    }
}

fn split_args(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let (mut depth, mut start) = (0, 0);
    for (i, c) in s.char_indices() {
        match c {
            '<' => depth += 1,
            '>' => depth -= 1,
            ',' if depth == 0 => {
                out.push(&s[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    out.push(&s[start..]);
    out
}

pub fn parse_ty(s: &str) -> Ty {
    if let Some(open) = s.find('<') {
        let head = &s[..open];
        let inner = &s[open + 1..s.len().saturating_sub(1)];
        let a = split_args(inner);
        return match head {
            "Byte" => Ty::Byte(Some(inner.to_string())),
            "Struct" => Ty::Struct(inner.to_string()),
            "Enum" => Ty::Enum(a[0].to_string(), Box::new(parse_ty(a.get(1).copied().unwrap_or("Byte")))),
            "Array" => Ty::Array(Box::new(parse_ty(inner))),
            "Set" => Ty::Set(Box::new(parse_ty(inner))),
            "Optional" => Ty::Optional(Box::new(parse_ty(inner))),
            "Map" => Ty::Map(Box::new(parse_ty(a[0])), Box::new(parse_ty(a.get(1).copied().unwrap_or("?")))),
            _ => Ty::Unknown(0),
        };
    }
    match s {
        "Byte" => Ty::Byte(None),
        "Bool" => Ty::Bool,
        "Int8" => Ty::Int8,
        "Int16" => Ty::Int16,
        "Int" => Ty::Int,
        "Int64" => Ty::Int64,
        "UInt16" => Ty::UInt16,
        "UInt32" => Ty::UInt32,
        "UInt64" => Ty::UInt64,
        "Float" => Ty::Float,
        "Double" => Ty::Double,
        "Name" => Ty::Name,
        "Str" => Ty::Str,
        "Text" => Ty::Text,
        "Object" => Ty::Object,
        "WeakObject" => Ty::WeakObject,
        "LazyObject" => Ty::LazyObject,
        "SoftObject" => Ty::SoftObject,
        "Interface" => Ty::Interface,
        "Delegate" => Ty::Delegate,
        "MulticastInline" => Ty::MulticastInline,
        "MulticastSparse" => Ty::MulticastSparse,
        "FieldPath" => Ty::FieldPath,
        x if x.starts_with("Unknown") => Ty::Unknown(x[7..].parse().unwrap_or(0)),
        _ => Ty::Unknown(0),
    }
}

// ------------------------------------------------------------------------------------------------------ PE
struct Section {
    va: u64,
    size: u64,
    raw: usize,
    raw_size: usize,
    exec: bool,
    write: bool,
}

pub struct Image<'a> {
    data: &'a [u8],
    _base: u64,
    secs: Vec<Section>,
}

impl<'a> Image<'a> {
    pub fn parse(data: &'a [u8]) -> Result<Image<'a>> {
        let rd32 = |o: usize| -> Result<u32> { data.get(o..o + 4).map(|b| u32::from_le_bytes(b.try_into().unwrap())).ok_or(super::reader::Error("PE truncated".into())) };
        let rd16 = |o: usize| -> Result<u16> { data.get(o..o + 2).map(|b| u16::from_le_bytes(b.try_into().unwrap())).ok_or(super::reader::Error("PE truncated".into())) };
        if data.get(0..2) != Some(b"MZ") {
            return err("not a Windows executable");
        }
        let pe = rd32(0x3C)? as usize;
        if data.get(pe..pe + 4) != Some(b"PE\0\0") {
            return err("not a PE executable");
        }
        let nsec = rd16(pe + 6)? as usize;
        let opt_size = rd16(pe + 20)? as usize;
        let opt = pe + 24;
        if rd16(opt)? != 0x20b {
            return err("not a 64-bit executable");
        }
        let base = data.get(opt + 24..opt + 32).map(|b| u64::from_le_bytes(b.try_into().unwrap())).ok_or(super::reader::Error("PE truncated".into()))?;
        let mut secs = Vec::new();
        for i in 0..nsec.min(96) {
            let s = opt + opt_size + i * 40;
            let vsize = rd32(s + 8)? as u64;
            let va = rd32(s + 12)? as u64;
            let raw_size = rd32(s + 16)? as usize;
            let raw = rd32(s + 20)? as usize;
            let ch = rd32(s + 36)?;
            secs.push(Section { va: base + va, size: vsize.max(raw_size as u64), raw, raw_size, exec: ch & 0x2000_0000 != 0, write: ch & 0x8000_0000 != 0 });
        }
        Ok(Image { data, _base: base, secs })
    }

    fn sec(&self, va: u64) -> Option<&Section> {
        self.secs.iter().find(|s| va >= s.va && va < s.va + s.size)
    }
    fn off(&self, va: u64, n: usize) -> Option<usize> {
        let s = self.sec(va)?;
        let rel = (va - s.va) as usize;
        if rel + n > s.raw_size {
            return None;
        }
        Some(s.raw + rel)
    }
    pub fn bytes(&self, va: u64, n: usize) -> Option<&'a [u8]> {
        let o = self.off(va, n)?;
        self.data.get(o..o + n)
    }
    pub fn q(&self, va: u64) -> u64 {
        self.bytes(va, 8).map(|b| u64::from_le_bytes(b.try_into().unwrap())).unwrap_or(0)
    }
    pub fn d(&self, va: u64) -> u32 {
        self.bytes(va, 4).map(|b| u32::from_le_bytes(b.try_into().unwrap())).unwrap_or(0)
    }
    pub fn is_code(&self, va: u64) -> bool {
        self.sec(va).map_or(false, |s| s.exec)
    }
    pub fn is_rdata(&self, va: u64) -> bool {
        self.sec(va).map_or(false, |s| !s.exec && !s.write)
    }
    pub fn is_data(&self, va: u64) -> bool {
        self.sec(va).map_or(false, |s| s.write && !s.exec)
    }
    pub fn is_ptr(&self, va: u64) -> bool {
        self.sec(va).is_some()
    }
    /// ASCII identifier at `va` (letters, digits, `_`), up to 255 chars.
    pub fn cstr(&self, va: u64) -> Option<String> {
        let o = self.off(va, 1)?;
        let tail = &self.data[o..self.data.len().min(o + 512)];
        let end = tail.iter().position(|&c| c == 0)?;
        let s = &tail[..end];
        if s.is_empty() || !s.iter().all(|&c| c.is_ascii_graphic() || c == b' ') {
            return None;
        }
        Some(String::from_utf8_lossy(s).into_owned())
    }
    fn ident16(&self, va: u64) -> Option<String> {
        let o = self.off(va, 2)?;
        let mut out = String::new();
        let mut i = o;
        while i + 1 < self.data.len() && out.len() < 200 {
            let c = u16::from_le_bytes([self.data[i], self.data[i + 1]]);
            if c == 0 {
                break;
            }
            let ch = char::from_u32(c as u32)?;
            if !(ch.is_ascii_alphanumeric() || ch == '_') {
                return None;
            }
            out.push(ch);
            i += 2;
        }
        if out.is_empty() || out.as_bytes()[0].is_ascii_digit() {
            return None;
        }
        Some(out)
    }
    /// Follow `jmp rel32` thunks (incremental linking / identical code folding).
    fn follow(&self, mut va: u64) -> u64 {
        for _ in 0..4 {
            match self.bytes(va, 5) {
                Some(b) if b[0] == 0xE9 => va = (va as i64 + 5 + i32::from_le_bytes([b[1], b[2], b[3], b[4]]) as i64) as u64,
                Some(b) if b[0] == 0xEB => va = (va as i64 + 2 + b[1] as i8 as i64) as u64,
                _ => break,
            }
        }
        va
    }
    /// Target of the first `lea rdx, [rip+disp32]` of the function at `va` (stops at `ret`).
    fn lea_rdx(&self, va: u64) -> Option<u64> {
        let f = self.follow(va);
        let code = self.bytes(f, 96).or_else(|| self.bytes(f, 32))?;
        let mut i = 0;
        while i + 7 <= code.len() {
            if code[i] == 0x48 && code[i + 1] == 0x8D && code[i + 2] == 0x15 {
                let disp = i32::from_le_bytes([code[i + 3], code[i + 4], code[i + 5], code[i + 6]]) as i64;
                let t = (f as i64 + i as i64 + 7 + disp) as u64;
                if self.is_ptr(t) {
                    return Some(t);
                }
            }
            if code[i] == 0xC3 && i > 8 {
                break;
            }
            i += 1;
        }
        None
    }
}

// ------------------------------------------------------------------------------------------------- extract
struct Layout {
    /// offset of the type-specific pointer (struct / enum function) in property params
    typed: u64,
}

struct Ctx<'a, 'b> {
    img: &'b Image<'a>,
    layout: Layout,
    /// structs and enums met in property types: name -> params address (discovered from the classes)
    found_structs: std::cell::RefCell<HashMap<String, u64>>,
    found_enums: std::cell::RefCell<HashMap<String, u64>>,
}

impl<'a, 'b> Ctx<'a, 'b> {
    fn struct_name_of_fn(&self, f: u64) -> Option<String> {
        if f == 0 || !self.img.is_code(f) {
            return None;
        }
        let p = self.img.lea_rdx(f)?;
        let n = self.img.cstr(self.img.q(p + 24))?;
        self.found_structs.borrow_mut().entry(n.clone()).or_insert(p);
        Some(n)
    }
    fn enum_name_of_fn(&self, f: u64) -> Option<String> {
        if f == 0 || !self.img.is_code(f) {
            return None;
        }
        let p = self.img.lea_rdx(f)?;
        let n = self.img.cstr(self.img.q(p + 16))?;
        self.found_enums.borrow_mut().entry(n.clone()).or_insert(p);
        Some(n)
    }

    /// Consume one property from the end of `ptrs[..*end]`, with its inner properties.
    fn take(&self, ptrs: &[u64], end: &mut usize, depth: usize) -> Option<Prop> {
        if *end == 0 || depth > 8 {
            return None;
        }
        *end -= 1;
        let p = ptrs[*end];
        let img = self.img;
        let name = img.cstr(img.q(p)).unwrap_or_else(|| format!("prop_{:x}", p));
        let gen = img.bytes(p + 24, 1).map(|b| b[0]).unwrap_or(0xFF);
        let dim = (img.d(p + 32) & 0xFFFF) as u16;
        let typed = img.q(p + self.layout.typed);
        let ty = match gen & 0x3F {
            0x00 => Ty::Byte(self.enum_name_of_fn(typed)),
            0x01 => Ty::Int8,
            0x02 => Ty::Int16,
            0x03 => Ty::Int,
            0x04 => Ty::Int64,
            0x05 => Ty::UInt16,
            0x06 => Ty::UInt32,
            0x07 => Ty::UInt64,
            0x08 => Ty::Int,
            0x09 => Ty::UInt32,
            0x0A => Ty::Float,
            0x0B => Ty::Double,
            0x0C => Ty::Bool,
            0x0D | 0x10 => Ty::SoftObject,
            0x0E => Ty::WeakObject,
            0x0F => Ty::LazyObject,
            0x11 | 0x12 => Ty::Object,
            0x13 => Ty::Interface,
            0x14 => Ty::Name,
            0x15 => Ty::Str,
            0x16 => Ty::Array(Box::new(self.take(ptrs, end, depth + 1)?.ty)),
            0x17 => {
                let k = self.take(ptrs, end, depth + 1)?.ty;
                let v = self.take(ptrs, end, depth + 1)?.ty;
                Ty::Map(Box::new(k), Box::new(v))
            }
            0x18 => Ty::Set(Box::new(self.take(ptrs, end, depth + 1)?.ty)),
            0x19 => Ty::Struct(self.struct_name_of_fn(typed).unwrap_or_else(|| "?".into())),
            0x1A => Ty::Delegate,
            0x1B => Ty::MulticastInline,
            0x1C => Ty::MulticastSparse,
            0x1D => Ty::Text,
            0x1E => {
                let u = self.take(ptrs, end, depth + 1)?.ty;
                Ty::Enum(self.enum_name_of_fn(typed).unwrap_or_else(|| "?".into()), Box::new(u))
            }
            0x1F => Ty::FieldPath,
            0x20 => Ty::Double, // LargeWorldCoordinatesReal
            0x22 => Ty::Optional(Box::new(self.take(ptrs, end, depth + 1)?.ty)),
            x => Ty::Unknown(x),
        };
        Some(Prop { name, array_dim: dim.max(1), ty })
    }

    fn props(&self, array: u64, count: usize) -> Vec<Prop> {
        if array == 0 || count == 0 || count > 4096 || !self.img.is_ptr(array) {
            return Vec::new();
        }
        let ptrs: Vec<u64> = (0..count).map(|i| self.img.q(array + 8 * i as u64)).collect();
        let mut end = ptrs.len();
        let mut out = Vec::new();
        while end > 0 {
            match self.take(&ptrs, &mut end, 0) {
                Some(p) => out.push(p),
                None => break,
            }
        }
        out.reverse();
        out
    }
}

/// Registration entries found in the writable data: (kind, Z_Construct function, registered name).
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    ClassOrStruct,
    Enum,
}

fn scan_registrations(img: &Image) -> Vec<(Kind, u64, String)> {
    let mut out = Vec::new();
    for s in img.secs.iter().filter(|s| s.write && !s.exec) {
        let n = s.raw_size / 8;
        let words: Vec<u64> = (0..n).map(|i| {
            let o = s.raw + i * 8;
            img.data.get(o..o + 8).map(|b| u64::from_le_bytes(b.try_into().unwrap())).unwrap_or(0)
        }).collect();
        let mut i = 0;
        while i + 4 < words.len() {
            let (a, b, c, d) = (words[i], words[i + 1], words[i + 2], words[i + 3]);
            if img.is_code(a) && img.is_code(b) && img.is_rdata(c) && img.is_data(d) {
                if let Some(name) = img.ident16(c) {
                    out.push((Kind::ClassOrStruct, a, name));
                    i += 5;
                    continue;
                }
            }
            if img.is_code(a) && img.is_rdata(b) && img.is_data(c) && !img.is_rdata(d) {
                if let Some(name) = img.ident16(b) {
                    out.push((Kind::Enum, a, name));
                    i += 4;
                    continue;
                }
            }
            i += 1;
        }
    }
    out
}

pub fn extract(exe: &[u8]) -> Result<Schema> {
    let img = Image::parse(exe)?;
    let regs = scan_registrations(&img);
    if regs.is_empty() {
        return err("no reflection data found in the executable");
    }
    // Classes: the registration's first function is Z_Construct_UClass_X (struct and enum entries start with
    // StaticStruct / StaticEnum instead: their types are found through the properties that use them).
    let mut classes: Vec<(String, u64)> = Vec::new();
    let mut class_fn: HashMap<u64, String> = HashMap::new();
    for (kind, f, name) in &regs {
        if *kind != Kind::ClassOrStruct || name.len() < 2 || !matches!(name.as_bytes()[0], b'U' | b'A' | b'I') {
            continue;
        }
        let Some(p) = img.lea_rdx(*f) else { continue };
        let props = img.q(p + 40);
        let nprops = img.d(p + 64);
        let deps = img.q(p + 24);
        let config = img.q(p + 8);
        let valid = img.is_code(img.q(p))
            && (props == 0 || img.is_rdata(props) || img.is_data(props))
            && nprops < 4096
            && (deps == 0 || img.is_ptr(deps))
            && (config == 0 || img.cstr(config).is_some());
        if !valid {
            continue;
        }
        let short = name[1..].to_string();
        class_fn.insert(img.follow(*f), short.clone());
        classes.push((short, p));
    }
    if classes.is_empty() {
        return err("reflection tables found but no class could be read");
    }
    // Calibrate the type-specific pointer offset on the struct properties of the classes.
    let mut best = (0usize, 64u64);
    for cand in [40u64, 48, 56, 64, 72, 80] {
        let mut ok = 0;
        for (_, p) in classes.iter().take(600) {
            let arr = img.q(p + 40);
            let cnt = img.d(p + 64) as usize;
            if arr == 0 || cnt > 512 || !img.is_ptr(arr) {
                continue;
            }
            for k in 0..cnt {
                let pp = img.q(arr + 8 * k as u64);
                if img.bytes(pp + 24, 1).map(|b| b[0] & 0x3F) == Some(0x19) {
                    let f = img.q(pp + cand);
                    if img.is_code(f) && img.lea_rdx(f).and_then(|x| img.cstr(img.q(x + 24))).is_some() {
                        ok += 1;
                    }
                }
            }
        }
        if ok > best.0 {
            best = (ok, cand);
        }
    }
    let ctx = Ctx { img: &img, layout: Layout { typed: best.1 }, found_structs: Default::default(), found_enums: Default::default() };
    let mut schema = Schema::default();
    for (name, p) in &classes {
        let deps = img.q(p + 24);
        let ndeps = img.d(p + 56) as usize;
        let mut sup = None;
        if deps != 0 && ndeps > 0 && ndeps < 64 {
            sup = class_fn.get(&img.follow(img.q(deps))).cloned();
        }
        let props = ctx.props(img.q(p + 40), img.d(p + 64) as usize);
        schema.structs.insert(name.clone(), StructDef { name: name.clone(), super_name: sup, props });
    }
    // Structs, transitively (a struct's properties and super can name more structs).
    let mut done: std::collections::HashSet<String> = Default::default();
    loop {
        let todo: Vec<(String, u64)> = ctx.found_structs.borrow().iter().filter(|(n, _)| !done.contains(*n)).map(|(n, p)| (n.clone(), *p)).collect();
        if todo.is_empty() {
            break;
        }
        for (name, p) in todo {
            done.insert(name.clone());
            let sup = ctx.struct_name_of_fn(img.q(p + 8));
            let props = ctx.props(img.q(p + 48), img.d(p + 56) as usize);
            schema.structs.entry(name.clone()).or_insert(StructDef { name, super_name: sup, props });
        }
    }
    for (name, p) in ctx.found_enums.borrow().iter() {
        // FEnumParams: outer, display name fn, name, cpp type, enumerators {name, i64}, i32 count
        let arr = img.q(p + 32);
        let n = img.d(p + 40) as usize;
        let mut vals = Vec::new();
        if arr != 0 && n < 4096 {
            for k in 0..n {
                let e = arr + 16 * k as u64;
                if let Some(en) = img.cstr(img.q(e)) {
                    let short = en.rsplit("::").next().unwrap_or(&en).to_string();
                    vals.push((short, img.q(e + 8) as i64));
                }
            }
        }
        schema.enums.insert(name.clone(), vals);
    }
    Ok(schema)
}
