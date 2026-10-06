<script setup lang="ts">
/**
 * Jeux : the library. Give omni a game folder (or take one Steam found): it works out the game and engine, then
 * prepares everything else on its own (tools, the game's structures, indexes, catalogs) as a background job.
 * Games stay separate: each has its own data, exports and addon.
 */
definePageMeta({ key: 'games' })

interface Step {
  key: string
  label: string
  ok: boolean
}
interface GameStatus {
  steps: Step[]
  ready: boolean
  updated?: boolean
  builtin?: boolean
  reason?: string
}
interface GameInfo {
  id: string
  title: string
  engine: 'glacier' | 'unreal'
  profile: string
  root: string
  version?: string
  supported: boolean
  reason?: string
  capabilities: string[]
  ready?: boolean
  status?: GameStatus
}

const toast = useToast()
const confirm = useConfirm()
const route = useRoute()
const { jobs } = useJobs()
const { load: loadSources } = useSources()
const games = ref<GameInfo[]>([])
const candidates = ref<GameInfo[]>([])
const loading = ref(true)
const busy = ref('')

async function load() {
  try {
    const r = await api<{ games: GameInfo[]; candidates: GameInfo[] }>('/games')
    games.value = r.games
    candidates.value = r.candidates
  } catch (e) {
    toast.add({ title: 'Bibliothèque indisponible', description: apiError(e), color: 'error' })
  } finally {
    loading.value = false
  }
}
onMounted(load)

/** The preparation job of a game, while it is queued or running. */
function preparing(id: string) {
  return jobs.value.find(
    (j) => j.kind === 'setup' && j.source === id && ['queued', 'running'].includes(j.phase),
  )
}
// when a preparation ends, the library (and the source list of the menu) is read again
watch(
  () => games.value.map((g) => !!preparing(g.id)).join(),
  async (now, before) => {
    if (before && before.includes('true') && !now.includes('true')) {
      await load()
      await loadSources(true).catch(() => [])
    }
  },
)

async function add(path?: string) {
  busy.value = path ?? 'pick'
  try {
    const r = path
      ? await api<GameInfo & { job?: string }>('/games', { method: 'POST', body: { path } })
      : await api<(GameInfo & { job?: string }) | { cancelled: true }>('/games/pick', {
          method: 'POST',
        })
    if ('cancelled' in r) return
    toast.add({
      title: `${r.title} ajouté`,
      description: r.job
        ? 'omni prépare le jeu : tout ce qu’il faut est récupéré automatiquement.'
        : undefined,
      icon: 'i-ri-gamepad-line',
      color: 'success',
    })
    await load()
  } catch (e) {
    toast.add({ title: 'Jeu non reconnu', description: apiError(e), color: 'error' })
  } finally {
    busy.value = ''
  }
}

async function prepare(g: GameInfo) {
  busy.value = g.id
  try {
    await api(`/games/${g.id}/prepare`, { method: 'POST' })
  } catch (e) {
    toast.add({ title: 'Préparation impossible', description: apiError(e), color: 'error' })
  } finally {
    busy.value = ''
  }
}

async function remove(g: GameInfo) {
  if (
    !(await confirm({
      title: `Retirer ${g.title} ?`,
      description:
        'Il disparaît de la bibliothèque. Ses fichiers et exports ne sont pas supprimés.',
      confirmLabel: 'Retirer',
      destructive: true,
    }))
  )
    return
  await api(`/games/${g.id}`, { method: 'DELETE' }).catch((e) =>
    toast.add({ title: 'Impossible', description: apiError(e), color: 'error' }),
  )
  await load()
  await loadSources(true).catch(() => [])
}

const CAP: Record<string, string> = {
  props: 'Props',
  characters: 'Personnages',
  textures: 'Textures',
  sounds: 'Sons',
}
const ENGINE = { glacier: 'Glacier', unreal: 'Unreal Engine' } as const
const focus = computed(() => String(route.query.game ?? ''))
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-10 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-3xl space-y-4">
      <div class="flex flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <h1 class="text-2xl font-semibold text-highlighted">Jeux</h1>
          <p class="text-sm text-muted">Donne le dossier d’un jeu : omni reconnaît le moteur et prépare tout le reste.</p>
        </div>
        <UButton label="Ajouter un dossier" icon="i-ri-folder-add-line" :loading="busy === 'pick'" @click="add()" />
      </div>

      <USkeleton v-if="loading" class="h-24 w-full" />
      <UEmpty
        v-else-if="!games.length"
        icon="i-ri-gamepad-line"
        title="Aucun jeu"
        description="Ajoute le dossier d’un jeu ou prends-en un détecté dans Steam ci-dessous."
      />
      <div v-else class="space-y-3">
        <UCard
          v-for="g in games"
          :key="g.id"
          :ui="{ body: 'p-4' }"
          :class="focus === g.id ? 'ring-2 ring-primary' : ''"
        >
          <div class="flex flex-wrap items-start gap-3">
            <UIcon name="i-ri-gamepad-line" class="mt-1 size-6 shrink-0 text-muted" />
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="font-semibold text-highlighted">{{ g.title }}</h2>
                <UBadge :label="ENGINE[g.engine]" color="neutral" variant="subtle" size="sm" />
                <UBadge v-if="g.version" :label="`UE ${g.version}`" color="neutral" variant="subtle" size="sm" />
                <UBadge v-if="g.ready" label="Prêt" color="success" variant="subtle" size="sm" />
                <UBadge v-else-if="preparing(g.id)" label="Préparation…" color="primary" variant="subtle" size="sm" />
                <UBadge v-else-if="!g.supported" label="Non pris en charge" color="warning" variant="subtle" size="sm" />
                <UBadge v-else-if="g.status?.updated" label="Mis à jour : à préparer" color="warning" variant="subtle" size="sm" />
                <UBadge v-else label="À préparer" color="warning" variant="subtle" size="sm" />
              </div>
              <p class="mt-0.5 truncate text-xs text-muted">{{ g.root }}</p>
              <p v-if="!g.supported && g.reason" class="mt-1 text-xs text-warning">{{ g.reason }}</p>
              <div v-if="g.capabilities.length && g.supported" class="mt-2 flex flex-wrap gap-1.5">
                <UBadge v-for="c in g.capabilities" :key="c" :label="CAP[c] ?? c" color="primary" variant="soft" size="sm" />
              </div>
              <div v-if="g.status?.steps?.length && !g.ready" class="mt-3 space-y-1">
                <div v-for="s in g.status.steps" :key="s.key" class="flex items-center gap-2 text-xs">
                  <UIcon
                    :name="s.ok ? 'i-ri-checkbox-circle-fill' : 'i-ri-checkbox-blank-circle-line'"
                    :class="s.ok ? 'text-success' : 'text-muted'"
                  />
                  <span :class="s.ok ? 'text-default' : 'text-muted'">{{ s.label }}</span>
                </div>
              </div>
              <div v-if="preparing(g.id)" class="mt-3 space-y-1">
                <UProgress
                  :model-value="preparing(g.id)!.total ? (100 * preparing(g.id)!.done) / preparing(g.id)!.total : null"
                  size="sm"
                />
                <p class="truncate text-xs text-muted">{{ preparing(g.id)!.last || 'En attente…' }}</p>
              </div>
            </div>
            <div class="flex gap-1">
              <UButton v-if="g.ready" label="Ouvrir" icon="i-ri-arrow-right-line" size="sm" :to="`/${g.id}`" />
              <UButton
                v-else-if="g.supported && g.id !== '007fl' && !preparing(g.id)"
                label="Préparer"
                icon="i-ri-magic-line"
                size="sm"
                :loading="busy === g.id"
                @click="prepare(g)"
              />
              <UButton v-else-if="g.id === '007fl' && !g.ready" label="Installer" icon="i-ri-install-line" size="sm" to="/setup" />
              <UButton icon="i-ri-delete-bin-line" size="sm" color="neutral" variant="ghost" aria-label="Retirer le jeu" @click="remove(g)" />
            </div>
          </div>
        </UCard>
      </div>

      <template v-if="candidates.length">
        <h2 class="px-1 pt-2 text-sm font-semibold uppercase tracking-wide text-muted">Détectés dans Steam</h2>
        <UCard v-for="g in candidates" :key="g.id" :ui="{ body: 'p-3 sm:p-3' }">
          <div class="flex items-center gap-3">
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium text-highlighted">
                {{ g.title }}
                <span class="text-xs text-muted">· {{ ENGINE[g.engine] }}<template v-if="g.version"> {{ g.version }}</template></span>
              </p>
              <p class="truncate text-xs text-muted">{{ g.root }}</p>
              <p v-if="!g.supported && g.reason" class="truncate text-xs text-warning">{{ g.reason }}</p>
            </div>
            <UButton
              label="Ajouter"
              icon="i-ri-add-line"
              size="sm"
              color="neutral"
              variant="soft"
              :disabled="!g.supported"
              :loading="busy === g.root"
              @click="add(g.root)"
            />
          </div>
        </UCard>
      </template>
    </div>
  </div>
</template>
