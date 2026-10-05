/** Thin client for the omni API (`/api/<source>/...`, see omni/ui/api.py). */
export function api<T>(path: string, opts?: Record<string, unknown>): Promise<T> {
  return $fetch<T>(`/api${path}`, opts as never)
}

export function apiError(e: unknown): string {
  const err = e as { data?: { detail?: unknown }; statusMessage?: string; message?: string }
  const detail = err?.data?.detail
  return typeof detail === 'string' ? detail : (err?.statusMessage ?? err?.message ?? String(e))
}
