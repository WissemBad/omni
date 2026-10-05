<script setup lang="ts">
import type { Job, JobCause, JobResult } from '~/utils/types'

/**
 * Slide-over listing every conversion / build / export / setup step: the queue (one heavy job at a time), live
 * progress pushed by the server, and for a finished job its results, its failures grouped by cause and what to retry.
 */
const { jobs, open, cancel, front, remove, clear, retry } = useJobs()
const confirm = useConfirm()
const toast = useToast()

const expanded = ref<string | null>(null)
const detail = ref<Job | null>(null)
const results = ref<JobResult[]>([])
const resultsTotal = ref(0)
const causes = ref<JobCause[]>([])
const loadingMore = ref(false)
const PAGE = 40

const KIND: Record<string, { icon: string; label: string }> = {
  props: { icon: 'i-ri-box-3-line', label: 'Props' },
  character: { icon: 'i-ri-user-3-line', label: 'Personnage' },
  sounds: { icon: 'i-ri-music-2-line', label: 'Sons' },
  textures: { icon: 'i-ri-image-2-line', label: 'Textures' },
  maintenance: { icon: 'i-ri-tools-line', label: 'Maintenance' },
  setup: { icon: 'i-ri-install-line', label: 'Installation' },
}
const kindOf = (k: string) => KIND[k] ?? { icon: 'i-ri-list-check-3', label: k }

const PHASE = {
  queued: { label: 'En attente', color: 'neutral' },
  running: { label: 'En cours', color: 'primary' },
  done: { label: 'Terminé', color: 'success' },
  error: { label: 'Échec', color: 'error' },
  cancelled: { label: 'Annulé', color: 'neutral' },
  interrupted: { label: 'Interrompu', color: 'warning' },
} as const

const STATUS_COLOR: Record<string, 'success' | 'warning' | 'error' | 'neutral'> = {
  OK: 'success',
  PARTIAL: 'warning',
  FAILED: 'error',
  SKIPPED: 'neutral',
}

const active = (j: Job) => j.phase === 'queued' || j.phase === 'running'
const current = computed(() => jobs.value.find((j) => j.id === expanded.value) ?? null)

async function loadDetail() {
  const id = expanded.value
  if (!id) return
  try {
    detail.value = await api<Job>(`/jobs/${id}`)
    if (expanded.value !== id) return
    if (current.value && !active(current.value)) await loadResults(true)
    else {
      results.value = ((await api<Job>(`/jobs/${id}?tail=20`)).results ?? []).slice().reverse()
      resultsTotal.value = detail.value.results_total ?? 0
    }
  } catch {
    detail.value = null
  }
}

async function loadResults(reset = false) {
  const id = expanded.value
  if (!id) return
  loadingMore.value = true
  try {
    const offset = reset ? 0 : results.value.length
    const page = await api<{ total: number; items: JobResult[] }>(
      `/jobs/${id}/results?limit=${PAGE}&offset=${offset}`,
    )
    results.value = reset ? page.items : [...results.value, ...page.items]
    resultsTotal.value = page.total
    if (reset)
      causes.value =
        (current.value?.failed ?? 0) > 0 ? await api<JobCause[]>(`/jobs/${id}/report`) : []
  } finally {
    loadingMore.value = false
  }
}

function toggle(id: string) {
  expanded.value = expanded.value === id ? null : id
  detail.value = null
  results.value = []
  causes.value = []
  if (expanded.value) loadDetail()
}

// a running job's detail is refreshed every few seconds; a finished one is read once, again when its phase changes
let tick: ReturnType<typeof setInterval> | undefined
watch(
  [expanded, () => current.value?.phase],
  () => {
    clearInterval(tick)
    if (expanded.value && current.value && active(current.value))
      tick = setInterval(loadDetail, 3000)
    else if (expanded.value) loadDetail()
  },
  { flush: 'post' },
)
onBeforeUnmount(() => clearInterval(tick))
watch(open, (v) => {
  if (!v) {
    expanded.value = null
    detail.value = null
  }
})

// Garry's Mod open while models are written: its file locks make conversions fail
const gmodOpen = ref(false)
const writing = computed(() =>
  jobs.value.some((j) => active(j) && ['props', 'character'].includes(j.kind)),
)
watch(
  [open, writing],
  async ([isOpen, busy]) => {
    if (isOpen && busy)
      gmodOpen.value = (
        await api<{ running: boolean }>('/gmod/status').catch(() => ({ running: false }))
      ).running
  },
  { immediate: true },
)

function view(j: Job, model: string) {
  open.value = false
  navigateTo({ path: `/${j.source}/viewer`, query: { m: model } })
}

function elapsed(j: Job) {
  const s = Math.max(0, Math.round((j.ended ?? Date.now() / 1000) - j.started))
  return s < 60
    ? `${s} s`
    : s < 3600
      ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`
      : `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`
}

const SUMMARY_HIDDEN = new Set(['report', 'cancelled'])
const summaryRows = (j: Job) =>
  Object.entries(j.summary ?? {}).filter(
    ([k, v]) =>
      !SUMMARY_HIDDEN.has(k) && typeof v !== 'object' && v !== '' && v !== null && v !== 0,
  )

const countsOf = (j: Job) => Object.entries(j.counts ?? {}).filter(([, n]) => n > 0)

async function stop(j: Job) {
  if (
    j.phase === 'running' &&
    !(await confirm({
      title: 'Annuler ce travail ?',
      description: `« ${j.label} » s’arrête ; ce qui est déjà converti est conservé.`,
      confirmLabel: 'Annuler le travail',
      destructive: true,
    }))
  )
    return
  await cancel(j.id)
}

async function copyReport(j: Job) {
  const lines = [
    `${j.label} — ${j.phase}`,
    ...causes.value.map(
      (c) => `${c.count} × ${c.cause}\n   ${c.examples.join(', ')}\n   ${c.sample}`,
    ),
  ]
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    toast.add({ title: 'Rapport copié', icon: 'i-ri-clipboard-line' })
  } catch {
    toast.add({ title: 'Copie impossible', color: 'error' })
  }
}

async function wipe() {
  if (
    await confirm({
      title: 'Vider l’historique ?',
      description:
        'Les travaux terminés sont supprimés de la liste (les fichiers produits restent).',
      confirmLabel: 'Vider',
      destructive: true,
    })
  )
    await clear()
}
</script>

<template>
  <USlideover v-model:open="open" title="Travaux" description="File d’attente, conversions et exports : un travail lourd à la fois." :ui="{ content: 'max-w-md' }">
    <template #body>
      <UEmpty
        v-if="!jobs.length"
        icon="i-ri-list-check-3"
        title="Rien pour le moment"
        description="Les conversions et exports que tu lances apparaissent ici avec leur progression."
      />
      <div v-else class="space-y-3">
        <UAlert v-if="gmodOpen && writing" color="warning" variant="subtle" icon="i-ri-gamepad-line" title="Garry’s Mod est ouvert" description="Il verrouille les fichiers de l’addon : ferme-le pendant la conversion pour éviter des échecs." />
        <div class="flex justify-end">
          <UButton label="Vider l’historique" icon="i-ri-delete-bin-line" size="xs" color="neutral" variant="ghost" :disabled="jobs.every(active)" @click="wipe" />
        </div>
        <UCard v-for="j in jobs" :key="j.id" :ui="{ body: 'p-3 sm:p-3' }">
          <UCollapsible :open="expanded === j.id" @update:open="toggle(j.id)">
            <button type="button" class="flex w-full items-start gap-3 text-left" :aria-expanded="expanded === j.id">
              <UIcon :name="kindOf(j.kind).icon" class="mt-0.5 size-5 shrink-0 text-muted" />
              <span class="min-w-0 flex-1">
                <span class="flex items-center gap-2">
                  <span class="truncate font-medium text-highlighted">{{ j.label }}</span>
                  <UBadge :label="j.cancelling ? 'Annulation…' : PHASE[j.phase].label" :color="j.cancelling ? 'warning' : PHASE[j.phase].color" size="sm" class="shrink-0" />
                </span>
                <span class="mt-0.5 block truncate text-xs text-muted">
                  {{ kindOf(j.kind).label }} · {{ j.source || 'omni' }} · {{ elapsed(j) }}
                  <template v-if="j.phase === 'queued' && j.position"> · n°{{ j.position }} dans la file</template>
                  <template v-for="[st, n] in countsOf(j)" :key="st"> · <span :class="st === 'FAILED' ? 'text-error' : st === 'PARTIAL' ? 'text-warning' : ''">{{ n.toLocaleString('fr-FR') }} {{ st === 'OK' ? 'OK' : st === 'PARTIAL' ? 'partiel(s)' : st === 'FAILED' ? 'échec(s)' : 'ignoré(s)' }}</span></template>
                </span>
              </span>
              <UIcon name="i-ri-arrow-down-s-line" class="mt-1 size-4 shrink-0 text-muted transition-transform" :class="expanded === j.id && 'rotate-180'" />
            </button>

            <template #content>
              <div class="mt-3 space-y-3 border-t border-default pt-3">
                <USkeleton v-if="!detail" class="h-12 w-full" />
                <template v-else>
                  <dl v-if="detail.phase === 'done' && summaryRows(detail).length" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    <template v-for="[k, v] in summaryRows(detail)" :key="k">
                      <dt class="text-muted">{{ k }}</dt>
                      <dd class="truncate text-right tabular-nums text-toned">{{ v }}</dd>
                    </template>
                  </dl>

                  <div v-if="causes.length" class="space-y-2">
                    <div class="flex items-center justify-between gap-2">
                      <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Échecs par cause</h3>
                      <UButton label="Copier" icon="i-ri-clipboard-line" size="xs" color="neutral" variant="ghost" @click="copyReport(detail)" />
                    </div>
                    <ul class="space-y-1.5">
                      <li v-for="c in causes" :key="c.cause" class="rounded-md border border-default p-2 text-xs">
                        <div class="flex items-start gap-2">
                          <span class="min-w-0 flex-1 break-words font-medium text-toned">{{ c.cause }}</span>
                          <UBadge :label="String(c.count)" color="error" variant="subtle" size="sm" />
                        </div>
                        <p class="mt-1 break-all font-mono text-[11px] text-muted">{{ c.examples.join(' · ') }}</p>
                        <UButton v-if="detail.has_request" label="Réessayer ceux-là" icon="i-ri-restart-line" size="xs" color="neutral" variant="soft" class="mt-1.5" @click="retry(detail.id, 'failed', c.cause)" />
                      </li>
                    </ul>
                  </div>

                  <div v-if="!active(detail) && detail.has_request" class="flex flex-wrap gap-2">
                    <UButton v-if="detail.failed" :label="`Réessayer les ${detail.failed} échecs`" icon="i-ri-restart-line" size="xs" @click="retry(detail.id, 'failed')" />
                    <UButton v-if="detail.phase === 'interrupted' || detail.phase === 'cancelled'" label="Reprendre" icon="i-ri-play-line" size="xs" @click="retry(detail.id, 'remaining')" />
                    <UButton label="Relancer tout" icon="i-ri-loop-left-line" size="xs" color="neutral" variant="soft" @click="retry(detail.id, 'all')" />
                  </div>

                  <ul v-if="results.length" class="max-h-64 space-y-1 overflow-y-auto pr-1 text-xs">
                    <li v-for="r in results" :key="r.key" class="flex gap-2">
                      <UBadge :color="STATUS_COLOR[r.status] ?? 'neutral'" variant="solid" size="sm" class="mt-1 size-1.5 shrink-0 p-0" />
                      <span class="min-w-0 flex-1 break-words text-toned">
                        {{ r.model || r.key }}
                        <span v-if="r.errors?.length" class="block text-error">{{ r.errors.join(' · ') }}</span>
                        <span v-else-if="r.notes?.length" class="block text-muted">{{ r.notes.join(' · ') }}</span>
                      </span>
                      <UTooltip v-if="r.model && r.status !== 'FAILED'" text="Voir dans la visionneuse">
                        <UButton icon="i-ri-archive-line" size="xs" color="neutral" variant="ghost" aria-label="Voir dans la visionneuse" class="-mt-1 shrink-0" @click="view(detail, r.model)" />
                      </UTooltip>
                    </li>
                    <li v-if="results.length < resultsTotal && !active(detail)">
                      <UButton :label="`Afficher la suite (${(resultsTotal - results.length).toLocaleString('fr-FR')})`" size="xs" color="neutral" variant="ghost" block :loading="loadingMore" @click="loadResults()" />
                    </li>
                  </ul>
                  <ul v-else-if="detail.log?.length" class="max-h-64 space-y-0.5 overflow-y-auto font-mono text-[11px] text-muted">
                    <li v-for="(l, i) in detail.log.slice().reverse()" :key="i">{{ l }}</li>
                  </ul>

                  <UButton v-if="!active(detail)" label="Supprimer de l’historique" icon="i-ri-delete-bin-line" size="xs" color="neutral" variant="ghost" @click="remove(detail.id).then(() => toggle(detail!.id))" />
                </template>
              </div>
            </template>
          </UCollapsible>

          <UProgress v-if="j.phase === 'running'" class="mt-3" size="sm" :model-value="j.total ? j.done : null" :max="j.total || undefined" aria-label="Progression du travail" />
          <div v-if="active(j)" class="mt-1 flex items-center justify-between gap-2">
            <p class="text-xs tabular-nums text-muted">{{ j.phase === 'queued' ? 'en attente…' : j.total ? `${j.done.toLocaleString('fr-FR')} / ${j.total.toLocaleString('fr-FR')}` : 'en cours…' }}</p>
            <div class="flex gap-1">
              <UButton v-if="j.phase === 'queued' && (j.position ?? 0) > 1" label="En premier" icon="i-ri-arrow-up-line" size="xs" color="neutral" variant="ghost" @click="front(j.id)" />
              <UButton v-if="j.cancellable || j.phase === 'queued'" :label="j.cancelling ? 'Annulation…' : 'Annuler'" icon="i-ri-stop-circle-line" size="xs" color="neutral" variant="ghost" :disabled="j.cancelling" @click="stop(j)" />
            </div>
          </div>
          <p v-if="j.phase === 'running' && j.last" class="mt-1 truncate text-xs text-muted">{{ j.last }}</p>
          <UAlert v-if="j.error" class="mt-2" :color="j.phase === 'interrupted' ? 'warning' : 'error'" variant="subtle" :description="j.error" />
        </UCard>
      </div>
    </template>
  </USlideover>
</template>
