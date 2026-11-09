<script setup lang="ts">
import type { Job, Overview } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/exports` })

/**
 * Everything omni produces from a game, in one place: the Garry's Mod addon (link, .gma, partial addon) and the
 * whole-game exports with their progress. Single items are still exported from their own page.
 */
const sid = useSourceId()
const jobs = useJobs()
const { toggleDeploy } = useSourceDetails()
const { values: settingsValues, load: loadSettings } = useSettings()
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
onMounted(() => Promise.all([load(), loadSettings().catch(() => null)]))
watch(() => jobs.jobs.value.filter((j) => !['queued', 'running'].includes(j.phase)).length, load)

const n = (v?: number | null) => (v ?? 0).toLocaleString('fr-FR')
const caps = computed(() => ov.value?.capabilities ?? [])
const deployed = computed(() => ov.value?.addon.deployed)
const live = (kind: Job['kind']) => jobs.live(sid.value, kind)

const forceSounds = ref(false)
const starting = ref('')
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
  const out: ExportCard[] = []
  const q = settingsValues.value?.textures.quality ?? 'max'
  const extra = [
    settingsValues.value?.props.gltf && '.glb',
    settingsValues.value?.props.blend && '.blend',
  ]
    .filter(Boolean)
    .join(' et ')
  if (caps.value.includes('props')) {
    out.push({
      key: 'props',
      kind: 'props',
      icon: 'i-ri-box-3-line',
      title: 'Tous les props',
      text: `Le catalogue nommé vers Garry’s Mod, qualité « ${q} »${extra ? `, avec un ${extra} par modèle` : ''}.`,
      label: 'Convertir tout',
      action: exportProps,
    })
  }
  if (caps.value.includes('characters')) {
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
  if (caps.value.includes('sounds')) {
    out.push({
      key: 'sounds',
      kind: 'sounds',
      icon: 'i-ri-music-2-line',
      title: 'Tous les sons',
      text: 'Une arborescence nommée (voix par langue et conversation, effets par événement), sans doublons, avec un index.',
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

const SINGLE = computed(
  () =>
    [
      caps.value.includes('props') && {
        icon: 'i-ri-box-3-line',
        text: 'Un prop, en .mdl ou en .glb : depuis son inspecteur, page Modèles.',
        to: `/${sid.value}/models?tab=props`,
      },
      caps.value.includes('textures') && {
        icon: 'i-ri-image-2-line',
        text: 'Une texture en PNG pleine résolution : page Textures.',
        to: `/${sid.value}/textures`,
      },
      caps.value.includes('sounds') && {
        icon: 'i-ri-music-2-line',
        text: 'Un son, ou les sons d’un filtre et d’un format choisis : page Sons.',
        to: `/${sid.value}/sounds`,
      },
    ].filter(Boolean) as { icon: string; text: string; to: string }[],
)
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-8 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-6xl space-y-3">
      <div class="flex flex-wrap items-center gap-3">
        <div class="min-w-0 flex-1">
          <h1 class="text-xl font-semibold text-highlighted">Exports</h1>
          <p class="text-sm text-muted">Ce qu’omni produit à partir de {{ ov?.title ?? 'ce jeu' }} : l’addon Garry’s Mod, les modèles glTF et les sons.</p>
        </div>
        <UButton icon="i-ri-settings-3-line" label="Réglages de conversion" size="sm" color="neutral" variant="outline" to="/settings" />
      </div>

      <UAlert v-if="failed" color="error" variant="subtle" icon="i-ri-error-warning-line" title="Exports indisponibles" :description="failed" />

      <div class="grid gap-3 lg:grid-cols-3">
        <!-- the Garry's Mod addon -->
        <UCard class="lg:col-span-2" :ui="{ body: 'grid gap-4 p-4 sm:p-4 md:grid-cols-2' }">
          <template #header>
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 class="text-base font-semibold text-highlighted">Addon Garry’s Mod</h2>
                <p class="text-sm text-muted">Le dossier que GMod charge, lié par une jonction.</p>
              </div>
              <UBadge :label="deployed ? 'Lié à GMod' : 'Non lié'" :color="deployed ? 'success' : 'neutral'" variant="subtle" :icon="deployed ? 'i-ri-link' : 'i-ri-link-unlink'" />
            </div>
          </template>
          <div class="min-w-0 space-y-3">
            <USkeleton v-if="!ov" class="h-20" />
            <dl v-else class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt class="text-muted">Modèles</dt>
              <dd class="text-right tabular-nums text-toned">{{ n(ov.addon.models) }}</dd>
              <dt class="text-muted">Taille</dt>
              <dd class="text-right tabular-nums text-toned">{{ ov.addon.addon === null ? 'calcul…' : fmtBytes(ov.addon.addon) }}</dd>
              <dt class="text-muted">Paquet .gma</dt>
              <dd class="text-right tabular-nums text-toned">{{ ov.addon.gma ? fmtBytes(ov.addon.gma_bytes) : 'aucun' }}</dd>
            </dl>
            <p v-if="ov" class="truncate font-mono text-xs text-muted" :title="ov.addon.path">{{ ov.addon.path }}</p>
          </div>
          <div class="flex min-w-0 flex-col gap-2">
            <UButton :label="deployed ? 'Délier de GMod' : 'Lier à GMod'" :icon="deployed ? 'i-ri-link-unlink' : 'i-ri-link'" :color="deployed ? 'neutral' : 'primary'" :variant="deployed ? 'outline' : 'soft'" block @click="async () => { await toggleDeploy(); load() }" />
            <UButton label="Créer le .gma" icon="i-ri-archive-2-line" color="neutral" variant="outline" block :loading="starting === 'gma'" @click="run('gma', `/${sid}/gma`)" />
            <UButton label="Voir dans la visionneuse" icon="i-ri-eye-line" color="neutral" variant="ghost" block :to="`/${sid}/viewer`" />
          </div>
        </UCard>

        <!-- partial addon -->
        <UCard :ui="{ root: 'flex flex-col', body: 'flex flex-1 flex-col gap-3 p-4 sm:p-4' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Addon partiel</h2>
            <p class="text-sm text-muted">Un addon séparé avec seulement les modèles d’une liste (un chemin .mdl par ligne), pour un serveur ou une collection.</p>
          </template>
          <UButton label="Préparer une sélection" icon="i-ri-scissors-cut-line" color="neutral" variant="soft" block class="mt-auto" :to="`/${sid}/subset`" />
        </UCard>
      </div>

      <!-- whole-game exports -->
      <UCard :ui="{ body: 'space-y-3 p-4 sm:p-4' }">
        <template #header>
          <h2 class="text-base font-semibold text-highlighted">Le jeu entier</h2>
          <p class="text-sm text-muted">Avec les réglages de conversion actuels. Annulable : un export interrompu reprend là où il s’est arrêté.</p>
        </template>
        <div class="grid gap-3 md:grid-cols-3">
          <template v-if="!ov">
            <USkeleton v-for="i in 3" :key="i" class="h-40" />
          </template>
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

      <!-- single items -->
      <UCard v-if="SINGLE.length" :ui="{ body: 'p-2 sm:p-2' }">
        <template #header>
          <h2 class="text-base font-semibold text-highlighted">Un élément à la fois</h2>
        </template>
        <ul>
          <li v-for="s in SINGLE" :key="s.to">
            <NuxtLink :to="s.to" class="flex items-center gap-3 rounded-md px-2 py-2 text-sm text-toned hover:bg-elevated">
              <UIcon :name="s.icon" class="size-4 shrink-0 text-muted" />
              <span class="min-w-0 flex-1">{{ s.text }}</span>
              <UIcon name="i-ri-arrow-right-line" class="size-4 shrink-0 text-muted" />
            </NuxtLink>
          </li>
        </ul>
      </UCard>
    </div>
  </div>
</template>
