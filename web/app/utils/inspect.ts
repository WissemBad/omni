import type { TexEntry } from '~/components/OTextureViewer.vue'
import type { Channel } from '~/utils/textures'
import type { OutputMaterial, SourceMaterial } from '~/utils/types'

/** Texture-viewer entries of game materials (props, characters, origin of a converted model). */
export function gameEntries(sid: string, materials: SourceMaterial[]): TexEntry[] {
  return materials.flatMap((m) =>
    m.textures
      .filter((t) => t.found !== false)
      .map((t) => ({
        id: `${m.key}:${t.slot}:${t.key}`,
        label: texRoleLabel(t.role),
        name: t.name || `${m.name} · ${t.slot}`,
        material: m.name,
        width: t.width,
        height: t.height,
        src: (channel: Channel, size: number) =>
          gameTextureUrl(sid, t.key, { size, channel, role: t.role }),
        info: [
          ['Dimensions', dimsLabel(t.width, t.height)],
          ['Format du jeu', t.format ?? '—'],
          ['Conversion', FORMAT_HINT[t.format ?? ''] ?? '—'],
          ['Emplacement', t.slot],
          ['Matériau', m.name],
          ['Clé', t.key],
        ] as [string, string][],
        gameKey: t.key,
        download: `/api/${sid}/textures/${t.key}/download`,
      })),
  )
}

/** Index of a material's texture in `gameEntries`. */
export function gameEntryIndex(materials: SourceMaterial[], mi: number, ti: number): number {
  let n = 0
  for (const [i, m] of materials.entries()) {
    const visible = m.textures.filter((t) => t.found !== false)
    if (i === mi) return n + Math.max(0, visible.indexOf(m.textures[ti]!))
    n += visible.length
  }
  return 0
}

/** Texture-viewer entries of converted (VTF) materials, with the UV layout of the model. */
export function outputEntries(
  sid: string,
  model: string,
  materials: OutputMaterial[],
  slotOf: (mi: number) => number | null,
): TexEntry[] {
  return materials.flatMap((mat, mi) =>
    mat.textures
      .filter((t) => t.exists)
      .map((t) => {
        const slot = slotOf(mi)
        return {
          id: `${mat.name}:${t.param}:${t.path}`,
          label: t.label,
          name: t.name,
          material: mat.name,
          width: t.width,
          height: t.height,
          src: (channel: Channel, size: number) =>
            outputUrl(sid, 'texture', { path: t.path, channel, size: Math.min(size, 4096) }),
          info: [
            ['Dimensions', dimsLabel(t.width, t.height)],
            ['Format', t.format ?? '—'],
            ['Niveaux de mip', t.mips ?? '—'],
            ['Poids', t.bytes ? fmtBytes(t.bytes) : '—'],
            ['Paramètre', t.param],
            ['Matériau', mat.name],
          ] as [string, string | number][],
          flags: t.flags,
          uv:
            slot !== null ? outputUrl(sid, 'uv', { path: model, skinref: slot, size: 2048 }) : null,
          path: t.path,
          gameKey: mat.origin?.textures.find((o) => o.role === texRoleOf(t.param))?.key,
        }
      }),
  )
}

const PARAM_ROLE: Record<string, string> = {
  $basetexture: 'base',
  $bumpmap: 'normal',
  $normalmap: 'normal',
  $selfillummask: 'emissive',
}
const texRoleOf = (param: string) => PARAM_ROLE[param] ?? ''

/** Origin game materials of converted materials (viewer "origin" view). */
export function originMaterials(materials: OutputMaterial[]): SourceMaterial[] {
  const seen = new Set<string>()
  const out: SourceMaterial[] = []
  for (const m of materials) {
    const o = m.origin
    if (!o || seen.has(o.key)) continue
    seen.add(o.key)
    out.push({
      key: o.key,
      name: o.name,
      source_name: o.source,
      class: o.cls,
      params: {},
      textures: o.textures.map((t) => ({
        role: t.role,
        slot: t.slot,
        key: t.key,
        width: t.width,
        height: t.height,
        format: t.fmt,
        name: t.name,
        found: true,
      })),
    })
  }
  return out
}
