//! StaticMesh and SkeletalMesh render data (cooked UE 5.0 - 5.x): LOD sections, vertex buffers (positions,
//! packed tangent basis, UVs, colours), index buffers, skin weights and reference skeleton.
//!
//! Output geometry stays in Unreal's frame (centimetres, Z up, left-handed); the Python side converts it.

use super::assets::{bulk_header, flags};
use super::props::{self, Ctx, Props};
use super::reader::{err, Error, Reader, Result};
use super::zen::Package;
use super::Game;

#[derive(Clone, Debug, Default)]
pub struct Section {
    pub material: i32,
    pub first_index: u32,
    pub num_triangles: u32,
    pub min_vertex: u32,
    pub max_vertex: u32,
    /// skeletal sections: section bone index -> skeleton bone
    pub bone_map: Vec<u16>,
    pub base_vertex: u32,
    pub num_vertices: u32,
}

#[derive(Clone, Debug, Default)]
pub struct Lod {
    pub sections: Vec<Section>,
    pub positions: Vec<f32>,
    pub normals: Vec<f32>,
    pub tangents: Vec<f32>,
    pub uvs: Vec<Vec<f32>>,
    pub colors: Vec<u8>,
    pub indices: Vec<u32>,
    /// skeletal: per vertex up to 8 (bone index in section map, weight 0-255)
    pub influences: usize,
    pub bone_indices: Vec<u16>,
    pub bone_weights: Vec<u8>,
}

#[derive(Clone, Debug, Default)]
pub struct Bone {
    pub name: String,
    pub parent: i32,
    /// rotation quaternion (x, y, z, w), translation, scale
    pub rotation: [f64; 4],
    pub translation: [f64; 3],
    pub scale: [f64; 3],
}

pub struct Mesh {
    pub lods: Vec<Lod>,
    pub bones: Vec<Bone>,
    pub props: Props,
}

pub(crate) fn strip(r: &mut Reader) -> Result<(u8, u8)> {
    Ok((r.u8()?, r.u8()?))
}

/// TArray bulk serialisation: i32 element size, i32 count, data.
pub(crate) fn bulk_array<'a>(r: &mut Reader<'a>) -> Result<(usize, usize, &'a [u8])> {
    let size = r.i32()?;
    let count = r.i32()?;
    if size < 0 || count < 0 {
        return err("negative bulk array");
    }
    let n = (size as usize).checked_mul(count as usize).ok_or(Error("bulk array size".into()))?;
    Ok((size as usize, count as usize, r.bytes(n)?))
}

fn skip_sampler(r: &mut Reader) -> Result<()> {
    let n = r.count(4)?;
    r.skip(n * 4)?;
    let n = r.count(4)?;
    r.skip(n * 4)?;
    r.f32()?;
    Ok(())
}

fn half(h: u16) -> f32 {
    let s = if h & 0x8000 != 0 { -1.0 } else { 1.0 };
    let e = ((h >> 10) & 0x1F) as i32;
    let m = (h & 0x3FF) as f32;
    s * if e == 0 { m * 2f32.powi(-24) } else if e == 31 { f32::INFINITY } else { (1.0 + m / 1024.0) * 2f32.powi(e - 15) }
}

fn packed8(v: u32) -> [f32; 4] {
    let b = v.to_le_bytes();
    [b[0] as i8 as f32 / 127.0, b[1] as i8 as f32 / 127.0, b[2] as i8 as f32 / 127.0, b[3] as i8 as f32 / 127.0]
}

/// Position, tangent and UV buffers (FPositionVertexBuffer, FStaticMeshVertexBuffer).
pub(crate) fn vertex_buffers(r: &mut Reader, lod: &mut Lod) -> Result<()> {
    // positions
    let _stride = r.i32()?;
    let nverts = r.i32()?.max(0) as usize;
    let (es, cnt, data) = bulk_array(r)?;
    if es == 12 {
        lod.positions = data.chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect();
    } else if es == 24 {
        lod.positions = data.chunks_exact(8).map(|c| f64::from_le_bytes(c.try_into().unwrap()) as f32).collect();
    } else if cnt > 0 {
        return err(format!("position element size {es}"));
    }
    // tangents + UVs
    let (_g, cls) = strip(r)?;
    let _ = cls;
    let ntex = r.i32()?.max(0) as usize;
    let nv = r.i32()?.max(0) as usize;
    let full_uv = r.u32()? != 0;
    let high_tan = r.u32()? != 0;
    if nv != nverts && nverts != 0 {
        return err(format!("vertex count mismatch {nv} vs {nverts}"));
    }
    let (tes, tcnt, tdata) = bulk_array(r)?;
    if tcnt != nv && tcnt != 0 {
        return err("tangent count mismatch");
    }
    lod.normals = Vec::with_capacity(nv * 3);
    lod.tangents = Vec::with_capacity(nv * 4);
    for i in 0..tcnt {
        let v = &tdata[i * tes..(i + 1) * tes];
        let (t, n) = if high_tan {
            let c = |k: usize| i16::from_le_bytes([v[k * 2], v[k * 2 + 1]]) as f32 / 32767.0;
            ([c(0), c(1), c(2), c(3)], [c(4), c(5), c(6), c(7)])
        } else {
            (packed8(u32::from_le_bytes(v[0..4].try_into().unwrap())), packed8(u32::from_le_bytes(v[4..8].try_into().unwrap())))
        };
        lod.tangents.extend_from_slice(&[t[0], t[1], t[2], if n[3] < 0.0 { -1.0 } else { 1.0 }]);
        lod.normals.extend_from_slice(&[n[0], n[1], n[2]]);
    }
    let (ues, ucnt, udata) = bulk_array(r)?;
    if ntex > 0 && ucnt > 0 {
        let per = ues * ucnt / (nv.max(1) * ntex).max(1);
        let _ = per;
        lod.uvs = vec![Vec::with_capacity(nv * 2); ntex];
        let item = if full_uv { 8 } else { 4 };
        for vi in 0..nv {
            for t in 0..ntex {
                let o = (vi * ntex + t) * item;
                let Some(b) = udata.get(o..o + item) else { break };
                let (u, v) = if full_uv {
                    (f32::from_le_bytes(b[0..4].try_into().unwrap()), f32::from_le_bytes(b[4..8].try_into().unwrap()))
                } else {
                    (half(u16::from_le_bytes([b[0], b[1]])), half(u16::from_le_bytes([b[2], b[3]])))
                };
                lod.uvs[t].push(u);
                lod.uvs[t].push(v);
            }
        }
    }
    Ok(())
}

pub(crate) fn color_buffer(r: &mut Reader, lod: &mut Lod) -> Result<()> {
    let _strip = strip(r)?;
    let _stride = r.i32()?;
    let n = r.i32()?;
    if n > 0 {
        let (es, cnt, data) = bulk_array(r)?;
        if es == 4 {
            lod.colors = data.chunks_exact(4).flat_map(|c| [c[2], c[1], c[0], c[3]]).collect();
        }
        let _ = cnt;
    }
    Ok(())
}

fn index_buffer(r: &mut Reader, ue5: i32) -> Result<Vec<u32>> {
    let is32 = r.u32()? != 0;
    let (_es, _cnt, data) = bulk_array(r)?;
    let _ = ue5;
    r.u32()?; // bShouldExpandTo32Bit (4.25+)
    Ok(if is32 {
        data.chunks_exact(4).map(|c| u32::from_le_bytes(c.try_into().unwrap())).collect()
    } else {
        data.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]]) as u32).collect()
    })
}

fn static_buffers(r: &mut Reader, lod: &mut Lod, nsections: usize, ue5: i32) -> Result<()> {
    let (_g, cls) = strip(r)?;
    vertex_buffers(r, lod)?;
    color_buffer(r, lod)?;
    lod.indices = index_buffer(r, ue5)?;
    if cls & 4 == 0 {
        index_buffer(r, ue5)?; // reversed
    }
    index_buffer(r, ue5)?; // depth only
    if cls & 4 == 0 {
        index_buffer(r, ue5)?; // reversed depth only
    }
    if ue5 < 1004 && cls & 1 == 0 {
        index_buffer(r, ue5)?; // adjacency (removed with tessellation in 5.0)
    }
    if cls & 8 == 0 {
        if ue5 >= 1016 {
            r.skip(24)?;
        }
        bulk_array(r)?; // ray tracing geometry
    }
    for _ in 0..nsections {
        skip_sampler(r)?;
    }
    skip_sampler(r)?;
    Ok(())
}

fn static_lod(game: &Game, pkg: &Package, pid: u64, r: &mut Reader, want_data: bool) -> Result<Lod> {
    let ue5 = game.ue5_version;
    let (_g, _cls) = strip(r)?;
    let nsec = r.count(28)?;
    let mut lod = Lod::default();
    for _ in 0..nsec {
        let s = Section {
            material: r.i32()?,
            first_index: r.u32()?,
            num_triangles: r.u32()?,
            min_vertex: r.u32()?,
            max_vertex: r.u32()?,
            ..Default::default()
        };
        r.skip(4 * 4)?; // collision, cast shadow, force opaque, visible in ray tracing
        if ue5 >= 1008 {
            r.skip(4)?; // affect distance field lighting (5.1+)
        }
        lod.sections.push(s);
    }
    if ue5 >= 1016 {
        r.skip(56)?; // source mesh bounds (5.6+)
    }
    r.f32()?; // max deviation
    let cooked_out = r.u32()? != 0;
    let inlined = r.u32()? != 0;
    if !cooked_out {
        if ue5 >= 1013 {
            r.skip(4)?; // bHasRayTracingGeometry (5.5+)
        }
        if inlined {
            static_buffers(r, &mut lod, nsec, ue5)?;
        } else {
            let b = bulk_header(pkg, r)?;
            if want_data {
                if let Some(data) = game.bulk_bytes(pkg, pid, &b)? {
                    let mut br = Reader::new(&data);
                    static_buffers(&mut br, &mut lod, nsec, ue5)?;
                }
            }
            r.skip(8 + 4 * 4 + 2 * 4 + 2 * 4 + 5 * 2 * 4)?;
            if ue5 >= 1016 {
                r.skip(24)?;
            }
        }
        r.skip(12)?; // buffer sizes
    }
    Ok(lod)
}

pub fn read_static_mesh(game: &Game, ctx: &Ctx, pkg: &Package, index: usize, class: &str, max_lods: usize) -> Result<Mesh> {
    let pid = game.package_id_of(&pkg.name).unwrap_or(0);
    let e = &pkg.exports[index];
    let (props, off) = props::read_object(ctx, class, pkg.export_data(index), e.flags & 0x10 != 0)?;
    let mut r = Reader::at(&pkg.data, e.start + off);
    strip(&mut r)?;
    let cooked = r.u32()? != 0;
    r.i32()?; // body setup
    r.i32()?; // nav collision
    r.skip(16)?; // lighting guid
    let n = r.count(4)?; // sockets
    r.skip(n * 4)?;
    if !cooked {
        return err("static mesh is not cooked");
    }
    let nlods = r.count(8)?;
    let mut lods = Vec::new();
    for i in 0..nlods {
        lods.push(static_lod(game, pkg, pid, &mut r, i < max_lods)?);
    }
    let _ = flags::UNUSED;
    Ok(Mesh { lods, bones: Vec::new(), props })
}
