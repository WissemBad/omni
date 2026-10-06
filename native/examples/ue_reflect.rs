use omni_native::unreal::reflect::*;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let t = std::time::Instant::now();
    let data = std::fs::read(&args[1]).unwrap();
    let s = extract(&data).unwrap();
    println!("{} structs, {} enums in {:?}", s.structs.len(), s.enums.len(), t.elapsed());
    for n in args[2..].iter() {
        match s.structs.get(n) {
            Some(d) => { println!("{} : {:?} ({} own)", d.name, d.super_name, d.own_count()); for p in &d.props { println!("   [{}] {} {}", p.array_dim, p.name, ty_text(&p.ty)); } }
            None => println!("{n}: missing"),
        }
    }
    std::fs::write("C:/Users/Wissem/Downloads/refs/bbt_mappings.txt", s.to_text()).unwrap();
    let unk = s.structs.values().flat_map(|d| d.props.iter()).filter(|p| ty_text(&p.ty).contains('?') || ty_text(&p.ty).contains("Unknown")).count();
    println!("unknown-typed props: {unk}");
}
