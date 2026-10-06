//! Oodle decompression through the official `oo2core_9_win64.dll`, loaded at run time.
//!
//! Oodle is not open source: omni downloads the DLL Epic redistributes (pinned SHA-256, see `omni/core/tools.py`)
//! and hands its path to `set_library`. Nothing here links against it, so the crate builds and tests without it.

use std::sync::OnceLock;

use super::reader::{err, Result};

type Decompress = unsafe extern "system" fn(
    comp: *const u8,
    comp_len: isize,
    raw: *mut u8,
    raw_len: isize,
    fuzz_safe: i32,
    check_crc: i32,
    verbosity: i32,
    dec_buf_base: *mut u8,
    dec_buf_size: isize,
    callback: *const u8,
    callback_data: *const u8,
    decoder_mem: *mut u8,
    decoder_mem_size: isize,
    thread_phase: i32,
) -> isize;

struct Lib {
    decompress: Decompress,
}

// SAFETY: the function pointer comes from a DLL that stays loaded for the life of the process.
unsafe impl Send for Lib {}
unsafe impl Sync for Lib {}

static LIB: OnceLock<std::result::Result<Lib, String>> = OnceLock::new();

#[cfg(windows)]
mod sys {
    #[link(name = "kernel32")]
    extern "system" {
        pub fn LoadLibraryW(name: *const u16) -> *mut core::ffi::c_void;
        pub fn GetProcAddress(module: *mut core::ffi::c_void, name: *const u8) -> *mut core::ffi::c_void;
    }
}

#[cfg(windows)]
fn load(path: &str) -> std::result::Result<Lib, String> {
    let wide: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        let h = sys::LoadLibraryW(wide.as_ptr());
        if h.is_null() {
            return Err(format!("cannot load {path}"));
        }
        let f = sys::GetProcAddress(h, b"OodleLZ_Decompress\0".as_ptr());
        if f.is_null() {
            return Err(format!("{path} has no OodleLZ_Decompress"));
        }
        Ok(Lib { decompress: std::mem::transmute::<*mut core::ffi::c_void, Decompress>(f) })
    }
}

#[cfg(not(windows))]
fn load(path: &str) -> std::result::Result<Lib, String> {
    Err(format!("Oodle is only supported on Windows ({path})"))
}

/// Load the DLL once; later calls report whether the first one worked.
pub fn set_library(path: &str) -> Result<()> {
    match LIB.get_or_init(|| load(path)) {
        Ok(_) => Ok(()),
        Err(e) => err(e.clone()),
    }
}

pub fn available() -> bool {
    matches!(LIB.get(), Some(Ok(_)))
}

pub fn decompress(src: &[u8], dst: &mut [u8]) -> Result<()> {
    let lib = match LIB.get() {
        Some(Ok(l)) => l,
        Some(Err(e)) => return err(e.clone()),
        None => return err("Oodle is needed for this game and was not loaded (oo2core_9_win64.dll)"),
    };
    let n = unsafe {
        (lib.decompress)(
            src.as_ptr(),
            src.len() as isize,
            dst.as_mut_ptr(),
            dst.len() as isize,
            1,
            0,
            0,
            std::ptr::null_mut(),
            0,
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null_mut(),
            0,
            3,
        )
    };
    if n != dst.len() as isize {
        return err(format!("Oodle decompression failed ({n} of {} bytes)", dst.len()));
    }
    Ok(())
}
