import type { Job, Settings, SourceDetails, SourceInfo, SystemInfo } from '~/utils/types'

/** Sources the API knows (loaded once). */
export function useSources() {
  const sources = useState<SourceInfo[]>('sources', () => [])
  const ready = useState('sources-ready', () => false)
  async function load() {
    if (ready.value) return sources.value
    sources.value = await api<SourceInfo[]>('/sources')
    ready.value = true
    return sources.value
  }
  return { sources, load }
}

/** Source id of the current route (`/<source>/props`). */
export function useSourceId() {
  const route = useRoute()
  return computed(() => String(route.params.source ?? ''))
}

/** Details + GMod link state of the current source. */
export function useSourceDetails() {
  const sid = useSourceId()
  const details = useState<Record<string, SourceDetails>>('source-details', () => ({}))
  const current = computed(() => details.value[sid.value])
  async function refresh() {
    if (!sid.value) return
    try {
      details.value = { ...details.value, [sid.value]: await api<SourceDetails>(`/${sid.value}/info`) }
    } catch {
      /* the page shows its own error */
    }
  }
  async function toggleDeploy() {
    const on = current.value?.deployed
    try {
      const r = await api<{ message: string }>(`/${sid.value}/deploy?remove=${on ? 'true' : 'false'}`, {
        method: 'POST',
      })
      useToast().add({
        title: on ? 'Addon délié de GMod' : 'Addon lié à GMod',
        description: r.message,
        color: 'success',
      })
    } catch (e) {
      useToast().add({ title: 'Liaison impossible', description: apiError(e), color: 'error' })
    }
    await refresh()
  }
  return { details: current, refresh, toggleDeploy }
}

let pollTimer: ReturnType<typeof setTimeout> | undefined

/** Long operations (conversion, playermodel build, sound export): one shared poller. */
export function useJobs() {
  const jobs = useState<Job[]>('jobs', () => [])
  const open = useState('jobs-open', () => false)
  const watched = useState<string[]>('jobs-watched', () => [])
  const running = computed(() => jobs.value.filter((j) => j.phase === 'running').length)
  const toast = useToast()

  async function refresh() {
    try {
      const before = new Map(jobs.value.map((j) => [j.id, j.phase]))
      jobs.value = await api<Job[]>('/jobs')
      for (const j of jobs.value) {
        if (before.get(j.id) === 'running' && j.phase !== 'running' && watched.value.includes(j.id)) {
          watched.value = watched.value.filter((id) => id !== j.id)
          const ok = j.phase === 'done'
          toast.add({
            title: ok ? `${j.label} : terminé` : j.phase === 'cancelled' ? `${j.label} : annulé` : `${j.label} : échec`,
            description: j.error || j.last || undefined,
            color: ok ? 'success' : j.phase === 'cancelled' ? 'neutral' : 'error',
            icon: ok ? 'i-ri-checkbox-circle-line' : j.phase === 'cancelled' ? 'i-ri-stop-circle-line' : 'i-ri-error-warning-line',
            actions: [
              ...(j.phase === 'done' && j.models
                ? [{
                    label: 'Voir le résultat',
                    onClick: () => navigateTo({ path: `/${j.source}/viewer`, query: j.models === 1 ? { m: j.model } : {} }),
                  }]
                : []),
              { label: 'Détails', onClick: () => (open.value = true) },
            ],
          })
        }
      }
    } catch {
      /* API restarting */
    }
    schedule()
  }

  function schedule() {
    clearTimeout(pollTimer)
    pollTimer = undefined
    if (import.meta.client && (running.value > 0 || open.value)) pollTimer = setTimeout(refresh, 1200)
  }

  /** Follow a job: it appears in the panel and a toast announces its end. */
  async function track(id: string) {
    watched.value = [...watched.value, id]
    await refresh()
  }

  async function cancel(id: string) {
    await api(`/jobs/${id}/cancel`, { method: 'POST' }).catch(() => {})
    await refresh()
  }

  /** The running job of a kind for a source (progress cards). */
  const live = (source: string, kind: Job['kind']) =>
    jobs.value.find((j) => j.source === source && j.kind === kind && j.phase === 'running')

  return { jobs, open, running, refresh, track, schedule, cancel, live }
}

/** Server-side settings (workspace/settings.json): what the conversions, exports and previews really use. */
export function useSettings() {
  const values = useState<Settings | null>('settings', () => null)
  const defaults = useState<Settings | null>('settings-defaults', () => null)
  const saving = useState('settings-saving', () => false)
  async function load(force = false) {
    if (values.value && !force) return values.value
    const r = await api<{ values: Settings; defaults: Settings }>('/settings')
    values.value = r.values
    defaults.value = r.defaults
    return r.values
  }
  async function save(patch: Partial<Record<keyof Settings, Record<string, unknown>>>) {
    saving.value = true
    try {
      const r = await api<{ values: Settings; defaults: Settings }>('/settings', { method: 'PUT', body: patch })
      values.value = r.values
    } finally {
      saving.value = false
    }
  }
  async function reset() {
    const r = await api<{ values: Settings; defaults: Settings }>('/settings/reset', { method: 'POST' })
    values.value = r.values
  }
  return { values, defaults, saving, load, save, reset }
}

/** Rust core, tools, workspace (home and settings pages). */
export function useSystem() {
  const info = useState<SystemInfo | null>('system', () => null)
  async function load() {
    info.value = await api<SystemInfo>('/system').catch(() => null)
    return info.value
  }
  return { info, load }
}

/** Starts a server job (POST returning {job}) and follows it in the jobs panel. */
export async function startJob(path: string, opts: { body?: unknown; title?: string; open?: boolean } = {}) {
  const jobs = useJobs()
  try {
    const { job } = await api<{ job: string }>(path, { method: 'POST', body: opts.body })
    if (opts.title) useToast().add({ title: opts.title, icon: 'i-ri-play-large-line' })
    await jobs.track(job)
    if (opts.open) jobs.open.value = true
    jobs.schedule()
    return job
  } catch (e) {
    useToast().add({ title: 'Impossible de lancer', description: apiError(e), color: 'error' })
    return null
  }
}
