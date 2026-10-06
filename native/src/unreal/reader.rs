//! Bounds-checked little-endian reader shared by the Unreal parsers: every length read from a file is checked
//! against what is left before anything is allocated from it.

use std::fmt;

#[derive(Debug, Clone)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

pub fn err<T>(msg: impl Into<String>) -> Result<T> {
    Err(Error(msg.into()))
}

#[derive(Clone)]
pub struct Reader<'a> {
    pub data: &'a [u8],
    pub pos: usize,
}

macro_rules! prim {
    ($name:ident, $t:ty, $n:expr) => {
        #[inline]
        pub fn $name(&mut self) -> Result<$t> {
            let b = self.bytes($n)?;
            Ok(<$t>::from_le_bytes(b.try_into().unwrap()))
        }
    };
}

impl<'a> Reader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Reader { data, pos: 0 }
    }
    pub fn at(data: &'a [u8], pos: usize) -> Self {
        Reader { data, pos }
    }
    #[inline]
    pub fn left(&self) -> usize {
        self.data.len().saturating_sub(self.pos)
    }
    pub fn seek(&mut self, pos: usize) -> Result<()> {
        if pos > self.data.len() {
            return err(format!("seek past the end ({pos} > {})", self.data.len()));
        }
        self.pos = pos;
        Ok(())
    }
    pub fn skip(&mut self, n: usize) -> Result<()> {
        self.bytes(n).map(|_| ())
    }
    #[inline]
    pub fn bytes(&mut self, n: usize) -> Result<&'a [u8]> {
        if n > self.left() {
            return err(format!("truncated data: need {n} bytes at {}, {} left", self.pos, self.left()));
        }
        let s = &self.data[self.pos..self.pos + n];
        self.pos += n;
        Ok(s)
    }
    prim!(u8, u8, 1);
    prim!(i8, i8, 1);
    prim!(u16, u16, 2);
    prim!(i16, i16, 2);
    prim!(u32, u32, 4);
    prim!(i32, i32, 4);
    prim!(u64, u64, 8);
    prim!(i64, i64, 8);
    prim!(f32, f32, 4);
    prim!(f64, f64, 8);

    /// UE `bool` serialised as a 32-bit integer.
    pub fn bool32(&mut self) -> Result<bool> {
        Ok(self.u32()? != 0)
    }

    /// A count that must fit in what is left, each element taking at least `elem` bytes.
    pub fn count(&mut self, elem: usize) -> Result<usize> {
        let n = self.i32()?;
        self.check_count(n as i64, elem)
    }
    pub fn check_count(&self, n: i64, elem: usize) -> Result<usize> {
        if n < 0 {
            return err(format!("negative count {n} at {}", self.pos));
        }
        let n = n as usize;
        if n.saturating_mul(elem.max(1)) > self.left() {
            return err(format!("count {n} x {elem} bytes larger than the data at {}", self.pos));
        }
        Ok(n)
    }

    /// FString: i32 length (negative = UTF-16), terminating NUL included.
    pub fn fstring(&mut self) -> Result<String> {
        let n = self.i32()?;
        if n == 0 {
            return Ok(String::new());
        }
        if n > 0 {
            let b = self.bytes(n as usize)?;
            let b = b.strip_suffix(&[0]).unwrap_or(b);
            Ok(String::from_utf8_lossy(b).into_owned())
        } else {
            let n = (n as i64).unsigned_abs() as usize;
            let b = self.bytes(n.checked_mul(2).ok_or(Error("string length".into()))?)?;
            let w: Vec<u16> = b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            let w = w.strip_suffix(&[0]).unwrap_or(&w);
            Ok(String::from_utf16_lossy(w))
        }
    }

    pub fn guid(&mut self) -> Result<[u8; 16]> {
        Ok(self.bytes(16)?.try_into().unwrap())
    }

    pub fn array<T>(&mut self, elem: usize, mut f: impl FnMut(&mut Self) -> Result<T>) -> Result<Vec<T>> {
        let n = self.count(elem)?;
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(f(self)?);
        }
        Ok(v)
    }

    pub fn n_of<T>(&mut self, n: usize, elem: usize, mut f: impl FnMut(&mut Self) -> Result<T>) -> Result<Vec<T>> {
        self.check_count(n as i64, elem)?;
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(f(self)?);
        }
        Ok(v)
    }

    pub fn f32s(&mut self, n: usize) -> Result<Vec<f32>> {
        let b = self.bytes(n.checked_mul(4).ok_or(Error("size".into()))?)?;
        Ok(b.chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect())
    }
}
