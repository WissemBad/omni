//! Texture slots of a Glacier material class (`.materialclass` MATE resource).
//!
//! A class is a compiled shader package; next to the bytecode it keeps the list of its parameters as plain strings
//! (data-relative pointers to NUL-terminated names): each texture slot (`mapTexture2D_03`, the name a material
//! instance uses) is followed by the name the class gives it (`mapSpecular`, `mapEmissive`,
//! `mapSpecular_R_SpecularLevel_G_Roughness_B_Metallic`...). The generic slot names mean different things in
//! different classes, this list is the authority.

/// (slot, meaning) of every texture slot of the class, in file order; `None` when the class names the slot only.
pub fn slots(d: &[u8]) -> Vec<(String, Option<String>)> {
    // strings of the parameter list: "map..." names that start right after a non-printable byte
    let mut names: Vec<String> = Vec::new();
    let mut i = 0;
    while i < d.len() {
        let starts = d.len() - i > 4 && &d[i..i + 3] == b"map" && (i == 0 || !(0x20..0x7F).contains(&d[i - 1]));
        if starts {
            let end = d[i..].iter().take(200).position(|&c| c == 0);
            if let Some(e) = end {
                let s = &d[i..i + e];
                if s.len() >= 4 && s.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'_') {
                    let name = String::from_utf8_lossy(s).into_owned();
                    if names.contains(&name) {
                        break; // the list repeats once: the first run is the class's own parameter list
                    }
                    names.push(name);
                    i += e + 1;
                    continue;
                }
            }
        }
        i += 1;
    }
    let generic = |s: &str| {
        let l = s.to_ascii_lowercase();
        l.starts_with("maptexture") || l.starts_with("mapred") || l.starts_with("mapgreen")
    };
    let mut out = Vec::new();
    let mut k = 0;
    while k < names.len() {
        if generic(&names[k]) && k + 1 < names.len() && !names[k + 1].to_ascii_lowercase().starts_with("maptexture") {
            out.push((names[k].clone(), Some(names[k + 1].clone())));
            k += 2;
        } else {
            out.push((names[k].clone(), None));
            k += 1;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairs_generic_slots_with_their_meaning() {
        let mut d = vec![1u8, 2, 3];
        for s in ["mapTexture2D_01", "mapDiffuse", "ConstantVector1D_01_Value", "mapTexture2D_04", "mapEmissive", "mapTexture2D_02", "mapTexture2DNormal_01", "mapNormal", "mapTex_Basecolor"] {
            d.extend(s.as_bytes());
            d.push(0);
            d.push(0);
        }
        d.extend(b"mapTexture2D_01\0");
        let s = slots(&d);
        assert_eq!(s[0], ("mapTexture2D_01".into(), Some("mapDiffuse".into())));
        assert_eq!(s[1], ("mapTexture2D_04".into(), Some("mapEmissive".into())));
        assert_eq!(s[2], ("mapTexture2D_02".into(), None));
        assert_eq!(s[3], ("mapTexture2DNormal_01".into(), Some("mapNormal".into())));
        assert_eq!(s[4], ("mapTex_Basecolor".into(), None));
        assert_eq!(s.len(), 5);
    }

    #[test]
    fn garbage_gives_nothing_or_something_but_never_panics() {
        assert!(slots(&[]).is_empty());
        assert!(slots(b"map").is_empty());
        let _ = slots(&[b'm', b'a', b'p', b'x', 0, b'm', b'a', b'p', b'Q']);
    }
}
