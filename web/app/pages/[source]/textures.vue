<script setup lang="ts">
import type { TexEntry } from '~/components/OTextureViewer.vue'
import type { Channel } from '~/utils/textures'
import type { Category, GameTexture, TextureDetail, TexturePage } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/textures` })

/**
 * Textures: every texture of the game (format, size, role), a large preview by channel, and who uses it: the
 * materials (with their slot) and the models, with links to the Models workbench and the converted output.
 */
const sid = useSourceId()
const route = useRoute()
const router = useRouter()

const q = ref(String(route.query.q ?? ''))
const folder = ref(String(route.query.folder ?? ''))
const fmt = ref(String(route.query.fmt ?? ''))
const role = ref(String(route.query.role ?? ''))
const usage = ref(String(route.query.usage ?? ''))
const minSize = ref(Number(route.query.min ?? 0))
const sort = ref(String(route.query.sort ?? 'name'))

const categories = ref<Category[]>([])
const items = ref<GameTexture[]>([])
const total = ref(0)
const facets = ref<TexturePage['facets']>({ fmt: [], role: [] })
const loading = ref(false)
const loaded = ref(false)
const building = ref(false)
const progress = ref('')
const failed = ref('')
const treeOpen = ref(false)
const inspectorOpen = ref(false)
const mobilePreview = ref(false)
const active = ref<GameTexture | null>(null)
const detail = ref<TextureDetail | null>(null)
const channel = ref<Channel>('rgb')
const bg = usePersisted('tex-bg', { value: 'checker' as 'checker' | 'dark' | 'light' })
const search = useTemplateRef<{ inputRef?: HTMLInputElement }>('search')
const scroll = useTemplateRef<{
  $el: HTMLElement
  virtualizer?: { scrollToIndex: (i: number, o?: object) => void }
}>('scroll')

let seq = 0
let waitTimer: ReturnType<typeof setTimeout> | undefined
async function fetchPage(reset: boolean) {
  const mine = ++seq
  loading.value = true
  failed.value = ''
  try {
    const params = new URLSearchParams({
      q: q.value,
      folder: folder.value,
      fmt: fmt.value,
      role: role.value,
      usage: usage.value,
      min_size: String(minSize.value),
      sort: sort.value,
      limit: '200',
      offset: reset ? '0' : String(items.value.length),
    })
    const r = await api<TexturePage>(`/${sid.value}/textures?${params}`)
    if (mine !== seq) return
    building.value = r.building
    progress.value = r.progress ?? ''
    if (r.building) {
      clearTimeout(waitTimer)
      waitTimer = setTimeout(() => fetchPage(true), 2000)
      return
    }
    if (!categories.value.length)
      categories.value = await api<Category[]>(`/${sid.value}/textures/categories`).catch(() => [])
    total.value = r.total
    facets.value = r.facets
    items.value = reset ? r.items : [...items.value, ...r.items]
    if (reset) scroll.value?.$el?.scrollTo({ top: 0 })
    await nextTick()
    more()
  } catch (e) {
    if (mine === seq) failed.value = apiError(e)
  } finally {
    if (mine === seq) {
      loading.value = false
      loaded.value = true
    }
  }
}
onBeforeUnmount(() => clearTimeout(waitTimer))

function more() {
  const el = scroll.value?.$el
  if (!el || loading.value || items.value.length >= total.value) return
  if (el.scrollTop + el.clientHeight > el.scrollHeight - 900) fetchPage(false)
}

const query = () => ({
  ...(q.value && { q: q.value }),
  ...(folder.value && { folder: folder.value }),
  ...(fmt.value && { fmt: fmt.value }),
  ...(role.value && { role: role.value }),
  ...(usage.value && { usage: usage.value }),
  ...(minSize.value && { min: String(minSize.value) }),
  ...(sort.value !== 'name' && { sort: sort.value }),
  ...(active.value && { k: active.value.key }),
})
const sync = () => route.path.endsWith('/textures') && router.replace({ query: query() })
const reload = debounce(() => fetchPage(true), 220)
watch([q, folder, fmt, role, usage, minSize, sort], () => {
  reload()
  sync()
})

let pick = 0
async function select(t: GameTexture) {
  const mine = ++pick
  active.value = t
  mobilePreview.value = true
  sync()
  try {
    const d = await api<TextureDetail>(`/${sid.value}/textures/${t.key}`)
    if (mine === pick) detail.value = d
  } catch {
    if (mine === pick) detail.value = null
  }
}

async function openKey(k: string) {
  if (!k || k === active.value?.key) return
  const found = items.value.find((i) => i.key === k)
  if (found) return select(found)
  const d = await api<TextureDetail>(`/${sid.value}/textures/${k}`).catch(() => null)
  if (d) select({ ...d, materials: d.materials.length, models: d.model_count })
}

onMounted(async () => {
  await fetchPage(true)
  await openKey(String(route.query.k ?? ''))
})
// links from the other workbenches (inspectors, viewer) land here with ?k=<texture>
watch(
  () => route.query.k,
  (k) => openKey(String(k ?? '')),
)

const activeIndex = computed(() => items.value.findIndex((t) => t.key === active.value?.key))
function step(delta: number) {
  if (!items.value.length) return
  const i = Math.min(
    items.value.length - 1,
    Math.max(0, (activeIndex.value < 0 ? (delta > 0 ? -1 : 1) : activeIndex.value) + delta),
  )
  const t = items.value[i]
  if (t) select(t)
  scroll.value?.virtualizer?.scrollToIndex(i, { align: 'auto' })
  more()
}

// ---- full view
const viewerOpen = ref(false)
const entries = computed<TexEntry[]>(() => {
  const t = active.value
  if (!t) return []
  return [
    {
      id: t.key,
      label: texRoleLabel(t.role),
      name: t.name,
      width: t.width,
      height: t.height,
      src: (ch: Channel, size: number) =>
        gameTextureUrl(sid.value, t.key, { size, channel: ch, role: t.role }),
      info: [
        ['Dimensions', dimsLabel(t.width, t.height)],
        ['Format du jeu', t.fmt],
        ['Niveaux de mip', t.mips],
        ['Poids (jeu)', fmtBytes(t.bytes)],
        ['Clé', t.key],
      ],
      download: `/api/${sid.value}/textures/${t.key}/download`,
    },
  ]
})

async function exportPng() {
  if (!active.value) return
  try {
    const r = await api<{ path: string }>(`/${sid.value}/textures/${active.value.key}/export`, {
      method: 'POST',
    })
    useToast().add({ title: 'Texture exportée', description: r.path, icon: 'i-ri-download-2-line' })
  } catch (e) {
    useToast().add({ title: 'Export impossible', description: apiError(e), color: 'error' })
  }
}

const stageStyle = computed(() => {
  if (bg.value.value === 'dark') return { background: '#0b0b10' }
  if (bg.value.value === 'light') return { background: '#f4f4f7' }
  return {
    backgroundColor: '#2a2a33',
    backgroundImage: 'conic-gradient(#3a3a46 25%, transparent 0 50%, #3a3a46 0 75%, transparent 0)',
    backgroundSize: '20px 20px',
  }
})

const CHANNELS = [
  { label: 'Couleur', value: 'rgb' },
  { label: 'Alpha+', value: 'rgba' },
  { label: 'R', value: 'r' },
  { label: 'V', value: 'g' },
  { label: 'B', value: 'b' },
  { label: 'A', value: 'a' },
] as const
const SORTS = [
  { label: 'Dossier et nom', value: 'name' },
  { label: 'Plus lourdes', value: 'size' },
  { label: 'Plus grandes', value: 'dims' },
  { label: 'Plus utilisées', value: 'used' },
]
const USAGES = [
  { label: 'Toutes', value: '' },
  { label: 'Utilisées', value: 'used' },
  { label: 'Inutilisées', value: 'unused' },
  { label: 'Nommées', value: 'named' },
]
const SIZES = [
  { label: 'Toute taille', value: 0 },
  { label: '≥ 512', value: 512 },
  { label: '≥ 1024', value: 1024 },
  { label: '≥ 2048', value: 2048 },
  { label: '4096 et plus', value: 4096 },
]
const fmtItems = computed(() => [
  { label: 'Tous les formats', value: '' },
  ...facets.value.fmt.map((f) => ({
    label: `${f.value} (${f.n.toLocaleString('fr-FR')})`,
    value: f.value,
  })),
])
const roleItems = computed(() => [
  { label: 'Tous les rôles', value: '' },
  ...facets.value.role.map((f) => ({
    label: `${texRoleLabel(f.value)} (${f.n.toLocaleString('fr-FR')})`,
    value: f.value,
  })),
])

defineShortcuts({
  '/': () => search.value?.inputRef?.focus(),
  arrowdown: { usingInput: true, handler: () => step(1) },
  arrowup: { usingInput: true, handler: () => step(-1) },
  f: () => active.value && (viewerOpen.value = true),
  escape: () => (mobilePreview.value = false),
})
</script>

<template>
  <div class="flex h-full gap-3 px-3 pb-3 pt-[4.75rem] sm:pt-20">
    <UCard class="w-60 shrink-0 max-2xl:hidden" :ui="{ root: 'flex min-h-0 flex-col', body: 'min-h-0 flex-1 p-2 sm:p-2' }">
      <OCategoryTree v-model="folder" :categories="categories" root-label="Toutes les textures" />
    </UCard>

    <!-- results -->
    <UCard class="w-full min-w-0 lg:w-[23rem] lg:shrink-0" :ui="panelUi">
      <template #header>
        <UInput ref="search" v-model="q" icon="i-ri-search-line" placeholder="Rechercher une texture, un hash…" size="lg" class="w-full" :loading="loading">
          <template #trailing>
            <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
            <UKbd v-else value="/" />
          </template>
        </UInput>
        <div class="grid grid-cols-2 gap-2">
          <USelectMenu v-model="fmt" :items="fmtItems" size="sm" class="w-full" value-key="value" :search-input="false" />
          <USelectMenu v-model="role" :items="roleItems" size="sm" class="w-full" value-key="value" :search-input="false" />
          <USelectMenu v-model="usage" :items="USAGES" size="sm" class="w-full" value-key="value" :search-input="false" />
          <USelect v-model="minSize" :items="SIZES" size="sm" class="w-full" />
        </div>
        <div class="flex items-center gap-2">
          <UButton class="2xl:hidden" icon="i-ri-folder-3-line" label="Dossiers" size="xs" color="neutral" variant="outline" @click="treeOpen = true" />
          <UBadge v-if="folder" :label="titleCase(leaf(folder))" color="primary" variant="soft" class="max-w-32">
            <template #trailing>
              <UButton icon="i-ri-close-line" color="primary" variant="link" size="xs" class="-mr-1 p-0" aria-label="Retirer le filtre" @click="folder = ''" />
            </template>
          </UBadge>
          <USelect v-model="sort" :items="SORTS" size="xs" class="ml-auto w-40" icon="i-ri-sort-desc" />
        </div>
        <p class="text-xs tabular-nums text-muted">{{ total.toLocaleString('fr-FR') }} texture{{ total > 1 ? 's' : '' }}</p>
      </template>

      <UEmpty v-if="failed" icon="i-ri-error-warning-line" title="Chargement impossible" :description="failed" class="absolute inset-0" :actions="[{ label: 'Réessayer', onClick: () => fetchPage(true) }]" />
      <UEmpty v-else-if="building" icon="i-ri-database-2-line" title="Indexation des textures…" :description="progress || 'Première ouverture : lecture des en-têtes et des matériaux (une demi-minute).'" class="absolute inset-0" />
      <UEmpty
        v-else-if="!items.length && loaded && !loading"
        icon="i-ri-search-eye-line"
        title="Aucune texture"
        description="Essaie un autre mot, ou retire un filtre."
        class="absolute inset-0"
        :actions="[{ label: 'Réinitialiser', color: 'neutral', variant: 'outline', onClick: () => { q = ''; folder = ''; fmt = ''; role = ''; usage = ''; minSize = 0 } }]"
      />
      <div v-else-if="!items.length" class="space-y-2 p-3">
        <USkeleton v-for="n in 9" :key="n" class="h-14 w-full" />
      </div>
      <UScrollArea
        v-else
        ref="scroll"
        v-slot="{ item }"
        :items="items"
        :virtualize="{ estimateSize: 62, overscan: 10, getItemKey: (i: number) => items[i]?.key ?? i }"
        class="absolute inset-0 overflow-y-auto"
        @scroll="(s: boolean) => !s && more()"
      >
        <div
          role="option"
          :aria-selected="active?.key === (item as GameTexture).key"
          class="flex cursor-pointer items-center gap-3 border-b border-default px-3 py-2 transition-colors"
          :class="active?.key === (item as GameTexture).key ? 'bg-primary/10' : 'hover:bg-elevated'"
          @click="select(item as GameTexture)"
        >
          <img
            :src="gameTextureUrl(sid, (item as GameTexture).key, { size: 64, role: (item as GameTexture).role })"
            alt=""
            loading="lazy"
            class="size-11 shrink-0 rounded-md border border-default bg-elevated object-cover"
          />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium text-highlighted">{{ (item as GameTexture).name }}</p>
            <p class="truncate text-xs text-muted">{{ texRoleLabel((item as GameTexture).role) }} · {{ crumb((item as GameTexture).folder + '/x') || 'Sans dossier' }}</p>
          </div>
          <div class="shrink-0 text-right text-xs tabular-nums text-muted">
            <p>{{ (item as GameTexture).width }}²</p>
            <p>{{ (item as GameTexture).fmt }}</p>
          </div>
        </div>
      </UScrollArea>
    </UCard>

    <!-- preview -->
    <UCard
      class="min-w-0 flex-1 max-lg:fixed max-lg:inset-x-3 max-lg:bottom-3 max-lg:top-[4.75rem] max-lg:z-30"
      :class="!mobilePreview && 'max-lg:hidden'"
      :ui="{ root: 'relative flex flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }"
    >
      <div v-if="active" class="absolute inset-0 overflow-hidden" :style="stageStyle">
        <img :key="active.key + channel" :src="gameTextureUrl(sid, active.key, { size: 2048, channel, role: active.role })" alt="" class="size-full object-contain p-6" />
      </div>
      <div class="pointer-events-none absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-3">
        <WGlassCard v-if="active" :halo="false" class="pointer-events-auto min-w-0 max-w-md" body-class="p-3 sm:p-3">
          <p class="truncate font-semibold text-highlighted">{{ active.name }}</p>
          <p class="truncate text-xs text-muted">{{ crumb(active.folder + '/x') || 'Sans dossier' }}</p>
          <div class="mt-2 flex flex-wrap gap-1.5">
            <UBadge :label="texRoleLabel(active.role)" color="primary" variant="subtle" size="sm" />
            <UBadge :label="`${active.width} × ${active.height}`" color="neutral" variant="subtle" size="sm" />
            <UBadge :label="active.fmt" color="neutral" variant="subtle" size="sm" />
            <UBadge :label="fmtBytes(active.bytes)" color="neutral" variant="subtle" size="sm" />
          </div>
        </WGlassCard>
        <div class="pointer-events-auto flex flex-col items-end gap-2">
          <UButton class="lg:hidden" icon="i-ri-close-line" color="neutral" variant="subtle" aria-label="Fermer l’aperçu" @click="mobilePreview = false" />
          <WGlassCard v-if="active" :halo="false" body-class="flex flex-col gap-0.5 p-1 sm:p-1">
            <UTooltip text="Plein écran (zoom, UV)" :kbds="['F']" :content="{ side: 'left' }">
              <UButton icon="i-ri-fullscreen-line" size="sm" color="neutral" variant="ghost" aria-label="Plein écran" @click="viewerOpen = true" />
            </UTooltip>
            <UTooltip text="Télécharger en PNG" :content="{ side: 'left' }">
              <UButton icon="i-ri-download-2-line" size="sm" color="neutral" variant="ghost" aria-label="Télécharger" :to="`/api/${sid}/textures/${active.key}/download`" external target="_blank" />
            </UTooltip>
            <UTooltip text="Détails" :content="{ side: 'left' }">
              <UButton class="xl:hidden" icon="i-ri-layout-right-line" size="sm" color="neutral" variant="ghost" aria-label="Ouvrir l’inspecteur" @click="inspectorOpen = true" />
            </UTooltip>
          </WGlassCard>
        </div>
      </div>
      <div v-if="active" class="pointer-events-none absolute inset-x-0 bottom-3 z-10 flex justify-center px-3">
        <div class="wi-glass wi-glass--pill pointer-events-auto flex items-center gap-0.5 p-1">
          <UButton v-for="c in CHANNELS" :key="c.value" :label="c.label" size="sm" :color="channel === c.value ? 'primary' : 'neutral'" :variant="channel === c.value ? 'soft' : 'ghost'" @click="channel = c.value" />
          <span class="mx-0.5 h-4 w-px bg-default" />
          <UButton icon="i-ri-contrast-2-line" size="sm" color="neutral" variant="ghost" aria-label="Fond" @click="bg.value = bg.value === 'checker' ? 'dark' : bg.value === 'dark' ? 'light' : 'checker'" />
        </div>
      </div>
      <div v-else class="flex h-full items-center justify-center p-6">
        <UEmpty icon="i-ri-image-2-line" title="Choisis une texture" description="Toutes les textures du jeu, avec leurs canaux et les matériaux et modèles qui les utilisent." />
      </div>
    </UCard>

    <!-- inspector -->
    <UCard class="w-[24rem] shrink-0 max-xl:hidden 2xl:w-[26rem]" :ui="{ root: 'relative flex min-h-0 flex-col', body: 'relative min-h-0 flex-1 overflow-y-auto p-3 sm:p-3' }">
      <OTextureInspector v-if="detail && active" :detail="detail" @export="exportPng" @full="viewerOpen = true" />
      <UEmpty v-else icon="i-ri-stethoscope-line" title="Inspecteur" description="Format, poids, conversion vers Source, matériaux et modèles qui utilisent la texture." class="absolute inset-0" />
    </UCard>

    <USlideover v-model:open="inspectorOpen" side="right" title="Détails de la texture" :ui="{ content: 'max-w-md' }">
      <template #body>
        <OTextureInspector v-if="detail && active" :detail="detail" @export="exportPng" @full="viewerOpen = true" />
      </template>
    </USlideover>
    <USlideover v-model:open="treeOpen" side="left" title="Dossiers" :ui="{ content: 'max-w-xs' }">
      <template #body>
        <OCategoryTree v-model="folder" :categories="categories" root-label="Toutes les textures" class="h-[calc(100dvh-9rem)]" @update:model-value="treeOpen = false" />
      </template>
    </USlideover>
    <OTextureViewer v-model:open="viewerOpen" :entries="entries" />
  </div>
</template>
