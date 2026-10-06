export const titleCase = (s: string) =>
  s
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/^./, (c) => c.toUpperCase())

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} o`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} Ko`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} Mo`
  return `${(n / 1024 ** 3).toFixed(1)} Go`
}

export function fmtDuration(seconds: number | string): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '—'
  if (s < 10) return `${s.toFixed(2)} s`
  const m = Math.floor(s / 60)
  return m ? `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}` : `${s.toFixed(1)} s`
}

/** Remaining time, rounded the way people read it ("≈ 12 min restantes"). */
export function fmtEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'estimation en cours…'
  if (seconds < 5) return 'quelques secondes'
  if (seconds < 90) return `≈ ${Math.round(seconds / 5) * 5} s restantes`
  if (seconds < 5400) return `≈ ${Math.round(seconds / 60)} min restantes`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 600) * 10
  return m === 60 ? `≈ ${h + 1} h restantes` : `≈ ${h} h ${String(m).padStart(2, '0')} restantes`
}

export const leaf = (path: string) => path.split('/').pop() ?? path
export const parent = (path: string) => path.split('/').slice(0, -1).join('/')

/** Folder trail of an asset path without the repetitions game paths have (`a/b/b/b` -> `a › b`). */
export function crumb(path: string): string {
  const parts = path.split('/').slice(0, -1)
  const out: string[] = []
  for (const p of parts) if (out[out.length - 1] !== p) out.push(p)
  return out.map((p) => titleCase(p)).join(' › ')
}

/** Debounce a function (search boxes). */
export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms = 250) {
  let t: ReturnType<typeof setTimeout> | undefined
  return (...a: A) => {
    clearTimeout(t)
    t = setTimeout(() => fn(...a), ms)
  }
}

export async function copy(text: string, what = 'Copié') {
  try {
    await navigator.clipboard.writeText(text)
    useToast().add({ title: what, description: text, icon: 'i-ri-file-copy-line', duration: 2200 })
  } catch {
    useToast().add({ title: 'Copie impossible', color: 'error' })
  }
}

// Labels for the roles/bodies the character browser shows. Unknown values fall back to a capitalised id.
export const BODY_LABEL: Record<string, string> = {
  male_reg: 'Homme',
  fem_reg: 'Femme',
  male_large: 'Large',
}
export const ROLE_LABEL: Record<string, string> = {
  civ: 'Civil',
  hero: 'Héros',
  grunt: 'Sbire',
  minion: 'Larbin',
  leader: 'Chef',
  specialist: 'Spécialiste',
  soldier: 'Soldat',
  armoredsoldier: 'Soldat blindé',
  brute: 'Brute',
  henchman: 'Homme de main',
  tank: 'Tank',
  sniper: 'Sniper',
  mercenaries: 'Mercenaires',
  authority: 'Autorité',
  crowd: 'Foule',
}
export const KIND_LABEL: Record<string, string> = {
  outfit: 'Tenue',
  outfitset: 'Ensemble',
  crowd: 'Foule',
}
export const roleLabel = (r: string) => (r ? (ROLE_LABEL[r] ?? titleCase(r)) : 'Sans rôle')
export const bodyLabel = (b: string) => BODY_LABEL[b] ?? titleCase(b)
