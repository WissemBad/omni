<script setup lang="ts">
/**
 * Jeux : the library. Pick a game folder (or take one Steam found) and omni works out which game and engine it is;
 * what it can convert is shown per game. Games stay separate: each has its own data, exports and addon.
 */
definePageMeta({ key: 'games' })

interface GameInfo {
  id: string
  title: string
  engine: 'glacier' | 'unreal'
  profile: string
  root: string
  version?: string
  supported: boolean
  capabilities: string[]
  ready?: boolean
}

const toast = useToast()
const confirm = useConfirm()
const games = ref<GameInfo[]>([])
const candidates = ref<GameInfo[]>([])
const loading = ref(true)
const busy = ref('')

async function load() {
  loading.value = true
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

async function add(path?: string) {
  busy.value = path ?? 'pick'
  try {
    const r = path
      ? await api<GameInfo>('/games', { method: 'POST', body: { path } })
      : await api<GameInfo | { cancelled: true }>('/games/pick', { method: 'POST' })
    if ('cancelled' in r) return
    toast.add({ title: `${r.title} ajouté`, icon: 'i-ri-gamepad-line', color: 'success' })
    await load()
  } catch (e) {
    toast.add({ title: 'Jeu non reconnu', description: apiError(e), color: 'error' })
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
}

const CAP: Record<string, string> = {
  props: 'Props',
  characters: 'Personnages',
  textures: 'Textures',
  sounds: 'Sons',
}
const ENGINE = { glacier: 'Glacier', unreal: 'Unreal Engine' } as const
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-10 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-3xl space-y-4">
      <div class="flex flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <h1 class="text-2xl font-semibold text-highlighted">Jeux</h1>
          <p class="text-sm text-muted">Donne le dossier d’un jeu : omni reconnaît le moteur et prépare le reste.</p>
        </div>
        <UButton label="Ajouter un dossier" icon="i-ri-folder-add-line" :loading="busy === 'pick'" @click="add()" />
      </div>

      <USkeleton v-if="loading" class="h-24 w-full" />
      <UEmpty v-else-if="!games.length" icon="i-ri-gamepad-line" title="Aucun jeu" description="Ajoute le dossier d’un jeu ou prends-en un détecté dans Steam ci-dessous." />
      <div v-else class="space-y-3">
        <UCard v-for="g in games" :key="g.id" :ui="{ body: 'p-4' }">
          <div class="flex flex-wrap items-start gap-3">
            <UIcon name="i-ri-gamepad-line" class="mt-1 size-6 shrink-0 text-muted" />
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="font-semibold text-highlighted">{{ g.title }}</h2>
                <UBadge :label="ENGINE[g.engine]" color="neutral" variant="subtle" size="sm" />
                <UBadge v-if="g.version" :label="`UE ${g.version}`" color="neutral" variant="subtle" size="sm" />
                <UBadge v-if="g.ready" label="Prêt" color="success" variant="subtle" size="sm" />
                <UBadge v-else-if="!g.supported" label="Bientôt pris en charge" color="warning" variant="subtle" size="sm" />
              </div>
              <p class="mt-0.5 truncate text-xs text-muted">{{ g.root }}</p>
              <div v-if="g.capabilities.length" class="mt-2 flex flex-wrap gap-1.5">
                <UBadge v-for="c in g.capabilities" :key="c" :label="CAP[c] ?? c" color="primary" variant="soft" size="sm" />
              </div>
            </div>
            <div class="flex gap-1">
              <UButton v-if="g.ready" label="Ouvrir" icon="i-ri-arrow-right-line" size="sm" :to="`/${g.id}`" />
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
              <p class="truncate font-medium text-highlighted">{{ g.title }} <span class="text-xs text-muted">· {{ ENGINE[g.engine] }}<template v-if="g.version"> {{ g.version }}</template></span></p>
              <p class="truncate text-xs text-muted">{{ g.root }}</p>
            </div>
            <UButton label="Ajouter" icon="i-ri-add-line" size="sm" color="neutral" variant="soft" :loading="busy === g.root" @click="add(g.root)" />
          </div>
        </UCard>
      </template>
    </div>
  </div>
</template>
