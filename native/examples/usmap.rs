fn main() {
    let a: Vec<String> = std::env::args().collect();
    let s = omni_native::unreal::reflect::from_usmap(&std::fs::read(&a[1]).unwrap()).unwrap();
    println!("{} structs {} enums", s.structs.len(), s.enums.len());
    for n in a[2..].iter() { if let Some(d) = s.structs.get(n) { println!("{} : {:?} {}", d.name, d.super_name, d.props.iter().map(|p| format!("{}:{}", p.name, omni_native::unreal::reflect::ty_text(&p.ty))).collect::<Vec<_>>().join(", ")); } }
}
