//! AES-256 ECB decryption of encrypted IoStore/pak data (UE encrypts 16-byte blocks independently).

use aes::cipher::{BlockCipherDecrypt, KeyInit};

pub fn decrypt_ecb(key: &[u8; 32], data: &mut [u8]) {
    let c = aes::Aes256::new(key.into());
    for block in data.chunks_exact_mut(16) {
        let block: &mut [u8; 16] = block.try_into().expect("16-byte chunk");
        c.decrypt_block(block.into());
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decrypts_the_fips_197_vector_block_by_block() {
        let key: [u8; 32] = std::array::from_fn(|i| i as u8);
        let plain: Vec<u8> = (0..16u8).map(|i| i * 0x11).collect();
        let cipher = [0x8e, 0xa2, 0xb7, 0xca, 0x51, 0x67, 0x45, 0xbf, 0xea, 0xfc, 0x49, 0x90, 0x4b, 0x49, 0x60, 0x89];
        let mut data = [cipher, cipher].concat();
        decrypt_ecb(&key, &mut data);
        assert_eq!(&data[..16], &plain[..]);
        assert_eq!(&data[16..], &plain[..]);     // every block is independent (ECB)
        let mut tail = [cipher.as_slice(), &[7u8; 5]].concat();
        decrypt_ecb(&key, &mut tail);
        assert_eq!(&tail[16..], &[7u8; 5]);      // a partial last block is left alone
    }
}
