<script setup lang="ts">
definePageMeta({ key: (r) => `${r.params.source}/subset` })

/**
 * Extraction ciblée: a text file with one GMod model per line, converted again (same settings) into an addon of its
 * own. The workflow it serves: export everything, sort, note the .mdl worth keeping, extract just those.
 */
interface Entry {
  model: string
  source: string
  kind: 'prop' | 'character' | ''
  key: string
  title: string
  error: string
}
interface Resolved {
  total: number
  found: number
  missing: number
  games: Record<string, { props: number; characters: number }>
  entries: Entry[]
}

const sid = useSourceId()
const jobs = useJobs()
const toast = useToast()
const { sources } = useSources()
const form = usePersisted('subset', { text: '', out: '' })
const resolved = ref<Resolved | null>(null)
const analysing = ref(false)
const starting = ref(false)
const picking = ref(false)
const only = ref<'all' | 'missing'>('all')
const fileInput = useTemplateRef<HTMLInputElement>('file')

const gameTitle = (id: string) => sources.value.find((s) => s.id === id)?.title ?? id

let seq = 0
async function analyse() {
  const mine = ++seq
  if (!form.value.text.trim()) {
    resolved.value = null
    analysing.value = false
    return
  }
  analysing.value = true
  try {
    const r = await api<Resolved>('/subset/resolve', {
      method: 'POST',
      body: { text: form.value.text, source: sid.value },
    })
    if (mine === seq) resolved.value = r
  } catch (e) {
    if (mine === seq) {
      resolved.value = null
      toast.add({ title: 'Liste illisible', description: apiError(e), color: 'warning' })
    }
  } finally {
    if (mine === seq) analysing.value = false
  }
}
const reanalyse = debounce(analyse, 500)
watch(() => form.value.text, reanalyse)
onMounted(analyse)

async function readFile(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (!f) return
  form.value.text = await f.text()
  input.value = ''
}

async function pickFolder() {
  picking.value = true
  try {
    const r = await api<{ path: string }>('/subset/pick', { method: 'POST' })
    if (r.path) form.value.out = r.path
  } catch (e) {
    toast.add({ title: 'Sélecteur indisponible', description: apiError(e), color: 'error' })
  } finally {
    picking.value = false
  }
}

async function start() {
  starting.value = true
  try {
    const { job } = await api<{ job: string }>('/subset/run', {
      method: 'POST',
      body: { text: form.value.text, source: sid.value, out: form.value.out },
    })
    toast.add({
      title: 'Extraction lancée',
      description: 'Les modèles et leurs textures arrivent dans le dossier choisi.',
      icon: 'i-ri-play-large-line',
    })
    await jobs.track(job)
    jobs.open.value = true
  } catch (e) {
    toast.add({ title: 'Extraction impossible', description: apiError(e), color: 'error' })
  } finally {
    starting.value = false
  }
}

const shown = computed(() =>
  (resolved.value?.entries ?? []).filter((e) => only.value === 'all' || e.error),
)
const canStart = computed(() => !!resolved.value?.found && !!form.value.out.trim())
const placeholder = 'models/wissem/007fl/pm/savannah_bloom_hero_bond_male_reg.mdl'
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-10 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto grid max-w-6xl gap-3 lg:grid-cols-[26rem_1fr]">
      <UCard :ui="{ body: 'space-y-5 p-4 sm:p-4', header: 'p-4 sm:px-4', footer: 'p-4 sm:px-4' }">
        <template #header>
          <h1 class="text-base font-semibold text-highlighted">Extraction ciblée</h1>
          <p class="mt-1 text-sm text-muted">
            Une liste de modèles (un chemin <code>.mdl</code> par ligne) : omni les extrait de nouveau, avec leurs textures, dans un addon à part. Pratique pour ne garder que le tri, sans un dossier de 50 Go.
          </p>
        </template>

        <UFormField label="Liste de modèles" description="Un fichier .txt, ou colle les chemins ici. Les lignes # sont ignorées.">
          <div class="space-y-2">
            <div class="flex gap-2">
              <UButton label="Choisir un fichier .txt" icon="i-ri-file-text-line" color="neutral" variant="soft" size="sm" @click="fileInput?.click()" />
              <UButton v-if="form.text" label="Vider" icon="i-ri-close-line" color="neutral" variant="ghost" size="sm" @click="form.text = ''" />
            </div>
            <input ref="file" type="file" accept=".txt,.csv,text/plain" class="hidden" @change="readFile" />
            <UTextarea v-model="form.text" :rows="9" autoresize :maxrows="16" class="w-full font-mono" :placeholder="placeholder" />
          </div>
        </UFormField>

        <UFormField label="Dossier de l’addon" description="Créé s’il n’existe pas ; un dossier déjà rempli est complété.">
          <div class="flex gap-2">
            <UInput v-model="form.out" class="w-full" placeholder="D:\Addons\ma_selection" />
            <UButton icon="i-ri-folder-open-line" color="neutral" variant="soft" :loading="picking" aria-label="Choisir le dossier" @click="pickFolder" />
          </div>
        </UFormField>

        <p class="text-xs text-muted">Mêmes réglages que l’export complet (qualité des textures, collision, LOD…).</p>

        <template #footer>
          <UButton label="Extraire" icon="i-ri-download-2-line" size="lg" block :disabled="!canStart" :loading="starting" @click="start" />
        </template>
      </UCard>

      <UCard :ui="{ root: 'flex min-h-[24rem] flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0', header: 'p-4 sm:px-4' }">
        <template #header>
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-sm font-semibold text-highlighted">Ce que la liste contient</h2>
            <UIcon v-if="analysing" name="i-ri-loader-4-line" class="size-4 animate-spin text-muted" />
            <template v-if="resolved">
              <UBadge :label="`${resolved.found} retrouvé${resolved.found > 1 ? 's' : ''}`" color="success" variant="subtle" size="sm" />
              <UBadge v-if="resolved.missing" :label="`${resolved.missing} introuvable${resolved.missing > 1 ? 's' : ''}`" color="warning" variant="subtle" size="sm" />
              <UBadge v-for="(n, g) in resolved.games" :key="g" :label="`${gameTitle(String(g))} : ${n.props} props, ${n.characters} personnages`" color="neutral" variant="subtle" size="sm" />
              <UTabs v-if="resolved.missing" v-model="only" :items="[{ label: 'Tous', value: 'all' }, { label: 'Introuvables', value: 'missing' }]" :content="false" size="xs" class="ml-auto" />
            </template>
          </div>
        </template>

        <UEmpty v-if="!resolved" icon="i-ri-file-list-3-line" title="Aucune liste" description="Choisis un fichier ou colle des chemins : omni retrouve d’où vient chaque modèle, même s’il n’a jamais été exporté." class="absolute inset-0" />
        <UScrollArea v-else v-slot="{ item }" :items="shown" :virtualize="{ estimateSize: 48, overscan: 12 }" class="absolute inset-0 overflow-y-auto">
          <div class="flex items-center gap-3 border-b border-default px-3 py-2">
            <UIcon :name="(item as Entry).error ? 'i-ri-error-warning-line' : (item as Entry).kind === 'character' ? 'i-ri-user-3-line' : 'i-ri-box-3-line'" class="size-5 shrink-0" :class="(item as Entry).error ? 'text-warning' : 'text-muted'" />
            <div class="min-w-0 flex-1">
              <p class="truncate font-mono text-xs text-highlighted" :title="(item as Entry).model">{{ (item as Entry).model }}</p>
              <p class="truncate text-xs" :class="(item as Entry).error ? 'text-warning' : 'text-muted'">
                {{ (item as Entry).error || `${gameTitle((item as Entry).source)} · ${(item as Entry).kind === 'character' ? 'personnage' : 'prop'}` }}
              </p>
            </div>
          </div>
        </UScrollArea>
      </UCard>
    </div>
  </div>
</template>
