//! SkeletalMesh render data (cooked UE 5.0 - 5.x): reference skeleton, LOD sections with their bone maps,
//! vertex buffers and skin weights. Shares the buffer readers of mesh.rs.

use super::assets::bulk_header;
use super::mesh::{bulk_array, color_buffer, strip, vertex_buffers, Bone, Lod, Mesh, Section};
use super::props::{self, Ctx};
use super::reader::{err, Reader, Result};
use super::zen::Package;
use super::Game;

fn skip_fixed(r: &mut Reader, elem: usize) -> Result<()> {
    let n = r.count(elem)?;
    r.skip(n * elem)
}

fn multisize_indices(r: &mut Reader) -> Result<Vec<u32>> {
    let size = r.u8()?;
    let (es, _cnt, data) = bulk_array(r)?;
    Ok(if size == 2 || es == 2 {
        data.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]]) as u32).collect()
    } else {
        data.chunks_exact(4).map(|c| u32::from_le_bytes(c.try_into().unwrap())).collect()
    })
}

/// FSkinWeightVertexBuffer (UE 4.25+ layout): per vertex bone indices (section bone map) then weights.
fn skin_weights(r: &mut Reader, lod: &mut Lod, ue5: i32) -> Result<()> {
    strip(r)?;
    let variable = r.u32()? != 0;
    let max_inf = r.u32()? as usize;
    let _num_bones = r.u32()?;
    let nverts = r.u32()? as usize;
    let idx16 = r.u32()? != 0;
    let w16 = if ue5 >= 1009 { r.u32()? != 0 } else { false };
    let (_es, _cnt, data) = bulk_array(r)?;
    strip(r)?;
    let _nlookup = r.i32()?;
    let (_les, lcnt, ldata) = bulk_array(r)?;
    if max_inf == 0 || max_inf > 16 {
        return err(format!("skin weights with {max_inf} influences"));
    }
    let keep = max_inf.min(8);
    lod.influences = keep;
    lod.bone_indices = vec![0; nverts * keep];
    lod.bone_weights = vec![0; nverts * keep];
    let ib = if idx16 { 2 } else { 1 };
    let wb = if w16 { 2 } else { 1 };
    for v in 0..nverts {
        let (start, count) = if variable {
            if v >= lcnt {
                break;
            }
            let l = u32::from_le_bytes(ldata[v * 4..v * 4 + 4].try_into().unwrap());
            ((l >> 8) as usize, (l & 0xFF) as usize)
        } else {
            (v * max_inf * (ib + wb), max_inf)
        };
        let wstart = start + count * ib;
        for k in 0..count.min(keep) {
            let bi = if idx16 {
                data.get(start + k * 2..start + k * 2 + 2).map(|b| u16::from_le_bytes([b[0], b[1]])).unwrap_or(0)
            } else {
                data.get(start + k).copied().unwrap_or(0) as u16
            };
            let w = if w16 {
                data.get(wstart + k * 2..wstart + k * 2 + 2).map(|b| (u16::from_le_bytes([b[0], b[1]]) >> 8) as u8).unwrap_or(0)
            } else {
                data.get(wstart + k).copied().unwrap_or(0)
            };
            lod.bone_indices[v * keep + k] = bi;
            lod.bone_weights[v * keep + k] = w;
        }
    }
    Ok(())
}

fn streamed(r: &mut Reader, lod: &mut Lod, has_colors: bool, has_cloth: bool, ue5: i32) -> Result<()> {
    strip(r)?;
    lod.indices = multisize_indices(r)?;
    vertex_buffers(r, lod)?;
    skin_weights(r, lod, ue5)?;
    if has_colors {
        color_buffer(r, lod)?;
    }
    if has_cloth {
        strip(r)?;
        bulk_array(r)?;
        skip_fixed(r, 12)?;
    }
    // skin weight profiles: name -> {bone ids, bone weights, u8 count, map u32 -> u32}
    let n = r.count(8)?;
    for _ in 0..n {
        r.skip(8)?;
        skip_fixed(r, 1)?;
        skip_fixed(r, 1)?;
        r.u8()?;
        skip_fixed(r, 8)?;
    }
    if ue5 >= 1016 {
        r.skip(24)?;
        bulk_array(r)?;
    } else {
        skip_fixed(r, 1)?; // ray tracing data
    }
    if r.u32()? != 0 {
        // compressed morph targets
        let n = r.count(4)?;
        r.skip(n * 4)?;
        skip_fixed(r, 16)?;
        skip_fixed(r, 16)?;
        skip_fixed(r, 4)?;
        skip_fixed(r, 4)?;
        r.skip(12)?;
    }
    if ue5 >= 1010 && r.count(8)? > 0 {
        return err("skeletal vertex attributes are not supported");
    }
    if ue5 >= 1012 {
        let (_g, c) = strip(r)?;
        if c & 1 == 0 {
            return err("skeletal half-edge data is not supported");
        }
    }
    Ok(())
}

fn lod(game: &Game, pkg: &Package, pid: u64, r: &mut Reader, has_colors: bool, want: bool) -> Result<Lod> {
    let ue5 = game.ue5_version;
    strip(r)?;
    let cooked_out = r.u32()? != 0;
    let inlined = r.u32()? != 0;
    skip_fixed(r, 2)?; // required bones
    let mut lod = Lod::default();
    if cooked_out {
        return Ok(lod);
    }
    let nsec = r.count(16)?;
    let mut has_cloth = false;
    for _ in 0..nsec {
        let (_sg, scls) = strip(r)?;
        let material = r.i16()? as i32;
        let first_index = r.u32()?;
        let num_triangles = r.u32()?;
        r.u32()?; // recompute tangent
        r.u8()?; // recompute tangent vertex mask channel
        r.u32()?; // cast shadow
        r.u32()?; // visible in ray tracing
        let base_vertex = r.u32()?;
        let nlods = r.count(4)?;
        for _ in 0..nlods {
            let n = r.count(64)?;
            if n > 0 {
                has_cloth = true;
            }
            r.skip(n * 64)?;
        }
        let nb = r.count(2)?;
        let bone_map: Vec<u16> = r.n_of(nb, 2, |r| r.u16())?;
        let num_vertices = r.u32()?;
        r.i32()?; // max bone influences
        r.i16()?; // cloth asset index
        r.skip(20)?; // clothing data
        if scls & 1 == 0 {
            skip_fixed(r, 4)?;
            skip_fixed(r, 8)?;
        }
        r.u32()?; // disabled
        lod.sections.push(Section { material, first_index, num_triangles, bone_map, base_vertex, num_vertices, ..Default::default() });
    }
    skip_fixed(r, 2)?; // active bone indices
    r.u32()?; // buffers size
    if inlined {
        streamed(r, &mut lod, has_colors, has_cloth, ue5)?;
    } else {
        let b = bulk_header(pkg, r)?;
        if want {
            if let Some(data) = game.bulk_bytes(pkg, pid, &b)? {
                let mut br = Reader::new(&data);
                streamed(&mut br, &mut lod, has_colors, has_cloth, ue5)?;
            }
        }
        if b.size > 0 {
            // availability info: metadata of the streamed buffers
            let mut skip = 1 + 4 + 16 + 8 + 8 + 4 * 4 + 4;
            if ue5 >= 1009 {
                skip += 4;
            }
            r.skip(skip)?;
            if has_cloth {
                let n = r.count(12)?;
                r.skip(n * 12 + 8)?;
            }
            let n = r.count(8)?;
            r.skip(n * 8)?;
            if ue5 >= 1016 {
                r.skip(24)?;
            }
        }
    }
    Ok(lod)
}

pub fn read_skeletal_mesh(game: &Game, ctx: &Ctx, pkg: &Package, index: usize, class: &str, max_lods: usize) -> Result<Mesh> {
    let pid = game.package_id_of(&pkg.name).unwrap_or(0);
    let ue5 = game.ue5_version;
    let e = &pkg.exports[index];
    let (props, off) = props::read_object(ctx, class, pkg.export_data(index), e.flags & 0x10 != 0)?;
    let has_colors = props::get(&props, "bHasVertexColors").and_then(|v| v.as_i64()) == Some(1);
    let mut r = Reader::at(&pkg.data, e.start + off);
    strip(&mut r)?;
    r.skip(if ue5 >= 1004 { 56 } else { 28 })?; // imported bounds
    let nmat = r.count(12)?;
    let mut materials = Vec::with_capacity(nmat);
    for _ in 0..nmat {
        materials.push(r.i32()?);
        ctx.fname(&mut r)?;
        if r.u32()? != 0 {
            ctx.fname(&mut r)?;
        }
        r.skip(24)?; // UV channel data
        if ue5 >= 1016 {
            r.i32()?; // overlay material
        }
    }
    let nb = r.count(12)?;
    let mut bones = Vec::with_capacity(nb);
    for _ in 0..nb {
        let name = ctx.fname(&mut r)?;
        let parent = r.i32()?;
        bones.push(Bone { name, parent, ..Default::default() });
    }
    let np = r.count(40)?;
    for i in 0..np {
        let mut f = [0f64; 10];
        for v in f.iter_mut() {
            *v = if ue5 >= 1004 { r.f64()? } else { r.f32()? as f64 };
        }
        if let Some(b) = bones.get_mut(i) {
            b.rotation = [f[0], f[1], f[2], f[3]];
            b.translation = [f[4], f[5], f[6]];
            b.scale = [f[7], f[8], f[9]];
        }
    }
    let nm = r.count(12)?;
    r.skip(nm * 12)?;
    if r.u32()? == 0 {
        return err("skeletal mesh is not cooked");
    }
    let nlods = r.count(8)?;
    let mut lods = Vec::new();
    for i in 0..nlods {
        lods.push(lod(game, pkg, pid, &mut r, has_colors, i < max_lods)?);
    }
    Ok(Mesh { lods, bones, materials, props })
}
