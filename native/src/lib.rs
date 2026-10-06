//! omni_native: the parts of omni that need native speed or a hardened binary parser.
//!
//! Every entry point releases the GIL while it works (callers run several conversions in threads/processes)
//! and reports bad input as a Python exception. Headers are checked against the file before anything is
//! allocated from them (a failed allocation aborts the process, which no exception can catch); a remaining
//! panic reaches Python as PyO3's ``PanicException``, which ``omni.native`` re-raises as ``NativeError``.
//!
//! Built by maturin as a CPython extension (features `python` + `parallel`). `cargo test --no-default-features`
//! tests the core modules without Python (the `par` shim then runs sequentially).

pub mod aloc;
pub mod audio;
pub mod entity;
pub mod eta;
pub mod gltf;
pub mod geom;
pub mod lbs;
pub mod skin;
pub mod smd;
pub mod texture;
pub mod vtf;
pub mod flac;
pub mod binka;
pub mod ww2ogg;
pub mod wwise;
pub mod par;
pub mod scan;
#[cfg(test)]
mod fuzz;
pub mod rpkg;
pub mod rpkg_store;
pub mod unreal;

#[cfg(feature = "python")]
mod py;
#[cfg(feature = "python")]
mod py_media;
#[cfg(feature = "python")]
mod py_eta;
#[cfg(feature = "python")]
mod py_gltf;
#[cfg(feature = "python")]
mod py_unreal;
#[cfg(feature = "python")]
mod py_glacier;
