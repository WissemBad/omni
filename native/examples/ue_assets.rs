use omni_native::unreal::*;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    oodle::set_library(&args[2]).unwrap();
    let g = Game::open(std::path::Path::new(&args[1]), "5.1", None).unwrap();
    let schema = reflect::Schema::from_text(&std::fs::read_to_string("C:/Users/Wissem/Downloads/refs/bbt_mappings.txt").unwrap()).unwrap();
    let out = std::path::Path::new("C:/Users/Wissem/Downloads/refs/out"); std::fs::create_dir_all(out).unwrap();
    let mut fmts = std::collections::HashMap::<String, (usize, usize)>::new();
    let mut sfmts = std::collections::HashMap::<String, usize>::new();
    let mut fails = 0; let mut sfails = 0; let mut pngs = 0;
    let mut paths: Vec<_> = g.packages.keys().cloned().collect(); paths.sort();
    let t = std::time::Instant::now();
    for p in &paths {
        let Ok(pkg) = g.package(p) else { continue };
        for (i, e) in pkg.exports.iter().enumerate() {
            if e.flags & 0x10 != 0 { continue; }
            let cls = g.class_name(&pkg, e.class);
            let ctx = props::Ctx { schema: &schema, names: &pkg.names, ue5: g.ue5_version };
            if cls == "Texture2D" {
                match assets::read_texture(&g, &ctx, &pkg, i, &cls) {
                    Ok(tx) => {
                        let have = tx.mips.iter().filter(|m| m.data.is_some()).count();
                        let ent = fmts.entry(tx.format.clone()).or_default(); ent.0 += 1; if have == 0 { ent.1 += 1; }
                        if pngs < 12 && have > 0 && (pngs < 4 || !tx.format.contains("DXT")) {
                            let m = tx.mips.iter().find(|m| m.data.is_some()).unwrap();
                            match assets::convert_mip(&tx.format, m.width, m.height, m.data.as_ref().unwrap()).and_then(|d| omni_native::texture::to_rgba(assets::omni_format(&tx.format), m.width, m.height, &d).map_err(|e| reader::Error(e))) {
                                Ok(rgba) => { let png = omni_native::vtf::encode_png(&rgba, m.width as usize, m.height as usize); let name = p.replace('/', "_"); std::fs::write(out.join(format!("{name}.png")), png).unwrap(); pngs += 1; println!("png {p} {} {}x{} mips {}/{}", tx.format, m.width, m.height, have, tx.mips.len()); }
                                Err(e) => println!("convert {p} {}: {e}", tx.format),
                            }
                        }
                    }
                    Err(er) => { fails += 1; if fails <= 5 { println!("TEX FAIL {p}: {er}"); } }
                }
            } else if cls == "SoundWave" {
                match assets::read_sound(&g, &ctx, &pkg, i, &cls) {
                    Ok(s) => { *sfmts.entry(s.format.clone()).or_default() += 1; if sfmts[&s.format] == 1 { println!("sound {p} {} {} bytes head {:02x?}", s.format, s.data.len(), &s.data[..16.min(s.data.len())]); std::fs::write(out.join(format!("snd_{}.bin", s.format)), &s.data).unwrap(); } }
                    Err(er) => { sfails += 1; if sfails <= 5 { println!("SND FAIL {p}: {er}"); } }
                }
            }
        }
    }
    println!("textures {fmts:?} fails {fails}\nsounds {sfmts:?} fails {sfails}\n{:?}", t.elapsed());
}
