<script setup lang="ts">
import type { Category, Page, Prop, PropDetails } from '~/utils/types'

/** Models › Props: the game's meshes, their 3D preview and an inspector with the raw game data. */
const sid = useSourceId()
const route = useRoute()
const router = useRouter()
const { details: srcDetails, refresh: refreshDetails } = useSourceDetails()
const jobs = useJobs()
const selection = useSelection()
const toast = useToast()

// ---- filters (kept in the URL: reload and back/forward keep the view)
const q = ref(String(route.query.q ?? ''))
const cat = ref(String(route.query.cat ?? ''))
const statues = ref<'all' | 'no' | 'only'>((route.query.statues as never) ?? 'all')
const named = ref(route.query.named !== '0')
const done = ref<'' | 'yes' | 'no'>((route.query.done as never) ?? '')

const categories = ref<Category[]>([])
const items = ref<Prop[]>([])
const total = ref(0)
const loading = ref(false)
const loaded = ref(false)
const failed = ref('')
const active = ref<Prop | null>(null)
const treeOpen = ref(false)
const inspectorOpen = ref(false)
const convertOpen = ref(false)
const convertKeys = ref<string[]>([])
const mobilePreview = ref(false)
const search = useTemplateRef<{ inputRef?: HTMLInputElement }>('search')
const scroll = useTemplateRef<{
  $el: HTMLElement
  virtualizer?: { scrollToIndex: (i: number, o?: object) => void }
}>('scroll')

let seq = 0
async function fetchPage(reset: boolean) {
  const mine = ++seq
  loading.value = true
  failed.value = ''
  try {
    const params = new URLSearchParams({
      q: q.value,
      cat: cat.value,
      statues: statues.value,
      named: named.value ? '1' : '0',
      done: done.value,
      limit: '200',
      offset: reset ? '0' : String(items.value.length),
    })
    const r = await api<Page<Prop>>(`/${sid.value}/props?${params}`)
    if (mine !== seq) return
    total.value = r.total
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

function more() {
  const el = scroll.value?.$el
  if (!el || loading.value || items.value.length >= total.value) return
  if (el.scrollTop + el.clientHeight > el.scrollHeight - 900) fetchPage(false)
}

const reload = debounce(() => fetchPage(true), 220)
const query = () => ({
  tab: 'props',
  ...(q.value && { q: q.value }),
  ...(cat.value && { cat: cat.value }),
  ...(statues.value !== 'all' && { statues: statues.value }),
  ...(!named.value && { named: '0' }),
  ...(done.value && { done: done.value }),
  ...(active.value && { k: active.value.key }),
  ...(tab.value !== 'summary' && { t: tab.value }),
})
const sync = () => route.path.endsWith('/models') && router.replace({ query: query() })
watch([q, cat, statues, named, done], () => {
  reload()
  sync()
})

onMounted(async () => {
  categories.value = await api<Category[]>(`/${sid.value}/props/categories`).catch(() => [])
  fetchPage(true)
  const k = String(route.query.k ?? '')
  if (k) {
    const r = await api<Page<Prop>>(
      `/${sid.value}/props?q=${encodeURIComponent(k)}&named=0&limit=3`,
    ).catch(() => null)
    const found = r?.items.find((i) => i.key === k)
    if (found) preview(found)
  }
})

// the catalog is indexed on first launch: wait for it
let waitTimer: ReturnType<typeof setInterval> | undefined
watch(
  () => srcDetails.value?.assets,
  (n) => {
    clearInterval(waitTimer)
    if (n === 0) {
      waitTimer = setInterval(async () => {
        await refreshDetails()
        if (srcDetails.value?.assets) {
          categories.value = await api<Category[]>(`/${sid.value}/props/categories`)
          fetchPage(true)
        }
      }, 3000)
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => clearInterval(waitTimer))

// ---- preview + inspector
const glb = computed(() =>
  active.value ? `/api/${sid.value}/props/${active.value.key}/glb` : undefined,
)
const gmodPath = computed(() => (active.value ? modelPath(sid.value, active.value) : ''))
const activeIndex = computed(() => items.value.findIndex((p) => p.key === active.value?.key))
const info = ref<PropDetails | null>(null)
const infoLoading = ref(false)
const infoError = ref('')
const tab = ref(String(route.query.t ?? 'summary'))
watch(tab, sync)
let pick = 0

async function loadInfo(p: Prop) {
  const mine = ++pick
  infoLoading.value = true
  infoError.value = ''
  try {
    const d = await api<PropDetails>(`/${sid.value}/props/${p.key}/details`)
    if (mine === pick) info.value = d
  } catch (e) {
    if (mine === pick) {
      info.value = null
      infoError.value = apiError(e)
    }
  } finally {
    if (mine === pick) infoLoading.value = false
  }
}
const loadInfoSoon = debounce((p: Prop) => loadInfo(p), 160)

function preview(p: Prop) {
  active.value = p
  mobilePreview.value = true
  loadInfoSoon(p)
  sync()
}

function step(delta: number) {
  if (!items.value.length) return
  const i = Math.min(
    items.value.length - 1,
    Math.max(0, (activeIndex.value < 0 ? (delta > 0 ? -1 : 1) : activeIndex.value) + delta),
  )
  const p = items.value[i]
  if (p) preview(p)
  scroll.value?.virtualizer?.scrollToIndex(i, { align: 'auto' })
  more()
}

// ---- selection
let lastTicked = -1
function tick(p: Prop, index: number, shift: boolean) {
  if (shift && lastTicked >= 0) {
    const [a, b] = [Math.min(lastTicked, index), Math.max(lastTicked, index)]
    selection.add(Object.fromEntries(items.value.slice(a, b + 1).map((x) => [x.key, x.rel])))
  } else selection.toggle(p.key, p.rel)
  lastTicked = index
}

const selectingAll = ref(false)
async function selectAllMatching() {
  if (
    total.value > 3000 &&
    !confirm(`Sélectionner les ${total.value.toLocaleString('fr-FR')} résultats ?`)
  )
    return
  selectingAll.value = true
  try {
    const params = new URLSearchParams({
      q: q.value,
      cat: cat.value,
      statues: statues.value,
      named: named.value ? '1' : '0',
    })
    const keys = await api<string[]>(`/${sid.value}/props/keys?${params}`)
    selection.add(Object.fromEntries(keys.map((k) => [k, k])))
    toast.add({
      title: `${keys.length.toLocaleString('fr-FR')} props sélectionnés`,
      icon: 'i-ri-checkbox-multiple-line',
    })
  } finally {
    selectingAll.value = false
  }
}

function convert(keys: string[]) {
  convertKeys.value = keys
  convertOpen.value = true
}

async function started(job: string) {
  toast.add({
    title: 'Conversion lancée',
    description: 'Suis la progression dans les travaux.',
    icon: 'i-ri-play-large-line',
  })
  await jobs.track(job)
  jobs.open.value = true
  jobs.schedule()
  selection.clear()
}

// a finished conversion marks the props of the list and the inspector
const finished = computed(
  () => jobs.jobs.value.filter((j) => j.phase === 'done' && j.kind === 'props').length,
)
watch(finished, () => {
  fetchPage(true)
  if (active.value) loadInfo(active.value)
})

// ---- textures
const texOpen = ref(false)
const texIndex = ref(0)
const texEntries = computed(() => (info.value ? gameEntries(sid.value, info.value.materials) : []))
function openTexture(mi: number, ti: number) {
  if (!info.value) return
  texIndex.value = gameEntryIndex(info.value.materials, mi, ti)
  texOpen.value = true
}

const textureCount = computed(
  () => new Set(info.value?.materials.flatMap((m) => m.textures.map((t) => t.key)) ?? []).size,
)
const dims = computed(() =>
  info.value?.extent ? info.value.extent.map((v) => Math.round(v * 100)) : null,
)
const stats = computed(() => {
  const d = info.value
  if (!d) return []
  return [
    { label: 'Triangles', value: d.triangles.toLocaleString('fr-FR'), icon: 'i-ri-shape-line' },
    { label: 'Sommets', value: d.vertices.toLocaleString('fr-FR'), icon: 'i-ri-focus-line' },
    { label: 'Matériaux', value: String(d.materials.length), icon: 'i-ri-palette-line' },
    { label: 'Textures du jeu', value: String(textureCount.value), icon: 'i-ri-image-line' },
    {
      label: 'Dimensions',
      value: dims.value ? `${dims.value[0]} × ${dims.value[2]} × ${dims.value[1]} cm` : '—',
      icon: 'i-ri-ruler-line',
    },
    {
      label: 'Niveaux de détail',
      value: d.lods.length ? d.lods.map((l) => `LOD${l}`).join(' ') : '—',
      icon: 'i-ri-stack-line',
    },
    {
      label: 'Collision du jeu',
      value: d.collision
        ? Object.entries(d.collision.shapes)
            .map(([k, v]) => `${v} ${k}`)
            .join(', ')
        : 'aucune',
      icon: 'i-ri-shield-line',
    },
    {
      label: 'Variantes',
      value: d.variants.length ? `${d.variants.length} skin(s)` : 'aucune',
      icon: 'i-ri-t-shirt-line',
    },
  ]
})

const TABS = [
  { label: 'Résumé', value: 'summary', icon: 'i-ri-dashboard-line' },
  { label: 'Matériaux', value: 'materials', icon: 'i-ri-palette-line' },
  { label: 'Données', value: 'data', icon: 'i-ri-database-2-line' },
]

defineShortcuts({
  '/': () => search.value?.inputRef?.focus(),
  arrowdown: { usingInput: true, handler: () => step(1) },
  arrowup: { usingInput: true, handler: () => step(-1) },
  space: () => active.value && selection.toggle(active.value.key, active.value.rel),
  escape: () => (mobilePreview.value = false),
})

const SEGMENTS = [
  { label: 'Tous', value: 'all' },
  { label: 'Props', value: 'no' },
  { label: 'Statues', value: 'only' },
] as const
const DONE = [
  { label: 'Tous', value: '' },
  { label: 'Convertis', value: 'yes' },
  { label: 'À convertir', value: 'no' },
]
</script>

<template>
  <div class="flex h-full gap-3 px-3 pb-3 pt-[4.75rem] sm:pt-20">
    <!-- folders (very wide screens) -->
    <UCard class="w-60 shrink-0 max-2xl:hidden" :ui="{ root: 'flex min-h-0 flex-col', body: 'min-h-0 flex-1 p-2 sm:p-2' }">
      <OCategoryTree v-model="cat" :categories="categories" />
    </UCard>

    <!-- results -->
    <UCard class="w-full min-w-0 lg:w-[23rem] lg:shrink-0" :ui="panelUi">
      <template #header>
        <slot name="switch" />
        <UInput ref="search" v-model="q" icon="i-ri-search-line" placeholder="Rechercher un prop, un hash…" size="lg" class="w-full" :loading="loading">
          <template #trailing>
            <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
            <UKbd v-else value="/" />
          </template>
        </UInput>

        <div class="flex flex-wrap items-center gap-2">
          <UFieldGroup>
            <UButton v-for="s in SEGMENTS" :key="s.value" :label="s.label" size="xs" color="neutral" :variant="statues === s.value ? 'solid' : 'outline'" @click="statues = s.value" />
          </UFieldGroup>
          <USelectMenu v-model="done" :items="DONE" size="xs" class="ml-auto w-32" icon="i-ri-checkbox-circle-line" value-key="value" :search-input="false" />
        </div>

        <div class="flex items-center gap-2">
          <UButton class="2xl:hidden" icon="i-ri-folder-3-line" label="Dossiers" size="xs" color="neutral" variant="outline" @click="treeOpen = true" />
          <UBadge v-if="cat" :label="titleCase(leaf(cat))" color="primary" variant="soft" class="max-w-40">
            <template #trailing>
              <UButton icon="i-ri-close-line" color="primary" variant="link" size="xs" class="-mr-1 p-0" aria-label="Retirer le filtre" @click="cat = ''" />
            </template>
          </UBadge>
          <USwitch v-model="named" size="sm" label="Nommés" class="ml-auto" />
        </div>

        <div class="flex items-center justify-between text-xs text-muted">
          <span class="tabular-nums">{{ total.toLocaleString('fr-FR') }} résultat{{ total > 1 ? 's' : '' }}</span>
          <UButton v-if="total" label="Tout sélectionner" size="xs" color="neutral" variant="link" :loading="selectingAll" @click="selectAllMatching" />
        </div>
      </template>

      <UEmpty v-if="failed" icon="i-ri-error-warning-line" title="Chargement impossible" :description="failed" class="absolute inset-0" :actions="[{ label: 'Réessayer', onClick: () => fetchPage(true) }]" />
      <UEmpty
        v-else-if="!items.length && loaded && !loading && srcDetails && !srcDetails.assets"
        icon="i-ri-database-2-line"
        title="Indexation du catalogue…"
        description="Première ouverture : le catalogue se construit (une à deux minutes)."
        class="absolute inset-0"
      />
      <UEmpty
        v-else-if="!items.length && loaded && !loading"
        icon="i-ri-search-eye-line"
        title="Aucun résultat"
        description="Essaie un autre mot, ou retire un filtre."
        class="absolute inset-0"
        :actions="[{ label: 'Réinitialiser', color: 'neutral', variant: 'outline', onClick: () => { q = ''; cat = ''; statues = 'all'; named = true; done = '' } }]"
      />
      <div v-else-if="!items.length" class="space-y-2 p-3">
        <USkeleton v-for="n in 9" :key="n" class="h-12 w-full" />
      </div>
      <UScrollArea
        v-else
        ref="scroll"
        v-slot="{ item, index }"
        :items="items"
        :virtualize="{ estimateSize: 56, overscan: 10, getItemKey: (i: number) => items[i]?.key ?? i }"
        class="absolute inset-0 overflow-y-auto"
        @scroll="(s: boolean) => !s && more()"
      >
        <div
          role="option"
          :aria-selected="active?.key === (item as Prop).key"
          class="flex cursor-pointer items-center gap-3 border-b border-default px-3 py-2 transition-colors"
          :class="active?.key === (item as Prop).key ? 'bg-primary/10' : 'hover:bg-elevated'"
          @click="preview(item as Prop)"
        >
          <UCheckbox
            :model-value="selection.has((item as Prop).key)"
            aria-label="Sélectionner"
            @click.stop
            @update:model-value="tick(item as Prop, index, ($event as unknown as { shiftKey?: boolean })?.shiftKey ?? false)"
          />
          <UUser
            :name="titleCase(leaf((item as Prop).rel))"
            :description="crumb((item as Prop).rel) || (item as Prop).cat"
            class="min-w-0 flex-1"
            :ui="{ wrapper: 'min-w-0', name: 'truncate', description: 'truncate' }"
          />
          <UTooltip v-if="(item as Prop).converted" text="Déjà converti">
            <UIcon name="i-ri-checkbox-circle-fill" class="size-4 shrink-0 text-success" />
          </UTooltip>
          <UBadge v-if="(item as Prop).skinned" label="statue" size="sm" color="neutral" variant="outline" />
        </div>
      </UScrollArea>

      <template v-if="selection.count.value" #footer>
        <div class="flex items-center gap-2">
          <p class="flex-1 text-sm">
            <span class="font-semibold tabular-nums text-highlighted">{{ selection.count.value.toLocaleString('fr-FR') }}</span>
            <span class="text-muted"> sélectionné{{ selection.count.value > 1 ? 's' : '' }}</span>
          </p>
          <UButton label="Vider" color="neutral" variant="ghost" size="sm" @click="selection.clear()" />
          <UButton label="Convertir…" icon="i-ri-hammer-line" color="primary" variant="solid" size="sm" @click="convert(selection.keys.value)" />
        </div>
      </template>
    </UCard>

    <!-- viewport -->
    <UCard
      class="min-w-0 flex-1 max-lg:fixed max-lg:inset-x-3 max-lg:bottom-3 max-lg:top-[4.75rem] max-lg:z-30"
      :class="!mobilePreview && 'max-lg:hidden'"
      :ui="{ root: 'relative flex flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }"
    >
      <OViewer :url="glb">
        <template #overlay>
          <WGlassCard v-if="active" :halo="false" class="max-w-md" body-class="p-3 sm:p-3">
            <p class="truncate font-semibold text-highlighted">{{ titleCase(leaf(active.rel)) }}</p>
            <p class="truncate text-xs text-muted" :title="active.rel">{{ active.cat }} › {{ crumb(active.rel) }}</p>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <UBadge v-if="info?.triangles" :label="`${info.triangles.toLocaleString('fr-FR')} tri`" color="neutral" variant="subtle" size="sm" />
              <UBadge v-if="dims" :label="`${dims[0]} × ${dims[2]} × ${dims[1]} cm`" color="neutral" variant="subtle" size="sm" />
              <UBadge :label="fmtBytes(active.size)" color="neutral" variant="subtle" size="sm" />
              <UBadge v-if="active.skinned" label="statue" color="neutral" variant="outline" size="sm" />
              <UBadge v-if="active.converted" label="converti" color="success" variant="subtle" size="sm" icon="i-ri-check-line" />
            </div>
          </WGlassCard>
        </template>
        <template #overlay-end>
          <UButton class="lg:hidden" icon="i-ri-close-line" color="neutral" variant="subtle" aria-label="Fermer l’aperçu" @click="mobilePreview = false" />
          <WGlassCard v-if="active" :halo="false" body-class="flex flex-col gap-0.5 p-1 sm:p-1">
            <UTooltip text="Convertir celui-ci" :content="{ side: 'left' }">
              <UButton icon="i-ri-hammer-line" size="sm" color="primary" variant="soft" aria-label="Convertir" @click="convert([active.key])" />
            </UTooltip>
            <UTooltip v-if="active.converted" text="Voir dans la visionneuse" :content="{ side: 'left' }">
              <UButton icon="i-ri-eye-line" size="sm" color="success" variant="ghost" aria-label="Visionneuse" :to="{ path: `/${sid}/viewer`, query: { m: gmodPath } }" />
            </UTooltip>
            <UTooltip text="Détails" :content="{ side: 'left' }">
              <UButton class="xl:hidden" icon="i-ri-layout-right-line" size="sm" color="neutral" variant="ghost" aria-label="Ouvrir l’inspecteur" @click="inspectorOpen = true" />
            </UTooltip>
          </WGlassCard>
        </template>
        <template #empty>
          <UEmpty icon="i-ri-cursor-line" title="Choisis un prop" description="Clique un élément de la liste, ou navigue avec ↑ ↓. Espace coche, / cherche." />
        </template>
      </OViewer>
    </UCard>

    <!-- inspector -->
    <UCard class="w-[24rem] shrink-0 max-xl:hidden 2xl:w-[26rem]" :ui="{ root: 'relative flex min-h-0 flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }">
      <OPropInspector
        v-if="info && active"
        v-model:tab="tab"
        :info="info"
        :prop="active"
        :stats="stats"
        :gmod-path="gmodPath"
        :selected="selection.has(active.key)"
        @texture="openTexture"
        @convert="convert([active.key])"
        @select="selection.toggle(active.key, active.rel)"
      />
      <UEmpty v-else-if="infoError" icon="i-ri-error-warning-line" title="Lecture impossible" :description="infoError" class="absolute inset-0" />
      <div v-else-if="infoLoading" class="space-y-2 p-3">
        <USkeleton class="h-8 w-full" />
        <USkeleton v-for="n in 4" :key="n" class="h-16 w-full" />
      </div>
      <UEmpty v-else icon="i-ri-stethoscope-line" title="Inspecteur" description="Données du jeu, matériaux et textures d’origine du prop choisi." class="absolute inset-0" />
    </UCard>

    <USlideover v-model:open="inspectorOpen" side="right" title="Détails du prop" :ui="{ content: 'max-w-md', body: 'p-0 sm:p-0' }">
      <template #body>
        <OPropInspector
          v-if="info && active"
          v-model:tab="tab"
          :info="info"
          :prop="active"
          :stats="stats"
          :gmod-path="gmodPath"
          :selected="selection.has(active.key)"
          @texture="openTexture"
          @convert="convert([active.key])"
          @select="selection.toggle(active.key, active.rel)"
        />
      </template>
    </USlideover>

    <USlideover v-model:open="treeOpen" side="left" title="Dossiers" :ui="{ content: 'max-w-xs' }">
      <template #body>
        <OCategoryTree v-model="cat" :categories="categories" class="h-[calc(100dvh-9rem)]" @update:model-value="treeOpen = false" />
      </template>
    </USlideover>

    <OTextureViewer v-model:open="texOpen" v-model:index="texIndex" :entries="texEntries" />
    <OConvertModal v-model:open="convertOpen" :keys="convertKeys" @started="started" />
  </div>
</template>
