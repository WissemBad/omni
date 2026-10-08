//! The text file a user keeps of the models to extract: one GMod model per line (`models/props/chair.mdl`).
//!
//! Lines come from anywhere: a copied path, an Explorer path of the addon (`...\garrysmod\addons\x\models\a.mdl`),
//! a sibling file of the model (`a.dx90.vtx`, `a.vvd`), quotes, comments. Everything is brought to the lower-case
//! `models/<path>.mdl` form, once per model, in file order.

use std::collections::HashSet;

/// Files Source writes next to a `.mdl`: any of them names the model.
const SIBLINGS: [&str; 7] = [".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx", ".vvd", ".phy", ".ani"];

fn comment_start(line: &str) -> Option<usize> {
    let t = line.trim_start();
    if t.starts_with('#') || t.starts_with(';') || t.starts_with("//") {
        return Some(0);
    }
    // after a path: " // note", " # note", "\t# note"
    let mut best: Option<usize> = None;
    for pat in [" //", "\t//", " #", "\t#", " ;", "\t;"] {
        if let Some(i) = line.find(pat) {
            best = Some(best.map_or(i, |b| b.min(i)));
        }
    }
    best
}

/// One line as a `models/....mdl` path; `None` for blank lines, comments and files that are not a model.
pub fn normalise(line: &str) -> Option<String> {
    let line = line.trim_start_matches('\u{feff}');
    let line = &line[..comment_start(line).unwrap_or(line.len())];
    let s = line
        .trim()
        .trim_matches(|c: char| c == '"' || c == '\'' || c == ',' || c.is_whitespace())
        .replace('\\', "/")
        .to_lowercase();
    let mut s = s.as_str();
    if let Some(i) = s.find("models/") {
        s = &s[i..];
    } else {
        s = s.trim_start_matches("./").trim_start_matches('/');
    }
    if s.is_empty() || s.split('/').any(|p| p == ".." || p.is_empty() || p.contains(':')) {
        return None;
    }
    if let Some(base) = s.strip_suffix(".mdl") {
        return (!base.is_empty() && !base.ends_with('/')).then(|| s.to_string());
    }
    for ext in SIBLINGS {
        if let Some(base) = s.strip_suffix(ext) {
            return (!base.is_empty()).then(|| format!("{base}.mdl"));
        }
    }
    let leaf = s.rsplit('/').next().unwrap_or(s);
    if leaf.contains('.') {
        return None; // .vmt, .vtf, .lua...: not a model
    }
    Some(format!("{s}.mdl"))
}

/// Every model of the file, once, in order.
pub fn parse(text: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    text.lines().filter_map(normalise).filter(|m| seen.insert(m.clone())).collect()
}

#[cfg(test)]
pub mod tests {
    use super::*;

    #[test]
    fn lines_become_model_paths() {
        let text = "\u{feff}# kept props\r\nmodels/Props/Chair.MDL\r\n\"models\\props\\table.mdl\",\r\n\r\n\
                    C:\\Games\\garrysmod\\addons\\omni\\models\\wissem\\007fl\\pm\\bond.dx90.vtx\r\n\
                    models/props/chair.mdl // twice\r\nprops/lamp\r\nmaterials/props/chair.vmt\r\n; comment\r\nmodels/../x.mdl\r\n";
        assert_eq!(
            parse(text),
            [
                "models/props/chair.mdl",
                "models/props/table.mdl",
                "models/wissem/007fl/pm/bond.mdl",
                "props/lamp.mdl"
            ]
        );
    }

    #[test]
    fn rubbish_is_skipped_not_fatal() {
        assert!(parse("").is_empty());
        assert!(parse("\n\n   \n#\n").is_empty());
        assert!(normalise("models/").is_none());
        assert!(normalise("models/a/.mdl").is_none());
        assert!(normalise("C:/models/a.mdl").is_some()); // the drive is dropped with everything before models/
    }
}
