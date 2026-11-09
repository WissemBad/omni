/** Thin client for the omni API (`/api/<source>/...`, see omni/ui/api.py). */
export function api<T>(path: string, opts?: Record<string, unknown>): Promise<T> {
  return $fetch<T>(`/api${path}`, opts as never)
}

const BY_STATUS: Record<number, string> = {
  400: 'Requête invalide.',
  403: 'Accès refusé : rouvre omni depuis sa fenêtre.',
  404: 'Introuvable.',
  409: 'Une opération identique est déjà en cours.',
  413: 'Fichier trop volumineux.',
  415: 'Format non pris en charge.',
  422: 'Valeurs invalides.',
  500: 'Erreur interne d’omni. Le détail est dans son journal (workspace\\logs).',
  501: 'Fonction indisponible sur cet ordinateur.',
  502: 'omni ne répond pas.',
  503: 'omni ne répond pas.',
  504: 'omni ne répond pas.',
}

type ApiFailure = {
  status?: number
  statusCode?: number
  statusMessage?: string
  message?: string
  data?: { detail?: unknown }
}

/** A message for the user, always in French: the server's own sentence when it sent one, otherwise one per status. */
export function apiError(e: unknown): string {
  const err = (e ?? {}) as ApiFailure
  const detail = err.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const status = err.status ?? err.statusCode
  if (Array.isArray(detail) && detail.length) {
    const fields = detail
      .map((d) => (d as { loc?: unknown[] })?.loc?.slice(1).join('.'))
      .filter(Boolean)
      .join(', ')
    return fields ? `Valeurs invalides (${fields}).` : (BY_STATUS[422] as string)
  }
  if (status && BY_STATUS[status]) return BY_STATUS[status]
  if (!status) return 'omni ne répond pas. Vérifie qu’il est toujours ouvert.'
  return `Erreur ${status}.`
}
