use omni_native::unreal::*;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    oodle::set_library(&args[2]).unwrap();
    let g = Game::open(std::path::Path::new(&args[1]), "5.1", None).unwrap();
    let schema = reflect::Schema::from_text(&std::fs::read_to_string("C:/Users/Wissem/Downloads/refs/bbt_mappings.txt").unwrap()).unwrap();
    let want: Vec<&str> = args[3].split(',').collect();
    let mut stats = std::collections::HashMap::<String, (usize, usize)>::new();
    let mut shown = 0;
    let mut paths: Vec<_> = g.packages.keys().cloned().collect(); paths.sort();
    for p in &paths {
        let Ok(pkg) = g.package(p) else { continue };
        for (i, e) in pkg.exports.iter().enumerate() {
            let cls = g.class_name(&pkg, e.class);
            if !want.contains(&cls.as_str()) { continue; }
            let ctx = props::Ctx { schema: &schema, names: &pkg.names, ue5: g.ue5_version };
            let data = pkg.export_data(i);
            let st = stats.entry(cls.clone()).or_default();
            match props::read_object(&ctx, &cls, data, e.flags & 0x10 != 0) {
                Ok((pr, off)) => { st.0 += 1; if shown < 6 && e.flags & 0x10 == 0 { shown += 1; println!("{p} {cls}: {} props, native {} of {} bytes", pr.len(), data.len() - off, data.len()); for (n, v) in pr.iter().take(14) { println!("   {n} = {:.120}", format!("{v:?}")); } } }
                Err(er) => { st.1 += 1; if st.1 <= 3 { println!("FAIL {p} {cls}: {er}"); } }
            }
        }
    }
    println!("{stats:?}");
}
