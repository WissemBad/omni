use omni_native::unreal::*;
use std::io::Write;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    oodle::set_library(&args[2]).unwrap();
    let g = Game::open(std::path::Path::new(&args[1]), "5.1", None).unwrap();
    let schema = reflect::Schema::from_text(&std::fs::read_to_string("C:/Users/Wissem/Downloads/refs/bbt_mappings.txt").unwrap()).unwrap();
    let mut ok = 0; let mut fails = 0; let mut empty = 0; let mut objs = 0;
    let mut paths: Vec<_> = g.packages.keys().cloned().collect(); paths.sort();
    let filter = args.get(3).cloned().unwrap_or_default();
    for p in &paths {
        let Ok(pkg) = g.package(p) else { continue };
        for (i, e) in pkg.exports.iter().enumerate() {
            if e.flags & 0x10 != 0 { continue; }
            let cls = g.class_name(&pkg, e.class);
            if cls != "StaticMesh" { continue; }
            let ctx = props::Ctx { schema: &schema, names: &pkg.names, ue5: g.ue5_version };
            match mesh::read_static_mesh(&g, &ctx, &pkg, i, &cls, 1) {
                Ok(m) => {
                    ok += 1;
                    let l = &m.lods[0];
                    if l.positions.is_empty() { empty += 1; }
                    if objs < 3 && p.contains(&filter) && !l.positions.is_empty() {
                        objs += 1;
                        println!("{p}: {} lods, lod0 {} verts {} tris {} sections uv {} col {}", m.lods.len(), l.positions.len()/3, l.indices.len()/3, l.sections.len(), l.uvs.len(), l.colors.len());
                        let name = p.replace('/', "_");
                        let mut f = std::fs::File::create(format!("C:/Users/Wissem/Downloads/refs/out/{name}.obj")).unwrap();
                        for v in l.positions.chunks(3) { writeln!(f, "v {} {} {}", v[0], v[2], v[1]).unwrap(); }
                        for t in l.indices.chunks(3) { writeln!(f, "f {} {} {}", t[0]+1, t[1]+1, t[2]+1).unwrap(); }
                    }
                }
                Err(er) => { fails += 1; if fails <= 8 { println!("FAIL {p}: {er}"); } }
            }
        }
    }
    println!("ok {ok} (empty lod0 {empty}) fail {fails}");
}
