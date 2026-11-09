<script setup lang="ts">
import type { Overview } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/home` })

/**
 * Overview of a game: what it offers and where things stand (catalogs, converted models, exported sounds), the
 * recent jobs, and the data omni keeps for it (storage, catalogs, previews). Exports live on their own page.
 */
const sid = useSourceId()
const jobs = useJobs()
const confirm = useConfirm()
const toast = useToast()

const ov = ref<Overview | null>(null)
const failed = ref('')
async function load() {
  try {
    ov.value = await api<Overview>(`/${sid.value}/overview`)
    failed.value = ''
  } catch (e) {
    failed.value = apiError(e)
  }
}
let timer: ReturnType<typeof setInterval> | undefined
onMounted(async () => {
  await Promise.all([load(), loadStorage()])
  timer = setInterval(() => {
    if (!document.hidden) load() // a minimised window does not need fresh figures
  }, 6000)
})
onBeforeUnmount(() => clearInterval(timer))
watch(() => jobs.jobs.value.filter((j) => !['queued', 'running'].includes(j.phase)).length, load)

const pct = (a = 0, b = 0) => (b ? Math.round((a / b) * 100) : 0)
const n = (v?: number | null) => (v ?? 0).toLocaleString('fr-FR')

interface Bench {
  key: string
  title: string
  icon: string
  to: string
  value: string
  detail: string
  progress?: number
  progressLabel?: string
}
const benches = computed<Bench[]>(() => {
  const o = ov.value
  if (!o) return []
  const out: Bench[] = []
  if (o.props) {
    out.push({
      key: 'props',
      title: 'Props',
      icon: 'i-ri-box-3-line',
      to: `/${sid.value}/models?tab=props`,
      value: n(o.props.named),
      detail: `objets nommés · ${n(o.props.total)} au total`,
      progress: pct(o.props.converted, o.props.named),
      progressLabel: `${n(o.props.converted)} convertis`,
    })
  }
  if (o.characters) {
    out.push({
      key: 'characters',
      title: 'Personnages',
      icon: 'i-ri-user-3-line',
      to: `/${sid.value}/models?tab=characters`,
      value: n(o.characters.total),
      detail: sid.value === '007fl' ? 'familles de tenues' : 'personnages jouables',
      progress: pct(o.characters.built, o.characters.total),
      progressLabel: `${n(o.characters.built)} playermodels créés`,
    })
  }
  if (o.textures) {
    out.push({
      key: 'textures',
      title: 'Textures',
      icon: 'i-ri-image-2-line',
      to: `/${sid.value}/textures`,
      value: o.textures.ready ? n(o.textures.textures) : '…',
      detail: o.textures.ready
        ? `${fmtBytes(o.textures.bytes ?? 0)} dans le jeu · ${n(o.textures.used)} utilisées`
        : 'indexation en cours',
    })
  }
  if (o.sounds) {
    out.push({
      key: 'sounds',
      title: 'Sons',
      icon: 'i-ri-music-2-line',
      to: `/${sid.value}/sounds`,
      value: o.sounds.exported ? n(o.sounds.count) : '0',
      detail: o.sounds.exported ? `exportés · ${fmtBytes(o.sounds.bytes)}` : 'pas encore exportés',
    })
  }
  return out
})

const deployed = computed(() => ov.value?.addon.deployed)

// ---- storage & maintenance of this game
const storage = ref<{
  addon: number
  previews: number
  audio: number
  cache: number
  path: string
  exports: string
} | null>(null)
const loadStorage = async () =>
  (storage.value = await api<typeof storage.value>(`/${sid.value}/storage`).catch(() => null))
const sizes = computed(() =>
  storage.value
    ? [
        {
          label: 'Addon converti',
          value: fmtBytes(storage.value.addon),
          icon: 'i-ri-archive-line',
        },
        { label: 'Sons exportés', value: fmtBytes(storage.value.audio), icon: 'i-ri-music-2-line' },
        { label: 'Aperçus 3D', value: fmtBytes(storage.value.previews), icon: 'i-ri-eye-line' },
        { label: 'Caches', value: fmtBytes(storage.value.cache), icon: 'i-ri-database-2-line' },
      ]
    : [],
)
async function clearPreviews() {
  if (
    !(await confirm({
      title: 'Supprimer les aperçus ?',
      description: 'Les aperçus 3D et les miniatures en cache seront recréés à la demande.',
      confirmLabel: 'Supprimer',
      destructive: true,
    }))
  )
    return
  try {
    await api(`/${sid.value}/previews/clear`, { method: 'POST' })
    toast.add({ title: 'Aperçus supprimés', icon: 'i-ri-delete-bin-line' })
    loadStorage()
  } catch (e) {
    toast.add({ title: 'Suppression impossible', description: apiError(e), color: 'error' })
  }
}
const MAINTENANCE = computed(() => {
  const caps = ov.value?.capabilities ?? []
  return [
    caps.includes('props') && {
      label: 'Réindexer les props',
      icon: 'i-ri-box-3-line',
      run: () => startJob(`/${sid.value}/catalog/rebuild`, { open: true }),
    },
    caps.includes('textures') && {
      label: 'Réindexer les textures',
      icon: 'i-ri-image-2-line',
      run: () => startJob(`/${sid.value}/textures/rebuild`, { open: true }),
    },
    caps.includes('sounds') && {
      label: 'Relister les sons',
      icon: 'i-ri-music-2-line',
      run: () => startJob(`/${sid.value}/sounds/relist`, { open: true }),
    },
  ].filter(Boolean) as { label: string; icon: string; run: () => void }[]
})
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-8 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-6xl space-y-3">
      <UAlert v-if="failed" color="error" variant="subtle" icon="i-ri-error-warning-line" title="Aperçu indisponible" :description="failed" />

      <!-- source -->
      <UCard :ui="{ body: 'flex flex-wrap items-center gap-4 p-4 sm:p-5' }">
        <div class="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <UIcon name="i-ri-gamepad-line" class="size-6" />
        </div>
        <div class="min-w-0 flex-1">
          <h1 class="text-xl font-semibold text-highlighted">{{ ov?.title ?? sid }}</h1>
          <p class="text-sm text-muted">{{ ov?.description || 'Source de jeu' }} · vers Garry’s Mod (Source)</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <UBadge :label="deployed ? 'Addon lié à GMod' : 'Addon non lié'" :color="deployed ? 'success' : 'neutral'" variant="subtle" :icon="deployed ? 'i-ri-link' : 'i-ri-link-unlink'" />
          <UButton label="Exports" icon="i-ri-download-2-line" size="sm" color="primary" variant="soft" :to="`/${sid}/exports`" />
        </div>
      </UCard>

      <!-- workbenches -->
      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <template v-if="!ov">
          <USkeleton v-for="i in 4" :key="i" class="h-36" />
        </template>
        <NuxtLink v-for="b in benches" :key="b.key" :to="b.to" class="group">
          <UCard class="h-full transition group-hover:ring-2 group-hover:ring-primary/40" :ui="{ body: 'space-y-3 p-4 sm:p-4' }">
            <div class="flex items-center gap-2 text-sm text-muted">
              <UIcon :name="b.icon" class="size-4" />{{ b.title }}
              <UIcon name="i-ri-arrow-right-up-line" class="ml-auto size-4 opacity-0 transition group-hover:opacity-100" />
            </div>
            <div>
              <p class="text-2xl font-semibold tabular-nums text-highlighted">{{ b.value }}</p>
              <p class="text-xs text-muted">{{ b.detail }}</p>
            </div>
            <div v-if="b.progress !== undefined" class="space-y-1">
              <UProgress :model-value="b.progress" size="xs" />
              <p class="text-xs tabular-nums text-muted">{{ b.progressLabel }} · {{ b.progress }} %</p>
            </div>
          </UCard>
        </NuxtLink>
      </div>

      <div class="grid gap-3 lg:grid-cols-3">
        <!-- storage & maintenance -->
        <UCard class="lg:col-span-2" :ui="{ body: 'space-y-4 p-4 sm:p-4' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Stockage et maintenance</h2>
            <p v-if="storage" class="truncate text-sm text-muted" :title="storage.path">Ce qu’omni garde pour ce jeu. Exports : {{ storage.exports }}</p>
          </template>
          <OStatGrid v-if="sizes.length" :stats="sizes" :cols="4" />
          <div class="flex flex-wrap gap-2">
            <UButton v-for="m in MAINTENANCE" :key="m.label" :label="m.label" :icon="m.icon" size="sm" color="neutral" variant="outline" @click="m.run()" />
            <UButton label="Vider les aperçus" icon="i-ri-delete-bin-line" size="sm" color="neutral" variant="outline" @click="clearPreviews" />
          </div>
        </UCard>

        <!-- recent jobs -->
        <UCard :ui="{ body: 'p-2 sm:p-2' }">
          <template #header>
            <div class="flex items-center justify-between">
              <h2 class="text-base font-semibold text-highlighted">Travaux récents</h2>
              <UButton label="Tout voir" size="xs" color="neutral" variant="link" @click="jobs.open.value = true" />
            </div>
          </template>
          <UEmpty v-if="!ov?.jobs.length" icon="i-ri-list-check-3" title="Rien pour l’instant" description="Les conversions et exports apparaissent ici." />
          <ul v-else class="space-y-0.5">
            <li v-for="j in ov.jobs" :key="j.id" class="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm">
              <UIcon :name="j.phase === 'running' ? 'i-ri-loader-4-line' : j.phase === 'done' ? 'i-ri-checkbox-circle-fill' : j.phase === 'cancelled' ? 'i-ri-stop-circle-line' : 'i-ri-close-circle-fill'" class="size-4 shrink-0" :class="[j.phase === 'running' && 'animate-spin text-primary', j.phase === 'done' && 'text-success', j.phase === 'error' && 'text-error', j.phase === 'cancelled' && 'text-muted']" />
              <span class="min-w-0 flex-1 truncate text-toned">{{ j.label }}</span>
              <span class="shrink-0 text-xs tabular-nums text-muted">{{ timeAgo(j.started) }}</span>
            </li>
          </ul>
        </UCard>
      </div>
    </div>
  </div>
</template>
