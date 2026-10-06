//! AES-256 ECB decryption of encrypted IoStore/pak data (UE encrypts 16-byte blocks independently).

use aes::cipher::{generic_array::GenericArray, BlockDecrypt, KeyInit};

pub fn decrypt_ecb(key: &[u8; 32], data: &mut [u8]) {
    let c = aes::Aes256::new(GenericArray::from_slice(key));
    for block in data.chunks_exact_mut(16) {
        c.decrypt_block(GenericArray::from_mut_slice(block));
    }
}

/// `0x...` hex or base64 key text -> 32 bytes.
pub fn parse_key(text: &str) -> Option<[u8; 32]> {
    let t = text.trim();
    let hex = t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")).unwrap_or(t);
    if hex.len() == 64 && hex.bytes().all(|c| c.is_ascii_hexdigit()) {
        let mut k = [0u8; 32];
        for i in 0..32 {
            k[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).ok()?;
        }
        return Some(k);
    }
    None
}
