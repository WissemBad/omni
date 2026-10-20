//! Allocation bounds for sizes read from game files.
//!
//! A count read from a header must be checked against the bytes that back it before anything is allocated from it:
//! on Windows a failed allocation aborts the process (no exception reaches Python), and Linux overcommit hides the
//! problem until the pages are touched. `checked_len` is that check; `MAX_ALLOC` caps what one buffer may take even
//! when the file is large enough to claim it.

/// Largest single buffer a parser may allocate from file-provided sizes (2 GiB, the largest RPKG resource).
pub const MAX_ALLOC: usize = 1 << 31;

/// Deepest nesting a recursive parser follows (types inside types, structs inside structs).
pub const MAX_DEPTH: usize = 64;

/// Byte length of `count` elements of `elem_size` bytes, refused when it overflows, exceeds `available` (the bytes
/// that must hold them, or the most the input can produce) or exceeds `MAX_ALLOC`.
pub fn checked_len(count: usize, elem_size: usize, available: usize) -> Result<usize, String> {
    let n = count.checked_mul(elem_size).ok_or_else(|| format!("size overflow: {count} x {elem_size} bytes"))?;
    if n > available {
        return Err(format!("{count} x {elem_size} bytes larger than the {available} bytes available"));
    }
    if n > MAX_ALLOC {
        return Err(format!("{n} bytes is more than one buffer may take ({MAX_ALLOC})"));
    }
    Ok(n)
}

/// `cargo test` allocator: refuses any single allocation above 512 MiB, so a size taken from a corrupt header fails
/// the test run on every platform (Linux overcommit would otherwise let it through). It returns null, which makes
/// std abort with "memory allocation of N bytes failed": an allocator must not unwind, so it cannot panic.
#[cfg(test)]
mod capped {
    use std::alloc::{GlobalAlloc, Layout, System};

    pub const CAP: usize = 512 << 20;

    pub struct Capped;

    unsafe impl GlobalAlloc for Capped {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            if layout.size() > CAP { std::ptr::null_mut() } else { System.alloc(layout) }
        }
        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            if layout.size() > CAP { std::ptr::null_mut() } else { System.alloc_zeroed(layout) }
        }
        unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
            if new_size > CAP { std::ptr::null_mut() } else { System.realloc(ptr, layout, new_size) }
        }
        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            System.dealloc(ptr, layout)
        }
    }

    #[global_allocator]
    static GLOBAL: Capped = Capped;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checked_len_refuses_what_the_data_cannot_hold() {
        assert_eq!(checked_len(10, 4, 40), Ok(40));
        assert_eq!(checked_len(0, usize::MAX, 0), Ok(0));
        assert!(checked_len(11, 4, 40).is_err());
        assert!(checked_len(usize::MAX, 2, usize::MAX).is_err());
        assert!(checked_len(MAX_ALLOC + 1, 1, usize::MAX).is_err());
    }

    #[test]
    fn test_allocator_refuses_huge_buffers() {
        let mut v: Vec<u8> = Vec::new();
        assert!(v.try_reserve_exact(capped::CAP + 1).is_err());
        assert!(v.try_reserve_exact(1 << 20).is_ok());
    }
}
