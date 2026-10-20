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
pub mod binka;
pub mod entity;
pub mod eta;
pub mod flac;
#[cfg(test)]
mod fuzz;
pub mod geom;
pub mod gltf;
pub mod lbs;
pub mod limits;
pub mod mate;
pub mod modellist;
pub mod par;
pub mod rpkg;
pub mod rpkg_store;
pub mod scan;
pub mod schema;
pub mod sign;
pub mod skin;
pub mod smd;
pub mod source_anim;
pub mod texture;
pub mod unreal;
pub mod vtf;
pub mod ww2ogg;
pub mod wwise;

#[cfg(feature = "python")]
mod py;
#[cfg(feature = "python")]
mod py_anim;
#[cfg(feature = "python")]
mod py_eta;
#[cfg(feature = "python")]
mod py_glacier;
#[cfg(feature = "python")]
mod py_gltf;
#[cfg(feature = "python")]
mod py_media;
#[cfg(feature = "python")]
mod py_unreal;
