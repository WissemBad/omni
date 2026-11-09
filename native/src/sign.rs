//! Release signatures: ed25519 over a release's `SHA256SUMS.txt`, so the updater installs only what the release key
//! signed (the sums alone come from the same place as the installer and prove nothing about who published it).
//! The secret key never reaches the repository: the release script passes its 32-byte seed.

use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};

fn seed(secret: &[u8]) -> Result<[u8; 32], String> {
    secret
        .try_into()
        .map_err(|_| format!("ed25519 secret key: {} bytes, 32 expected", secret.len()))
}

/// Public key (32 bytes) of a secret seed.
pub fn public_key(secret: &[u8]) -> Result<[u8; 32], String> {
    Ok(SigningKey::from_bytes(&seed(secret)?)
        .verifying_key()
        .to_bytes())
}

/// Signature (64 bytes) of `message`.
pub fn sign(secret: &[u8], message: &[u8]) -> Result<[u8; 64], String> {
    Ok(SigningKey::from_bytes(&seed(secret)?)
        .sign(message)
        .to_bytes())
}

/// Whether `signature` is the signature of `message` by `public`; any malformed input is simply not valid.
pub fn verify(public: &[u8], message: &[u8], signature: &[u8]) -> bool {
    let (Ok(public), Ok(signature)) = (
        <[u8; 32]>::try_from(public),
        <[u8; 64]>::try_from(signature),
    ) else {
        return false;
    };
    match VerifyingKey::from_bytes(&public) {
        Ok(key) => key
            .verify_strict(message, &Signature::from_bytes(&signature))
            .is_ok(),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signs_and_verifies() {
        let secret = [7u8; 32];
        let public = public_key(&secret).unwrap();
        let sig = sign(&secret, b"sums").unwrap();
        assert!(verify(&public, b"sums", &sig));
        assert!(!verify(&public, b"sumz", &sig));
        let mut bad = sig;
        bad[0] ^= 1;
        assert!(!verify(&public, b"sums", &bad));
        assert!(!verify(&public[..31], b"sums", &sig));
        assert!(!verify(&public, b"sums", &sig[..63]));
        assert!(!verify(&public_key(&[8u8; 32]).unwrap(), b"sums", &sig));
        assert!(sign(&[0u8; 31], b"x").is_err());
    }
}
