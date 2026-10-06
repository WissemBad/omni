fn main() {
    let a: Vec<String> = std::env::args().collect();
    let d = std::fs::read(&a[1]).unwrap();
    let t = std::time::Instant::now();
    let r = omni_native::binka::decode(&d).unwrap();
    eprintln!("{} ch {} Hz {} samples in {:?}", r.channels, r.sample_rate, r.pcm.len() / r.channels as usize, t.elapsed());
    let mut w = Vec::new();
    let n = r.pcm.len() * 2;
    w.extend_from_slice(b"RIFF"); w.extend_from_slice(&(36 + n as u32).to_le_bytes()); w.extend_from_slice(b"WAVEfmt ");
    w.extend_from_slice(&16u32.to_le_bytes()); w.extend_from_slice(&1u16.to_le_bytes()); w.extend_from_slice(&r.channels.to_le_bytes());
    w.extend_from_slice(&r.sample_rate.to_le_bytes()); w.extend_from_slice(&(r.sample_rate * 2 * r.channels as u32).to_le_bytes());
    w.extend_from_slice(&(2 * r.channels).to_le_bytes()); w.extend_from_slice(&16u16.to_le_bytes()); w.extend_from_slice(b"data"); w.extend_from_slice(&(n as u32).to_le_bytes());
    for s in &r.pcm { w.extend_from_slice(&s.to_le_bytes()); }
    std::fs::write(&a[2], w).unwrap();
}
