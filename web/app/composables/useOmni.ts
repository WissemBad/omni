import type {
  Job,
  Settings,
  SetupStatus,
  SourceDetails,
  SourceInfo,
  SystemInfo,
} from '~/utils/types'

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
      details.value = {
        ...details.value,
        [sid.value]: await api<SourceDetails>(`/${sid.value}/info`),
      }
    } catch {
      /* the page shows its own error */
    }
  }
  async function toggleDeploy() {
    const on = current.value?.deployed
    try {
      const r = await api<{ message: string }>(
        `/${sid.value}/deploy?remove=${on ? 'true' : 'false'}`,
        {
          method: 'POST',
        },
      )
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

let stream: EventSource | undefined
let retryTimer: ReturnType<typeof setTimeout> | undefined
let titleBase = ''

const ACTIVE = ['queued', 'running']

/**
 * Long operations (conversion, playermodel build, sound export, setup steps): one shared live list.
 * The server pushes the list over server-sent events whenever something changes; if the stream drops, it reconnects
 * (and reads the list once so nothing is missed).
 */
export function useJobs() {
  const jobs = useState<Job[]>('jobs', () => [])
  const open = useState('jobs-open', () => false)
  const live = useState('jobs-live', () => false)
  const running = computed(() => jobs.value.filter((j) => ACTIVE.includes(j.phase)).length)
  const toast = useToast()

  function announce(j: Job) {
    const ok = j.phase === 'done'
    const partial = ok && (j.failed ?? 0) > 0
    toast.add({
      title: ok
        ? `${j.label} : terminé${partial ? ` (${j.failed} échec(s))` : ''}`
        : j.phase === 'cancelled'
          ? `${j.label} : annulé`
          : j.phase === 'interrupted'
            ? `${j.label} : interrompu`
            : `${j.label} : échec`,
      description: j.error || j.last || undefined,
      color:
        ok && !partial ? 'success' : j.phase === 'cancelled' ? 'neutral' : ok ? 'warning' : 'error',
      icon: ok
        ? 'i-ri-checkbox-circle-line'
        : j.phase === 'cancelled'
          ? 'i-ri-stop-circle-line'
          : 'i-ri-error-warning-line',
      actions: [
        ...(ok && j.models
          ? [
              {
                label: 'Voir le résultat',
                onClick: () =>
                  navigateTo({
                    path: `/${j.source}/viewer`,
                    query: j.models === 1 ? { m: j.model } : {},
                  }),
              },
            ]
          : []),
        { label: 'Détails', onClick: () => (open.value = true) },
      ],
    })
  }

  function apply(next: Job[]) {
    const before = new Map(jobs.value.map((j) => [j.id, j.phase]))
    jobs.value = next
    for (const j of next) {
      const was = before.get(j.id)
      if (was && ACTIVE.includes(was) && !ACTIVE.includes(j.phase)) announce(j)
    }
    if (import.meta.client) {
      titleBase ||= document.title.replace(/^\(.*?\) /, '')
      const run = next.find((j) => j.phase === 'running')
      document.title = run
        ? `(${run.total ? `${Math.round((run.done / run.total) * 100)} %` : 'en cours'}) ${titleBase}`
        : titleBase
    }
  }

  async function refresh() {
    try {
      apply(await api<Job[]>('/jobs'))
    } catch {
      /* API restarting */
    }
  }

  function connect() {
    if (!import.meta.client || stream || typeof EventSource === 'undefined') return
    stream = new EventSource('/api/jobs/stream')
    stream.addEventListener('jobs', (e) => {
      live.value = true
      apply(JSON.parse((e as MessageEvent).data) as Job[])
    })
    stream.onerror = () => {
      live.value = false
      stream?.close()
      stream = undefined
      clearTimeout(retryTimer)
      retryTimer = setTimeout(() => {
        refresh()
        connect()
      }, 3000)
    }
  }
  connect()

  /** Kept for the callers that start a job: the stream already carries it. */
  async function track(_id?: string) {
    if (!live.value) await refresh()
  }

  async function act(path: string, method = 'POST') {
    try {
      return await api<Record<string, unknown>>(path, { method })
    } catch (e) {
      toast.add({ title: 'Action impossible', description: apiError(e), color: 'error' })
      throw e
    }
  }
  const cancel = (id: string) => act(`/jobs/${id}/cancel`).catch(() => {})
  const front = (id: string) => act(`/jobs/${id}/front`).catch(() => {})
  const remove = (id: string) => act(`/jobs/${id}`, 'DELETE').catch(() => {})
  const clear = () => act('/jobs/clear').catch(() => {})
  /** Replay a job: its failed items (optionally one cause), what it never reached, or everything. */
  async function retry(id: string, scope: 'failed' | 'remaining' | 'all' = 'failed', cause = '') {
    const q = new URLSearchParams({ scope })
    if (cause) q.set('cause', cause)
    const r = await act(`/jobs/${id}/retry?${q}`).catch(() => null)
    if (r)
      toast.add({ title: 'Ajouté à la file', icon: 'i-ri-play-list-add-line', color: 'success' })
    open.value = true
    return r
  }

  /** The running job of a kind for a source (progress cards). */
  const liveJob = (source: string, kind: Job['kind']) =>
    jobs.value.find((j) => j.source === source && j.kind === kind && ACTIVE.includes(j.phase))

  return {
    jobs,
    open,
    running,
    refresh,
    track,
    cancel,
    front,
    remove,
    clear,
    retry,
    live: liveJob,
    connected: live,
  }
}

/** A newer release of omni (checked by the server at launch, at most every six hours). */
export function useUpdate() {
  const info = useState<UpdateInfo | null>('update', () => null)
  const confirm = useConfirm()
  async function check(force = false) {
    try {
      info.value = await api<UpdateInfo>(`/update${force ? '?force=true' : ''}`)
    } catch {
      /* offline */
    }
    return info.value
  }
  async function install() {
    const u = info.value
    if (!u?.available) return
    const ok = await confirm({
      title: `Installer omni ${u.latest} ?`,
      description:
        'L’installeur est téléchargé et vérifié, puis omni se ferme et redémarre sur la nouvelle version.',
      confirmLabel: 'Mettre à jour',
    })
    if (ok) await startJob('/update/install', { open: true })
  }
  return { info, check, install }
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
      const r = await api<{ values: Settings; defaults: Settings }>('/settings', {
        method: 'PUT',
        body: patch,
      })
      values.value = r.values
    } finally {
      saving.value = false
    }
  }
  async function reset() {
    const r = await api<{ values: Settings; defaults: Settings }>('/settings/reset', {
      method: 'POST',
    })
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
export async function startJob(
  path: string,
  opts: { body?: unknown; title?: string; open?: boolean } = {},
) {
  const jobs = useJobs()
  try {
    const { job } = await api<{ job: string }>(path, { method: 'POST', body: opts.body })
    if (opts.title) useToast().add({ title: opts.title, icon: 'i-ri-play-large-line' })
    await jobs.track(job)
    if (opts.open) jobs.open.value = true
    return job
  } catch (e) {
    useToast().add({ title: 'Impossible de lancer', description: apiError(e), color: 'error' })
    return null
  }
}

/** First-run setup: what is configured, and the long steps (extraction, names, compiler) as jobs. */
export function useSetup() {
  const status = useState<SetupStatus | null>('setup-status', () => null)
  const jobs = useJobs()
  const running = computed(() =>
    jobs.jobs.value.find((j) => j.kind === 'setup' && ACTIVE.includes(j.phase)),
  )
  async function load() {
    status.value = await api<SetupStatus>('/setup/status').catch(() => status.value)
    return status.value
  }
  return { status, running, load, jobs }
}
