//! omni_native: the parts of omni that need native speed or a hardened binary parser.
//!
//! Every entry point releases the GIL while it works (callers run several conversions in threads/processes)
//! and reports bad input as a Python exception (Rust panics are turned into exceptions by PyO3), so a
//! corrupt game file can never take the converter down.
//!
//! The crate is built two ways from the same core modules:
//!   * a CPython extension (features `python` + `parallel`, built by maturin): fastest, multi-threaded;
//!   * a WebAssembly module (`--target wasm32-unknown-unknown --no-default-features`, see wasm_api.rs) run by
//!     wasmtime from Python: portable and sandboxed, used when the native module cannot be loaded.

pub mod aloc;
pub mod audio;
pub mod entity;
pub mod geom;
pub mod lbs;
pub mod skin;
pub mod smd;
pub mod texture;
pub mod vtf;
pub mod flac;
pub mod ww2ogg;
pub mod wwise;
pub mod par;
#[cfg(not(target_arch = "wasm32"))]
pub mod rpkg;

#[cfg(feature = "python")]
mod py;
#[cfg(feature = "python")]
mod py_media;
#[cfg(target_arch = "wasm32")]
mod wasm_api;
