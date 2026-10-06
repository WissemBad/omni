//! Localised texts of Glacier games (`.localized-textlist`, LOCR resource).
//!
//! Layout (verified on 007 First Light): `u8 flag`, `u32 offset` per language (the first offset gives the count),
//! then per language at its offset `u32 count` followed by `{u32 id, u32 length, length bytes, u8 0}` entries. The
//! id is the CRC32 of the text key and is the same in every language. The bytes of a text are enciphered in
//! 8-byte blocks (XTEA, little-endian words, zero padding): the structure is readable without any key, the texts
//! are not. 007 First Light does not use the key of the earlier Hitman games (checked on its files, all
//! variants of order and rounds), so the key is a parameter: a file is only decoded once a key makes the texts
//! readable ([`Locr::find_key`] / [`Locr::probe`]), never guessed text by text.

#[derive(Clone, Debug, Default)]
pub struct Locr {
    /// per language: (text id, enciphered bytes)
    pub languages: Vec<Vec<(u32, Vec<u8>)>>,
}

/// Key of the earlier Hitman games, the first candidate [`Locr::find_key`] tries.
pub const HITMAN_KEY: [u32; 4] = [0x30F9_5282, 0x1F48_C419, 0x295F_8548, 0x2A78_366D];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Cipher {
    pub key: [u32; 4],
    pub rounds: u32,
    pub big_endian: bool,
}

fn u32_at(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o.checked_add(4)?).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}

impl Locr {
    pub fn parse(d: &[u8]) -> Result<Locr, String> {
        let first = u32_at(d, 1).ok_or("truncated text list")? as usize;
        if first < 5 || (first - 1) % 4 != 0 || first > d.len() {
            return Err("not a text list".into());
        }
        let n = (first - 1) / 4;
        let mut languages = Vec::with_capacity(n);
        for l in 0..n {
            let mut o = u32_at(d, 1 + 4 * l).ok_or("truncated language table")? as usize;
            let count = u32_at(d, o).ok_or("language offset out of range")? as usize;
            o += 4;
            // every entry takes at least 9 bytes: a larger count cannot be real
            if count > d.len().saturating_sub(o) / 9 {
                return Err("entry count exceeds the file".into());
            }
            let mut entries = Vec::with_capacity(count);
            for _ in 0..count {
                let id = u32_at(d, o).ok_or("truncated entry")?;
                let len = u32_at(d, o + 4).ok_or("truncated entry")? as usize;
                let body = d.get(o + 8..o.checked_add(8 + len).ok_or("bad length")?).ok_or("text exceeds the file")?;
                entries.push((id, body.to_vec()));
                o += 9 + len;
            }
            languages.push(entries);
        }
        Ok(Locr { languages })
    }

    pub fn entries(&self) -> usize {
        self.languages.iter().map(Vec::len).sum()
    }

    /// Text `id` in language `lang` with `cipher`; `None` when absent or when it does not decode to text.
    pub fn text(&self, lang: usize, id: u32, cipher: &Cipher) -> Option<String> {
        let (_, body) = self.languages.get(lang)?.iter().find(|(i, _)| *i == id)?;
        decode(body, cipher)
    }

    /// Share (0..=1) of the entries that decode to text with `cipher`: how well a key fits this file.
    pub fn probe(&self, cipher: &Cipher) -> f32 {
        let mut total = 0usize;
        let mut ok = 0usize;
        for e in self.languages.iter().flatten().filter(|(_, b)| !b.is_empty()).take(256) {
            total += 1;
            ok += decode(&e.1, cipher).is_some() as usize;
        }
        if total == 0 { 0.0 } else { ok as f32 / total as f32 }
    }

    /// The first of `keys` (tried with both word orders and the usual round counts) that reads this file.
    pub fn find_key(&self, keys: &[[u32; 4]]) -> Option<Cipher> {
        for key in keys {
            for big_endian in [false, true] {
                for rounds in [32, 16, 64] {
                    let c = Cipher { key: *key, rounds, big_endian };
                    if self.probe(&c) >= 0.9 {
                        return Some(c);
                    }
                }
            }
        }
        None
    }

    /// Every text of a language as `(id, text)`; texts that do not decode are left out.
    pub fn texts(&self, lang: usize, cipher: &Cipher) -> Vec<(u32, String)> {
        self.languages
            .get(lang)
            .map(|l| l.iter().filter_map(|(i, b)| decode(b, cipher).map(|t| (*i, t))).collect())
            .unwrap_or_default()
    }
}

fn decipher_block(v: [u32; 2], c: &Cipher) -> [u32; 2] {
    const DELTA: u32 = 0x9E37_79B9;
    let (mut v0, mut v1) = (v[0], v[1]);
    let mut sum = DELTA.wrapping_mul(c.rounds);
    for _ in 0..c.rounds {
        v1 = v1.wrapping_sub((v0 << 4 ^ v0 >> 5).wrapping_add(v0) ^ sum.wrapping_add(c.key[(sum >> 11 & 3) as usize]));
        sum = sum.wrapping_sub(DELTA);
        v0 = v0.wrapping_sub((v1 << 4 ^ v1 >> 5).wrapping_add(v1) ^ sum.wrapping_add(c.key[(sum & 3) as usize]));
    }
    [v0, v1]
}

/// Deciphered bytes of a text (zero padding removed), or `None` when they are not UTF-8 text.
fn decode(body: &[u8], c: &Cipher) -> Option<String> {
    if body.is_empty() {
        return Some(String::new());
    }
    if body.len() % 8 != 0 {
        return None;
    }
    let word = |b: &[u8]| {
        let a: [u8; 4] = b.try_into().unwrap();
        if c.big_endian { u32::from_be_bytes(a) } else { u32::from_le_bytes(a) }
    };
    let mut out = Vec::with_capacity(body.len());
    for blk in body.chunks_exact(8) {
        let [a, b] = decipher_block([word(&blk[..4]), word(&blk[4..])], c);
        for w in [a, b] {
            out.extend(if c.big_endian { w.to_be_bytes() } else { w.to_le_bytes() });
        }
    }
    while out.last() == Some(&0) {
        out.pop();
    }
    let s = String::from_utf8(out).ok()?;
    // text, not noise: no control characters other than line breaks and tabs
    s.chars().all(|ch| !ch.is_control() || matches!(ch, '\n' | '\r' | '\t')).then_some(s)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    fn encipher_block(v: [u32; 2], c: &Cipher) -> [u32; 2] {
        const DELTA: u32 = 0x9E37_79B9;
        let (mut v0, mut v1) = (v[0], v[1]);
        let mut sum = 0u32;
        for _ in 0..c.rounds {
            v0 = v0.wrapping_add((v1 << 4 ^ v1 >> 5).wrapping_add(v1) ^ sum.wrapping_add(c.key[(sum & 3) as usize]));
            sum = sum.wrapping_add(DELTA);
            v1 = v1.wrapping_add((v0 << 4 ^ v0 >> 5).wrapping_add(v0) ^ sum.wrapping_add(c.key[(sum >> 11 & 3) as usize]));
        }
        [v0, v1]
    }

    fn encrypt(text: &str, c: &Cipher) -> Vec<u8> {
        let mut b = text.as_bytes().to_vec();
        b.push(0);
        while b.len() % 8 != 0 {
            b.push(0);
        }
        let word = |x: &[u8]| {
            let a: [u8; 4] = x.try_into().unwrap();
            if c.big_endian { u32::from_be_bytes(a) } else { u32::from_le_bytes(a) }
        };
        let mut out = Vec::new();
        for blk in b.chunks_exact(8) {
            for w in encipher_block([word(&blk[..4]), word(&blk[4..])], c) {
                out.extend(if c.big_endian { w.to_be_bytes() } else { w.to_le_bytes() });
            }
        }
        out
    }

    /// A file of `langs` languages with the given `(id, text)` entries, enciphered with `c`.
    pub(crate) fn file(langs: usize, entries: &[(u32, &str)], c: &Cipher) -> Vec<u8> {
        let mut out = vec![0u8];
        let table = 1 + 4 * langs;
        let mut body = Vec::new();
        for _ in 0..langs {
            out.extend(((table + body.len()) as u32).to_le_bytes());
            body.extend((entries.len() as u32).to_le_bytes());
            for (id, t) in entries {
                let e = encrypt(t, c);
                body.extend(id.to_le_bytes());
                body.extend((e.len() as u32).to_le_bytes());
                body.extend(e);
                body.push(0);
            }
        }
        out.extend(body);
        out
    }

    #[test]
    fn xtea_matches_the_reference_vector() {
        // reference algorithm (Needham-Wheeler), transcribed separately in Python: key 00..0f, 32 rounds
        let key = [0x0302_0100u32, 0x0706_0504, 0x0b0a_0908, 0x0f0e_0d0c];
        let c = Cipher { key, rounds: 32, big_endian: false };
        let enc = encipher_block([0x4142_4344, 0x4546_4748], &c);
        assert_eq!(enc, [0x9f78_6202, 0xda82_004a]);
        assert_eq!(decipher_block(enc, &c), [0x4142_4344, 0x4546_4748]);
    }

    #[test]
    fn reads_every_language_and_decodes_with_the_right_key() {
        let c = Cipher { key: HITMAN_KEY, rounds: 32, big_endian: false };
        let d = file(3, &[(0xEF6F0ACC, "Tuxedo – Lime"), (7, "Costume de plongée")], &c);
        let l = Locr::parse(&d).unwrap();
        assert_eq!(l.languages.len(), 3);
        assert_eq!(l.entries(), 6);
        assert_eq!(l.text(2, 0xEF6F0ACC, &c).as_deref(), Some("Tuxedo – Lime"));
        assert_eq!(l.text(0, 7, &c).as_deref(), Some("Costume de plongée"));
        assert!(l.probe(&c) > 0.99);
        assert_eq!(l.find_key(&[[1, 2, 3, 4], HITMAN_KEY]), Some(c));
    }

    #[test]
    fn a_wrong_key_reads_nothing() {
        let c = Cipher { key: HITMAN_KEY, rounds: 32, big_endian: false };
        let d = file(2, &[(1, "Black Tie"), (2, "Dinner jacket"), (3, "Gilded suit")], &c);
        let l = Locr::parse(&d).unwrap();
        let wrong = Cipher { key: [9, 8, 7, 6], rounds: 32, big_endian: false };
        assert!(l.probe(&wrong) < 0.2);
        assert_eq!(l.find_key(&[[9, 8, 7, 6]]), None);
        assert!(l.texts(0, &wrong).len() < 2);
    }

    #[test]
    fn finds_the_word_order_and_round_count() {
        let c = Cipher { key: HITMAN_KEY, rounds: 16, big_endian: true };
        let d = file(1, &[(1, "Black Tie"), (2, "Dinner jacket")], &c);
        assert_eq!(Locr::parse(&d).unwrap().find_key(&[HITMAN_KEY]), Some(c));
    }

    #[test]
    fn rejects_malformed_files() {
        for d in [&b""[..], b"\0", b"\0\x03\0\0\0", &[0xFFu8; 64], b"\0\x05\0\0\0\xff\xff\xff\xff"] {
            assert!(Locr::parse(d).is_err());
        }
        let c = Cipher { key: HITMAN_KEY, rounds: 32, big_endian: false };
        let mut d = file(1, &[(1, "abc")], &c);
        d.truncate(d.len() - 6);
        assert!(Locr::parse(&d).is_err());
    }
}
