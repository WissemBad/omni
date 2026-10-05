//! Data parallelism: rayon when the `parallel` feature is on (native module), plain sequential iterators
//! otherwise (WebAssembly build: one instance per thread, the caller runs several instances in parallel).
//! The sequential shim exposes the few rayon method names the crate uses, returning std iterators.

#[cfg(feature = "parallel")]
pub use rayon::prelude::*;

#[cfg(not(feature = "parallel"))]
pub trait ParSlice<T> {
    fn par_iter(&self) -> std::slice::Iter<'_, T>;
    fn par_chunks(&self, n: usize) -> std::slice::Chunks<'_, T>;
}

#[cfg(not(feature = "parallel"))]
impl<T> ParSlice<T> for [T] {
    fn par_iter(&self) -> std::slice::Iter<'_, T> {
        self.iter()
    }
    fn par_chunks(&self, n: usize) -> std::slice::Chunks<'_, T> {
        self.chunks(n)
    }
}

#[cfg(not(feature = "parallel"))]
pub trait ParSliceMut<T> {
    fn par_chunks_mut(&mut self, n: usize) -> std::slice::ChunksMut<'_, T>;
}

#[cfg(not(feature = "parallel"))]
impl<T> ParSliceMut<T> for [T] {
    fn par_chunks_mut(&mut self, n: usize) -> std::slice::ChunksMut<'_, T> {
        self.chunks_mut(n)
    }
}

#[cfg(not(feature = "parallel"))]
pub trait IntoParIter: Iterator + Sized {
    fn into_par_iter(self) -> Self {
        self
    }
}

#[cfg(not(feature = "parallel"))]
impl<I: Iterator> IntoParIter for I {}

#[cfg(not(feature = "parallel"))]
pub trait FlatMapIter: Iterator + Sized {
    fn flat_map_iter<U: IntoIterator, F: FnMut(Self::Item) -> U>(self, f: F) -> std::iter::FlatMap<Self, U, F> {
        self.flat_map(f)
    }
}

#[cfg(not(feature = "parallel"))]
impl<I: Iterator> FlatMapIter for I {}
