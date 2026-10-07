import type { OutputCheck, OutputModel } from '~/utils/types'

/** Folder the viewer is browsing ('' = what omni converted); the viewer page keeps it current. */
export const viewerRoot = ref('')

/** Query string of an `/api/<source>/output/<route>` URL. */
export function outputUrl(
  sid: string,
  route: string,
  params: Record<string, string | number> = {},
): string {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
  )
  if (viewerRoot.value) q.set('root', viewerRoot.value)
  return `/api/${sid}/output/${route}?${q}`
}

/** Opens the file in Windows Explorer (the API runs on the user's machine). */
export async function reveal(sid: string, path: string) {
  try {
    await api(`/${sid}/output/reveal?path=${encodeURIComponent(path)}&root=${viewerRoot.value}`, {
      method: 'POST',
    })
  } catch (e) {
    useToast().add({
      title: 'Impossible d’ouvrir le dossier',
      description: apiError(e),
      color: 'error',
    })
  }
}

export function timeAgo(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'à l’instant'
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`
  const d = Math.floor(s / 86400)
  return d < 30 ? `il y a ${d} j` : new Date(ts * 1000).toLocaleDateString('fr-FR')
}

export const VERDICT = {
  ok: { label: 'Prêt pour GMod', color: 'success', icon: 'i-ri-shield-check-line' },
  warn: { label: 'À vérifier', color: 'warning', icon: 'i-ri-alert-line' },
  error: { label: 'Problème détecté', color: 'error', icon: 'i-ri-error-warning-line' },
} as const

export const CHECK_STYLE: Record<OutputCheck['level'], { icon: string; color: string }> = {
  ok: { icon: 'i-ri-checkbox-circle-fill', color: 'text-success' },
  info: { icon: 'i-ri-information-fill', color: 'text-info' },
  warn: { icon: 'i-ri-alert-fill', color: 'text-warning' },
  error: { icon: 'i-ri-close-circle-fill', color: 'text-error' },
}

export const HITGROUP = [
  'générique',
  'tête',
  'torse',
  'ventre',
  'bras gauche',
  'bras droit',
  'jambe gauche',
  'jambe droite',
  'équipement',
]

/** Short human name of an output model (a character's title, else the file name). */
export function outputTitle(m: { title?: string; name: string; kind?: string }): string {
  return m.title || titleCase(m.kind === 'model' ? m.name : m.name.replace(/_[0-9a-f]{6}$/, ''))
}

/** Dimensions in cm in the order the props page uses: depth x width x height (Source axes). */
export function dimsCm(m: Pick<OutputModel, 'extent'>): string | null {
  if (!m.extent) return null
  return `${m.extent.map((v) => Math.round((v / 39.37) * 100)).join(' × ')} cm`
}

/** GMod console command that puts a converted player model on the local player. */
export const modelCommand = (model: string) => `lua_run Entity(1):SetModel("${model}")`

/** Name of a bodygroup option: the SMD name without the `G00_00_` ordering prefix the build adds. */
export const optionLabel = (name: string) => titleCase(name.replace(/^g\d+_\d+_/i, ''))
