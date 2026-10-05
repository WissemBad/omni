<script setup lang="ts">
import type { Job } from '~/utils/types'

/** Slide-over listing every conversion / build / export started from the UI, with live progress. */
const { jobs, open, schedule, refresh, cancel } = useJobs()

const expanded = ref<string | null>(null)
const detail = ref<Job | null>(null)

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
  running: { label: 'En cours', color: 'primary' },
  done: { label: 'Terminé', color: 'success' },
  error: { label: 'Échec', color: 'error' },
  cancelled: { label: 'Annulé', color: 'neutral' },
} as const

async function loadDetail() {
  if (!expanded.value) return
  try {
    detail.value = await api<Job>(`/jobs/${expanded.value}`)
  } catch {
    detail.value = null
  }
}

function view(j: Job, model: string) {
  open.value = false
  navigateTo({ path: `/${j.source}/viewer`, query: { m: model } })
}

function toggle(id: string) {
  expanded.value = expanded.value === id ? null : id
  detail.value = null
  loadDetail()
}

watch(open, (v) => {
  if (v) refresh()
  else {
    expanded.value = null
    detail.value = null
  }
  schedule()
})
watch(jobs, loadDetail)

function elapsed(j: Job) {
  const s = Math.max(0, Math.round((j.ended ?? Date.now() / 1000) - j.started))
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`
}

const summaryRows = (j: Job) =>
  Object.entries(j.summary ?? {}).filter(([, v]) => typeof v !== 'object' && v !== '' && v !== null)
</script>

<template>
  <USlideover v-model:open="open" title="Travaux" description="Conversions, playermodels et exports en cours ou récents." :ui="{ content: 'max-w-md' }">
    <template #body>
      <UEmpty
        v-if="!jobs.length"
        icon="i-ri-list-check-3"
        title="Rien pour le moment"
        description="Les conversions et exports que tu lances apparaissent ici avec leur progression."
      />
      <div v-else class="space-y-3">
        <UCard v-for="j in jobs" :key="j.id" :ui="{ body: 'p-3 sm:p-3' }">
          <UCollapsible :open="expanded === j.id" @update:open="toggle(j.id)">
            <button type="button" class="flex w-full items-start gap-3 text-left">
              <UIcon :name="kindOf(j.kind).icon" class="mt-0.5 size-5 shrink-0 text-muted" />
              <span class="min-w-0 flex-1">
                <span class="flex items-center gap-2">
                  <span class="truncate font-medium text-highlighted">{{ j.label }}</span>
                  <UBadge :label="PHASE[j.phase].label" :color="PHASE[j.phase].color" size="sm" class="shrink-0" />
                </span>
                <span class="mt-0.5 block truncate text-xs text-muted">
                  {{ kindOf(j.kind).label }} · {{ j.source }} · {{ elapsed(j) }}
                  <template v-if="j.failed"> · <span class="text-error">{{ j.failed }} échec(s)</span></template>
                </span>
              </span>
              <UIcon name="i-ri-arrow-down-s-line" class="mt-1 size-4 shrink-0 text-muted transition-transform" :class="expanded === j.id && 'rotate-180'" />
            </button>

            <template #content>
              <div class="mt-3 border-t border-default pt-3">
                <USkeleton v-if="!detail" class="h-12 w-full" />
                <template v-else>
                  <dl v-if="detail.phase === 'done' && summaryRows(detail).length" class="mb-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    <template v-for="[k, v] in summaryRows(detail)" :key="k">
                      <dt class="text-muted">{{ k }}</dt>
                      <dd class="truncate text-right tabular-nums text-toned">{{ v }}</dd>
                    </template>
                  </dl>
                  <ul v-if="detail.results?.length" class="max-h-64 space-y-1 overflow-y-auto pr-1 text-xs">
                    <li v-for="r in detail.results.slice().reverse()" :key="r.key" class="flex gap-2">
                      <UBadge :color="r.status === 'OK' ? 'success' : r.status === 'PARTIAL' ? 'warning' : 'error'" variant="solid" size="sm" class="mt-1 size-1.5 shrink-0 p-0" />
                      <span class="min-w-0 flex-1 break-words text-toned">
                        {{ r.model || r.key }}
                        <span v-if="r.errors.length" class="block text-error">{{ r.errors.join(' · ') }}</span>
                        <span v-else-if="r.notes.length" class="block text-muted">{{ r.notes.join(' · ') }}</span>
                      </span>
                      <UTooltip v-if="r.model && r.status !== 'FAILED'" text="Voir dans la visionneuse">
                        <UButton icon="i-ri-archive-line" size="xs" color="neutral" variant="ghost" aria-label="Voir dans la visionneuse" class="-mt-1 shrink-0" @click="view(detail, r.model)" />
                      </UTooltip>
                    </li>
                  </ul>
                  <ul v-else-if="detail.log?.length" class="max-h-64 space-y-0.5 overflow-y-auto font-mono text-[11px] text-muted">
                    <li v-for="(l, i) in detail.log.slice().reverse()" :key="i">{{ l }}</li>
                  </ul>
                </template>
              </div>
            </template>
          </UCollapsible>

          <template v-if="j.phase === 'running' || j.error">
            <UProgress v-if="j.phase === 'running'" class="mt-3" size="sm" :model-value="j.total ? j.done : null" :max="j.total || undefined" />
            <div v-if="j.phase === 'running'" class="mt-1 flex items-center justify-between gap-2">
              <p class="text-xs tabular-nums text-muted">{{ j.total ? `${j.done.toLocaleString('fr-FR')} / ${j.total.toLocaleString('fr-FR')}` : 'en cours…' }}</p>
              <UButton v-if="j.cancellable" label="Annuler" icon="i-ri-stop-circle-line" size="xs" color="neutral" variant="ghost" @click="cancel(j.id)" />
            </div>
            <p v-if="j.phase === 'running' && j.last" class="mt-1 truncate text-xs text-muted">{{ j.last }}</p>
            <UAlert v-if="j.error" class="mt-2" color="error" variant="subtle" :description="j.error" />
          </template>
        </UCard>
      </div>
    </template>
  </USlideover>
</template>
