use omni_native::unreal::*;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    oodle::set_library(&args[2]).unwrap();
    let g = Game::open(std::path::Path::new(&args[1]), "5.1", None).unwrap();
    let schema = reflect::Schema::from_text(&std::fs::read_to_string("C:/Users/Wissem/Downloads/refs/bbt_mappings.txt").unwrap()).unwrap();
    let (mut ok, mut fails) = (0, 0);
    let mut paths: Vec<_> = g.packages.keys().cloned().collect();
    paths.sort();
    for p in &paths {
        let Ok(pkg) = g.package(p) else { continue };
        for (i, e) in pkg.exports.iter().enumerate() {
            if e.flags & 0x10 != 0 {
                continue;
            }
            let cls = g.class_name(&pkg, e.class);
            if cls != "SkeletalMesh" {
                continue;
            }
            let ctx = props::Ctx { schema: &schema, names: &pkg.names, ue5: g.ue5_version };
            match skel::read_skeletal_mesh(&g, &ctx, &pkg, i, &cls, 1) {
                Ok(m) => {
                    ok += 1;
                    let l = &m.lods[0];
                    let w0: u32 = l.bone_weights.iter().take(l.influences).map(|&x| x as u32).sum();
                    println!("{p}: {} bones, {} lods, lod0 {} verts {} tris {} sections inf {} (w0 sum {w0})", m.bones.len(), m.lods.len(), l.positions.len() / 3, l.indices.len() / 3, l.sections.len(), l.influences);
                }
                Err(er) => {
                    fails += 1;
                    println!("FAIL {p}: {er}");
                }
            }
        }
    }
    println!("ok {ok} fail {fails}");
}
