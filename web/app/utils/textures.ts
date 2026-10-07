/** Game textures (before conversion): preview URLs and the labels every workbench shows. */

export type Channel = 'rgb' | 'rgba' | 'r' | 'g' | 'b' | 'a'

/** One texture the viewer can show: a game texture (before conversion) or a converted VTF. */
export interface TexEntry {
  id: string
  /** What the texture is for ("Couleur", "Normale"...). */
  label: string
  /** File / texture name. */
  name: string
  material?: string
  width?: number
  height?: number
  /** URL of the image for a channel and a size. */
  src: (channel: Channel, size: number) => string
  /** Facts shown in the side panel. */
  info: [string, string | number][]
  flags?: string[]
  /** UV layout of the model drawn over the texture, when it is known. */
  uv?: string | null
  /** Game texture key: link to the Textures workbench. */
  gameKey?: string
  /** Converted file path (copy / show in Explorer). */
  path?: string
  /** Full-resolution download URL. */
  download?: string
}

export const TEX_ROLE_LABEL: Record<string, string> = {
  base: 'Couleur',
  normal: 'Normale',
  detail_normal: 'Normale de détail',
  srm: 'SRM (spéculaire / rugosité / métal)',
  spec: 'Spéculaire',
  emissive: 'Émission',
  mask: 'Masque',
  alpha: 'Alpha',
  ao: 'Occlusion',
  height: 'Hauteur',
  detail: 'Détail',
  translucency: 'Translucidité',
  other: 'Autre',
}
export const texRoleLabel = (r: string) => TEX_ROLE_LABEL[r] ?? titleCase(r || 'autre')

/** Normal maps get their blue channel rebuilt (BC5 stores X and Y only). */
export const isNormalRole = (r?: string) => r === 'normal' || r === 'detail_normal'

/** PNG of a game texture at a given size and channel (`/api/<source>/textures/<key>/image`). */
export function gameTextureUrl(
  sid: string,
  key: string,
  opts: { size?: number; channel?: Channel; role?: string } = {},
): string {
  const q = new URLSearchParams({ size: String(opts.size ?? 256), channel: opts.channel ?? 'rgb' })
  if (opts.role !== undefined) q.set('normal', isNormalRole(opts.role) ? '1' : '0')
  return `/api/${sid}/textures/${key}/image?${q}`
}

export const FORMAT_HINT: Record<string, string> = {
  BC1: 'DXT1 · couleur sans alpha, copiée sans perte dans le VTF',
  BC3: 'DXT5 · couleur + alpha, copiée sans perte dans le VTF',
  BC2: 'DXT3 · copiée sans perte dans le VTF',
  BC4: 'un canal (masque), réencodée',
  BC5: 'normale X/Y, Z reconstruit puis réencodée',
  BC7: 'haute qualité, réencodée en DXT (Source ne lit pas BC7)',
  RGBA8: 'non compressée',
}

/** "1024 × 1024" */
export const dimsLabel = (w?: number, h?: number) => (w && h ? `${w} × ${h}` : '—')
