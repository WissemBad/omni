use omni_native::unreal::*;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    oodle::set_library(&args[2]).unwrap();
    let t = std::time::Instant::now();
    let g = Game::open(std::path::Path::new(&args[1]), "5.1", None).unwrap();
    println!("opened {} containers in {:?}: {} packages, {} files, header v{}, script {}", g.containers.len(), t.elapsed(), g.packages.len(), g.files.len(), g.header_version, g.script.as_ref().map(|s| s.by_index.len()).unwrap_or(0));
    for c in &g.containers { println!("  {} v{} mount {} files {}", c.name, c.version, c.mount_point, c.files.len()); }
    let mut classes = std::collections::HashMap::<String, usize>::new();
    let mut n = 0; let mut fails = 0;
    let mut paths: Vec<_> = g.packages.keys().cloned().collect(); paths.sort();
    let lim: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(400);
    for p in paths.iter().take(lim) {
        match g.package(p) {
            Ok(pkg) => { n += 1; for e in &pkg.exports { *classes.entry(g.class_name(&pkg, e.class)).or_default() += 1; } }
            Err(e) => { fails += 1; if fails < 5 { println!("FAIL {p}: {e}"); } }
        }
    }
    let mut v: Vec<_> = classes.into_iter().collect(); v.sort_by(|a,b| b.1.cmp(&a.1));
    println!("{n} parsed, {fails} failed in {:?}", t.elapsed());
    for (c, k) in v.iter().take(40) { println!("  {k:6} {c}"); }
}
