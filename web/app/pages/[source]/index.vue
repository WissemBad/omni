<script setup lang="ts">
import type { Job, Overview } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/home` })

/**
 * Home: what the source offers and where things stand (catalogs, converted models, exported sounds, the GMod
 * addon), the global exports with their progress, and the health of the tools (Rust core, StudioMDL, GMod).
 */
const sid = useSourceId()
const jobs = useJobs()
const { info: system, load: loadSystem } = useSystem()
const { toggleDeploy } = useSourceDetails()
const { values: settingsValues, load: loadSettings } = useSettings()

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
  await Promise.all([load(), loadSystem(), loadSettings().catch(() => null)])
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

// ---- global exports
const forceSounds = ref(false)
const starting = ref('')
const confirm = useConfirm()
const toast = useToast()
async function run(key: string, path: string, body?: unknown, confirmText?: string) {
  if (
    confirmText &&
    !(await confirm({
      title: 'Lancer ce travail ?',
      description: confirmText,
      confirmLabel: 'Lancer',
    }))
  )
    return
  starting.value = key
  try {
    await startJob(path, { body, open: true })
  } finally {
    starting.value = ''
  }
}
async function exportProps() {
  // the server resolves the filter: nothing to download (and nothing to lose when the download fails)
  let total = 0
  try {
    total = (await api<{ total: number }>(`/${sid.value}/props?named=1&limit=1`)).total
  } catch (e) {
    toast.add({ title: 'Catalogue indisponible', description: apiError(e), color: 'error' })
    return
  }
  if (!total) {
    toast.add({
      title: 'Aucun prop à convertir',
      description: 'Le catalogue est vide ou en cours de construction.',
      color: 'warning',
    })
    return
  }
  await run(
    'props',
    `/${sid.value}/props/convert`,
    { filter: { named_only: true } },
    `Convertir ${total.toLocaleString('fr-FR')} props ? Les déjà convertis sont refaits avec les réglages actuels ; cela peut durer plusieurs heures.`,
  )
}
const live = (kind: Job['kind']) => jobs.live(sid.value, kind)

interface ExportCard {
  key: string
  kind: Job['kind']
  icon: string
  title: string
  text: string
  label: string
  action: () => void
}
const exports = computed<ExportCard[]>(() => {
  const caps = ov.value?.capabilities ?? []
  const out: ExportCard[] = []
  const q = settingsValues.value?.textures.quality ?? 'max'
  if (caps.includes('props')) {
    out.push({
      key: 'props',
      kind: 'props',
      icon: 'i-ri-box-3-line',
      title: 'Tous les props',
      text: `Le catalogue nommé, qualité « ${q} », collision du jeu.`,
      label: 'Convertir tout',
      action: exportProps,
    })
  }
  if (caps.includes('characters')) {
    out.push({
      key: 'characters',
      kind: 'character',
      icon: 'i-ri-user-3-line',
      title: 'Tous les playermodels',
      text:
        sid.value === '007fl'
          ? 'Chaque famille de tenues devient un playermodel (variations en bodygroups et skins). Ceux déjà créés sont sautés.'
          : 'Chaque personnage devient un playermodel sur le squelette de GMod. Ceux déjà créés sont sautés.',
      label: 'Créer tout',
      action: () =>
        run(
          'characters',
          `/${sid.value}/characters/build-all`,
          { skip_built: true },
          'Créer tous les playermodels restants ? Compte une à deux minutes par personnage.',
        ),
    })
  }
  if (caps.includes('sounds')) {
    out.push({
      key: 'sounds',
      kind: 'sounds',
      icon: 'i-ri-music-2-line',
      title: 'Tous les sons',
      text:
        sid.value === '007fl' || sid.value === 'hitman3'
          ? 'Conversion par le cœur Rust, sans doublons, avec les noms des événements, de la musique et des dialogues.'
          : 'Conversion par le cœur Rust (Bink Audio, Ogg…), sans doublons, rangés comme dans le jeu.',
      label: 'Exporter',
      action: () =>
        run('sounds', `/${sid.value}/sounds/export`, {
          force: forceSounds.value,
          clean: forceSounds.value,
        }),
    })
  }
  return out
})

const deployed = computed(() => ov.value?.addon.deployed)
const rust = computed(() => system.value?.rust)
const rustLabel = computed(() => {
  const r = rust.value
  if (!r) return '…'
  return r.native ? `natif ${r.native_version}` : 'absent'
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
          <UBadge :label="`Rust : ${rustLabel}`" :color="rust?.native ? 'primary' : 'warning'" variant="subtle" icon="i-ri-cpu-line" />
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
        <!-- global exports -->
        <UCard class="lg:col-span-2" :ui="{ body: 'space-y-3 p-4 sm:p-4' }">
          <template #header>
            <div class="flex items-center justify-between gap-2">
              <div>
                <h2 class="text-base font-semibold text-highlighted">Exports globaux</h2>
                <p class="text-sm text-muted">Tout le jeu d’un coup, avec les réglages de la page Réglages. Annulable, reprend là où il s’est arrêté.</p>
              </div>
              <UButton icon="i-ri-settings-3-line" label="Réglages" size="sm" color="neutral" variant="ghost" :to="`/${sid}/settings`" />
            </div>
          </template>
          <div class="grid gap-3 md:grid-cols-3">
            <UCard v-for="e in exports" :key="e.key" :ui="{ body: 'flex h-full flex-col gap-3 p-3 sm:p-3' }">
              <div class="flex items-start gap-2.5">
                <UIcon :name="e.icon" class="mt-0.5 size-5 shrink-0 text-muted" />
                <div class="min-w-0">
                  <p class="font-medium text-highlighted">{{ e.title }}</p>
                  <p class="text-xs text-muted">{{ e.text }}</p>
                </div>
              </div>
              <div class="mt-auto space-y-2">
                <template v-if="live(e.kind)">
                  <OJobProgress :job="live(e.kind)!" />
                  <p v-if="!live(e.kind)!.stage" class="truncate text-xs text-muted">{{ live(e.kind)!.last }}</p>
                  <UButton label="Annuler" icon="i-ri-stop-circle-line" size="xs" color="neutral" variant="outline" block @click="jobs.cancel(live(e.kind)!.id)" />
                </template>
                <template v-else>
                  <USwitch v-if="e.key === 'sounds' && ov?.sounds?.exported" v-model="forceSounds" size="sm" label="Tout réécrire" description="Nouveaux noms et tags ; les anciens fichiers sont retirés." />
                  <UButton :label="e.label" icon="i-ri-download-2-line" color="primary" variant="soft" block :loading="starting === e.key" @click="e.action()" />
                </template>
              </div>
            </UCard>
          </div>
        </UCard>

        <!-- addon -->
        <UCard :ui="{ body: 'space-y-3 p-4 sm:p-4' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Addon Garry’s Mod</h2>
            <p class="text-sm text-muted">Le dossier que GMod charge, lié par une jonction.</p>
          </template>
          <dl v-if="ov" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt class="text-muted">Modèles</dt>
            <dd class="text-right tabular-nums text-toned">{{ n(ov.addon.models) }}</dd>
            <dt class="text-muted">Taille</dt>
            <dd class="text-right tabular-nums text-toned">{{ ov.addon.addon === null ? 'calcul…' : fmtBytes(ov.addon.addon) }}</dd>
            <dt class="text-muted">Paquet .gma</dt>
            <dd class="text-right tabular-nums text-toned">{{ ov.addon.gma ? fmtBytes(ov.addon.gma_bytes) : 'aucun' }}</dd>
          </dl>
          <p v-if="ov" class="truncate font-mono text-xs text-muted" :title="ov.addon.path">{{ ov.addon.path }}</p>
          <div class="flex flex-wrap gap-2">
            <UButton :label="deployed ? 'Délier de GMod' : 'Lier à GMod'" :icon="deployed ? 'i-ri-link-unlink' : 'i-ri-link'" size="sm" :color="deployed ? 'neutral' : 'primary'" :variant="deployed ? 'outline' : 'soft'" @click="async () => { await toggleDeploy(); load() }" />
            <UButton label="Créer le .gma" icon="i-ri-archive-2-line" size="sm" color="neutral" variant="outline" @click="run('gma', `/${sid}/gma`)" />
          </div>
        </UCard>
      </div>

      <div class="grid gap-3 lg:grid-cols-3">
        <!-- system -->
        <UCard class="lg:col-span-2" :ui="{ body: 'grid gap-4 p-4 sm:p-4 md:grid-cols-2' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Système</h2>
            <p class="text-sm text-muted">Le cœur Rust fait le travail lourd (textures, sons, collisions) ; les outils externes servent à compiler et empaqueter.</p>
          </template>
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Outils</h3>
            <ul class="space-y-1.5">
              <li v-for="t in system?.tools ?? []" :key="t.key" class="flex items-center gap-2 text-sm">
                <UIcon :name="t.ok ? 'i-ri-checkbox-circle-fill' : 'i-ri-close-circle-fill'" class="size-4 shrink-0" :class="t.ok ? 'text-success' : 'text-error'" />
                <span class="min-w-0 flex-1 truncate text-toned" :title="t.path">{{ t.label }}</span>
              </li>
            </ul>
            <p v-if="system" class="truncate text-xs text-muted" :title="system.workspace">Espace de travail : {{ system.workspace }} · {{ system.cpus }} cœurs</p>
          </div>
          <div class="space-y-2">
            <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Cœur Rust</h3>
            <p class="text-sm text-toned">omni_native {{ rust?.native_version || '—' }} : textures, sons, collisions, géométrie, paquets du jeu.</p>
            <UAlert
              v-if="rust && !rust.native"
              color="warning"
              variant="subtle"
              icon="i-ri-shield-line"
              title="Module natif indisponible"
              :description="rust.native_error || 'Reconstruis-le : python -m omni native --build'"
            />
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
