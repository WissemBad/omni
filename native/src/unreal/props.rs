//! Unversioned property reader: a struct's values in schema order, as written by cooked UE5 packages.
//!
//! Header: u16 fragments {skip count (7 bits), has zero mask (bit 7), last (bit 8), value count (bits 9-15)},
//! then a zero mask (1 byte if <= 8 bits, 2 if <= 16, else u32 words) over the values of fragments that have one.
//! A value flagged zero is not stored (default); every other value follows, in schema order. Structs without a
//! native serializer nest the same layout; native ones (vectors, colours, soft paths...) have fixed readers.

use super::reader::{err, Error, Reader, Result};
use super::reflect::{Schema, Ty};

#[derive(Clone, Debug)]
pub enum Value {
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Name(String),
    /// package index (FPackageIndex): < 0 import -(i+1), > 0 export i-1, 0 null
    Object(i32),
    SoftObject(String, String),
    Enum(String),
    Struct(String, Vec<(String, Value)>),
    Array(Vec<Value>),
    Map(Vec<(Value, Value)>),
    Floats(Vec<f64>),
    Unknown,
}

impl Value {
    pub fn field(&self, name: &str) -> Option<&Value> {
        match self {
            Value::Struct(_, f) => f.iter().find(|(n, _)| n == name).map(|(_, v)| v),
            _ => None,
        }
    }
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(i) => Some(*i),
            Value::Bool(b) => Some(*b as i64),
            Value::Float(f) => Some(*f as i64),
            _ => None,
        }
    }
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::Float(f) => Some(*f),
            Value::Int(i) => Some(*i as f64),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) | Value::Name(s) | Value::Enum(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_object(&self) -> Option<i32> {
        match self {
            Value::Object(i) => Some(*i),
            _ => None,
        }
    }
    pub fn as_array(&self) -> &[Value] {
        match self {
            Value::Array(a) => a,
            _ => &[],
        }
    }
}

pub type Props = Vec<(String, Value)>;

pub fn get<'a>(props: &'a Props, name: &str) -> Option<&'a Value> {
    props.iter().find(|(n, _)| n == name).map(|(_, v)| v)
}

pub struct Ctx<'s> {
    pub schema: &'s Schema,
    pub names: &'s [String],
    pub ue5: i32,
}

impl<'s> Ctx<'s> {
    pub fn lwc(&self) -> bool {
        self.ue5 >= 1004
    }

    pub fn fname(&self, r: &mut Reader) -> Result<String> {
        let i = r.u32()?;
        let n = r.u32()?;
        Ok(super::zen::mapped_name(self.names, i, n))
    }

    fn real(&self, r: &mut Reader) -> Result<f64> {
        if self.lwc() { r.f64() } else { Ok(r.f32()? as f64) }
    }

    fn reals(&self, r: &mut Reader, n: usize) -> Result<Value> {
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(self.real(r)?);
        }
        Ok(Value::Floats(v))
    }

    fn f32s(&self, r: &mut Reader, n: usize) -> Result<Value> {
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(r.f32()? as f64);
        }
        Ok(Value::Floats(v))
    }

    pub fn soft_path(&self, r: &mut Reader) -> Result<Value> {
        let path = if self.ue5 >= 1007 {
            let pkg = self.fname(r)?;
            let asset = self.fname(r)?;
            if asset == "None" { pkg } else { format!("{pkg}.{asset}") }
        } else {
            self.fname(r)?
        };
        let sub = r.fstring()?;
        Ok(Value::SoftObject(path, sub))
    }

    fn text(&self, r: &mut Reader) -> Result<Value> {
        let _flags = r.u32()?;
        let kind = r.i8()?;
        match kind {
            -1 => {
                if r.bool32()? {
                    return Ok(Value::Str(r.fstring()?));
                }
                Ok(Value::Str(String::new()))
            }
            0 => {
                let _ns = r.fstring()?;
                let _key = r.fstring()?;
                Ok(Value::Str(r.fstring()?))
            }
            11 => {
                let _table = self.fname(r)?;
                Ok(Value::Str(r.fstring()?))
            }
            k => err(format!("FText history {k} not supported")),
        }
    }

    /// Reader for structs that have a native serializer; None when the struct is read as properties.
    fn native(&self, name: &str, r: &mut Reader) -> Option<Result<Value>> {
        let v = match name {
            "Vector" | "Rotator" | "Vector3d" | "Rotator3d" => self.reals(r, 3),
            "Vector2D" | "Vector2d" | "DeprecateSlateVector2D" => self.reals(r, 2),
            "Vector4" | "Quat" | "Plane" | "Sphere" | "Vector4d" | "Quat4d" | "Plane4d" | "Sphere3d" => self.reals(r, 4),
            "Vector3f" | "Rotator3f" => self.f32s(r, 3),
            "Vector2f" => self.f32s(r, 2),
            "Vector4f" | "Quat4f" | "Plane4f" | "Sphere3f" => self.f32s(r, 4),
            "LinearColor" => self.f32s(r, 4),
            "Matrix" => self.reals(r, 16),
            "Matrix44f" => self.f32s(r, 16),
            "Box" => (|| {
                let mut v = Vec::new();
                for _ in 0..6 {
                    v.push(self.real(r)?);
                }
                r.u8()?;
                Ok(Value::Floats(v))
            })(),
            "Box2D" => (|| {
                let mut v = Vec::new();
                for _ in 0..4 {
                    v.push(self.real(r)?);
                }
                r.u8()?;
                Ok(Value::Floats(v))
            })(),
            "Box2f" => (|| {
                let v = self.f32s(r, 4)?;
                r.u8()?;
                Ok(v)
            })(),
            "Box3f" => (|| {
                let v = self.f32s(r, 6)?;
                r.u8()?;
                Ok(v)
            })(),
            "TwoVectors" => self.reals(r, 6),
            "Color" => r.bytes(4).map(|b| Value::Floats(vec![b[2] as f64, b[1] as f64, b[0] as f64, b[3] as f64])),
            "Guid" => r.bytes(16).map(|b| Value::Str(b.iter().map(|x| format!("{x:02x}")).collect())),
            "DateTime" | "Timespan" => r.i64().map(Value::Int),
            "FrameNumber" | "NavAgentSelector" => r.i32().map(|x| Value::Int(x as i64)),
            "IntPoint" | "Int32Point" | "IntVector2" | "Int32Vector2" | "UintVector2" | "Uint32Point" => {
                (|| Ok(Value::Floats(vec![r.i32()? as f64, r.i32()? as f64])))()
            }
            "IntVector" | "UintVector" => (|| Ok(Value::Floats(vec![r.i32()? as f64, r.i32()? as f64, r.i32()? as f64])))(),
            "IntVector4" | "UintVector4" | "Uint32Vector4" => (|| Ok(Value::Floats((0..4).map(|_| r.i32().map(|x| x as f64)).collect::<Result<Vec<_>>>()?)))(),
            "Int64Point" | "Int64Vector2" | "UInt64Point" | "UInt64Vector2" => r.skip(16).map(|_| Value::Unknown),
            "Int64Vector" | "UInt64Vector" => r.skip(24).map(|_| Value::Unknown),
            "SoftObjectPath" | "SoftClassPath" | "StringAssetReference" | "StringClassReference" => self.soft_path(r),
            "PerPlatformFloat" => self.per_platform(r, |r| r.f32().map(|x| Value::Float(x as f64))),
            "PerPlatformInt" => self.per_platform(r, |r| r.i32().map(|x| Value::Int(x as i64))),
            "PerPlatformBool" => self.per_platform(r, |r| r.bool32().map(Value::Bool)),
            "PerPlatformFrameRate" => self.per_platform(r, |r| Ok(Value::Floats(vec![r.i32()? as f64, r.i32()? as f64]))),
            "PerQualityLevelInt" => self.per_quality(r, |r| r.i32().map(|x| Value::Int(x as i64))),
            "PerQualityLevelFloat" => self.per_quality(r, |r| r.f32().map(|x| Value::Float(x as f64))),
            "GameplayTagContainer" => (|| {
                let n = r.count(8)?;
                let mut v = Vec::with_capacity(n);
                for _ in 0..n {
                    v.push(Value::Name(self.fname(r)?));
                }
                Ok(Value::Array(v))
            })(),
            "SmartName" => self.fname(r).map(Value::Name),
            "RichCurveKey" => r.skip(3 + 6 * 4).map(|_| Value::Unknown),
            "SimpleCurveKey" => r.skip(8).map(|_| Value::Unknown),
            "NameCurveKey" => (|| {
                r.f32()?;
                Ok(Value::Name(self.fname(r)?))
            })(),
            "StringCurveKey" => (|| {
                r.f32()?;
                Ok(Value::Str(r.fstring()?))
            })(),
            "SkeletalMeshSamplingLODBuiltData" => (|| {
                let n = r.count(4)?;
                r.skip(n * 4)?;
                let n = r.count(4)?;
                r.skip(n * 4)?;
                r.f32()?;
                Ok(Value::Unknown)
            })(),
            // triangles, bones, sampler (probabilities, aliases, total weight), vertices
            "SkeletalMeshSamplingRegionBuiltData" => (|| {
                for _ in 0..4 {
                    let n = r.count(4)?;
                    r.skip(n * 4)?;
                }
                r.f32()?;
                let n = r.count(4)?;
                r.skip(n * 4)?;
                Ok(Value::Unknown)
            })(),
            "MovieSceneFrameRange" => r.skip(2 * (1 + 4)).map(|_| Value::Unknown),
            "MovieSceneSequenceID" | "MovieSceneTrackIdentifier" | "MovieSceneSegmentIdentifier" => r.skip(4).map(|_| Value::Unknown),
            "MovieSceneEvaluationKey" => r.skip(12).map(|_| Value::Unknown),
            "UniqueNetIdRepl" => (|| {
                let size = r.i32()?;
                if size > 0 {
                    let _ty = self.fname(r)?;
                    let _s = r.fstring()?;
                }
                Ok(Value::Unknown)
            })(),
            _ => return None,
        };
        Some(v)
    }

    fn per_platform(&self, r: &mut Reader, f: impl Fn(&mut Reader) -> Result<Value>) -> Result<Value> {
        let cooked = r.bool32()?;
        let v = f(r)?;
        if !cooked {
            let n = r.count(8)?;
            for _ in 0..n {
                self.fname(r)?;
                f(r)?;
            }
        }
        Ok(v)
    }

    fn per_quality(&self, r: &mut Reader, f: impl Fn(&mut Reader) -> Result<Value>) -> Result<Value> {
        let _cooked = r.bool32()?;
        let v = f(r)?;
        let n = r.count(8)?;
        for _ in 0..n {
            r.i32()?;
            f(r)?;
        }
        Ok(v)
    }

    pub fn struct_value(&self, name: &str, r: &mut Reader) -> Result<Value> {
        if let Some(v) = self.native(name, r) {
            return v;
        }
        if !self.schema.has(name) {
            return err(format!("struct '{name}' has no layout (not in the game's reflection data)"));
        }
        Ok(Value::Struct(name.to_string(), self.properties(name, r)?))
    }

    /// Value of one property in unversioned form.
    pub fn value(&self, ty: &Ty, r: &mut Reader) -> Result<Value> {
        Ok(match ty {
            Ty::Bool => Value::Bool(r.u8()? != 0),
            Ty::Byte(Some(e)) => {
                let b = r.u8()? as i64;
                match self.schema.enum_name(e, b) {
                    Some(n) => Value::Enum(n.to_string()),
                    None => Value::Int(b),
                }
            }
            Ty::Byte(None) => Value::Int(r.u8()? as i64),
            Ty::Int8 => Value::Int(r.i8()? as i64),
            Ty::Int16 => Value::Int(r.i16()? as i64),
            Ty::UInt16 => Value::Int(r.u16()? as i64),
            Ty::Int => Value::Int(r.i32()? as i64),
            Ty::UInt32 => Value::Int(r.u32()? as i64),
            Ty::Int64 => Value::Int(r.i64()?),
            Ty::UInt64 => Value::Int(r.u64()? as i64),
            Ty::Float => Value::Float(r.f32()? as f64),
            Ty::Double => Value::Float(r.f64()?),
            Ty::Name => Value::Name(self.fname(r)?),
            Ty::Str => Value::Str(r.fstring()?),
            Ty::Text => self.text(r)?,
            Ty::Object | Ty::WeakObject | Ty::Interface => Value::Object(r.i32()?),
            Ty::LazyObject => {
                r.skip(16)?;
                Value::Unknown
            }
            Ty::SoftObject => self.soft_path(r)?,
            Ty::Delegate => {
                let o = r.i32()?;
                let _f = self.fname(r)?;
                Value::Object(o)
            }
            Ty::MulticastInline | Ty::MulticastSparse => {
                let n = r.count(12)?;
                for _ in 0..n {
                    r.i32()?;
                    self.fname(r)?;
                }
                Value::Unknown
            }
            Ty::FieldPath => {
                let n = r.count(8)?;
                for _ in 0..n {
                    self.fname(r)?;
                }
                r.i32()?;
                Value::Unknown
            }
            Ty::Struct(n) => self.struct_value(n, r)?,
            Ty::Enum(e, under) => {
                let v = match under.as_ref() {
                    Ty::Int8 | Ty::Byte(_) => r.u8()? as i64,
                    Ty::Int16 | Ty::UInt16 => r.u16()? as i64,
                    Ty::Int | Ty::UInt32 => r.u32()? as i64,
                    Ty::Int64 | Ty::UInt64 => r.i64()?,
                    _ => r.u8()? as i64,
                };
                match self.schema.enum_name(e, v) {
                    Some(n) => Value::Enum(n.to_string()),
                    None => Value::Int(v),
                }
            }
            Ty::Array(inner) => {
                let n = r.count(1)?;
                let mut v = Vec::with_capacity(n.min(1 << 16));
                for _ in 0..n {
                    v.push(self.value(inner, r)?);
                }
                Value::Array(v)
            }
            Ty::Set(inner) => {
                let removed = r.count(1)?;
                for _ in 0..removed {
                    self.value(inner, r)?;
                }
                let n = r.count(1)?;
                let mut v = Vec::with_capacity(n.min(1 << 16));
                for _ in 0..n {
                    v.push(self.value(inner, r)?);
                }
                Value::Array(v)
            }
            Ty::Map(k, val) => {
                let removed = r.count(1)?;
                for _ in 0..removed {
                    self.value(k, r)?;
                }
                let n = r.count(1)?;
                let mut v = Vec::with_capacity(n.min(1 << 16));
                for _ in 0..n {
                    let kk = self.value(k, r)?;
                    let vv = self.value(val, r)?;
                    v.push((kk, vv));
                }
                Value::Map(v)
            }
            Ty::Optional(inner) => {
                if r.bool32()? { self.value(inner, r)? } else { Value::Unknown }
            }
            Ty::Unknown(x) => return err(format!("property type {x:#x} not supported")),
        })
    }

    /// Unversioned properties of `struct_name` at `r` (header + values).
    pub fn properties(&self, struct_name: &str, r: &mut Reader) -> Result<Props> {
        let mut frags = Vec::new();
        loop {
            let f = r.u16()?;
            frags.push(f);
            if f & 0x100 != 0 || frags.len() > 4096 {
                break;
            }
        }
        let zero_bits: usize = frags.iter().filter(|f| *f & 0x80 != 0).map(|f| (*f >> 9) as usize).sum();
        let unmasked: usize = frags.iter().filter(|f| *f & 0x80 == 0).map(|f| (*f >> 9) as usize).sum();
        let mut mask = Vec::new();
        if zero_bits > 0 {
            let bytes = if zero_bits <= 8 { 1 } else if zero_bits <= 16 { 2 } else { zero_bits.div_ceil(32) * 4 };
            mask = r.bytes(bytes)?.to_vec();
        }
        let mut out = Vec::new();
        if zero_bits == 0 && unmasked == 0 {
            return Ok(out);
        }
        let mut idx = 0usize;
        let mut zi = 0usize;
        for f in frags {
            idx += (f & 0x7F) as usize;
            let n = (f >> 9) as usize;
            let has_zero = f & 0x80 != 0;
            for _ in 0..n {
                let zero = has_zero && (mask.get(zi / 8).copied().unwrap_or(0) >> (zi % 8)) & 1 != 0;
                if has_zero {
                    zi += 1;
                }
                let prop = self
                    .schema
                    .prop_at(struct_name, idx)
                    .ok_or_else(|| Error(format!("{struct_name}: no property at index {idx}")))?;
                if !zero {
                    let v = self.value(&prop.ty, r).map_err(|e| Error(format!("{struct_name}.{}: {e}", prop.name)))?;
                    out.push((prop.name.clone(), v));
                } else if matches!(prop.ty, Ty::Bool) {
                    out.push((prop.name.clone(), Value::Bool(false)));
                }
                idx += 1;
            }
        }
        Ok(out)
    }
}

/// Properties of an export of class `class` followed by the object guid; returns them and the offset of the
/// class's native data.
pub fn read_object(ctx: &Ctx, class: &str, data: &[u8], is_cdo: bool) -> Result<(Props, usize)> {
    let mut r = Reader::new(data);
    let props = ctx.properties(class, &mut r)?;
    if !is_cdo && r.left() >= 4 {
        if r.u32()? != 0 && r.left() >= 16 {
            r.skip(16)?;
        }
    }
    Ok((props, r.pos))
}
