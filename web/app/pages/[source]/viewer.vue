<script setup lang="ts">
import type * as THREE from 'three'
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js'
import type { LayerName } from '~/utils/overlays'
import type {
  Category,
  OutputItem,
  OutputModel,
  OutputPage,
  OutputStats,
  ViewerRoot,
} from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/viewer` })

/**
 * Model viewer: browse the models omni converted into the GMod addon, or any folder of compiled models (a decompiled
 * addon...), and inspect them with the files GMod will load (rebuilt from the compiled .mdl/.vtx/.vvd/.vmt/.vtf).
 * Conversion-related features (origin, original comparison, reconversion) only exist for omni's own output.
 */
const sid = useSourceId()
const route = useRoute()
const router = useRouter()
const jobsState = useJobs()
const toast = useToast()

// ---- list filters (kept in the URL)
const roots = ref<ViewerRoot[]>([])
const root = ref(String(route.query.root ?? ''))
viewerRoot.value = root.value
const external = computed(() => root.value !== '')
const currentRoot = computed(() => roots.value.find((r) => r.id === root.value))
const folderOpen = ref(false)
const q = ref(String(route.query.q ?? ''))
const kind = ref<'' | 'prop' | 'character'>((route.query.kind as never) ?? '')
const cat = ref(String(route.query.cat ?? ''))
const sort = ref<'recent' | 'name' | 'size'>((route.query.sort as never) ?? 'recent')

const categories = ref<Category[]>([])
const items = ref<OutputItem[]>([])
const total = ref(0)
const kinds = ref({ prop: 0, character: 0, model: 0 })
const stats = ref<OutputStats | null>(null)
const loading = ref(false)
const loaded = ref(false)
const failed = ref('')
const treeOpen = ref(false)
const mobilePreview = ref(false)
const inspectorOpen = ref(false)
const search = useTemplateRef<{ inputRef?: HTMLInputElement }>('search')
const scroll = useTemplateRef<{
  $el: HTMLElement
  virtualizer?: { scrollToIndex: (i: number, o?: object) => void }
}>('scroll')
const viewer = useTemplateRef<{
  root: THREE.Object3D | null
  parser: GLTF['parser'] | null
  size: [number, number, number] | null
  sync: () => void
  getScene: () => THREE.Scene
  getBox: () => THREE.Box3
}>('viewer')

let seq = 0
let retried = false
async function fetchPage(reset: boolean, fresh = false) {
  const mine = ++seq
  loading.value = true
  failed.value = ''
  try {
    const params = new URLSearchParams({
      q: q.value,
      kind: kind.value,
      cat: cat.value,
      sort: sort.value,
      root: root.value,
      limit: '200',
      offset: reset ? '0' : String(items.value.length),
      ...(fresh && { fresh: '1' }),
    })
    const r = await api<OutputPage>(`/${sid.value}/output?${params}`)
    if (mine !== seq) return
    total.value = r.total
    kinds.value = r.kinds
    items.value = reset ? r.items : [...items.value, ...r.items]
    if (reset && !fresh) scroll.value?.$el?.scrollTo({ top: 0 })
    await nextTick()
    more()
  } catch (e) {
    if (mine === seq) {
      failed.value = apiError(e)
      if (!retried) {
        retried = true
        setTimeout(() => fetchPage(reset, fresh), 1500)
      }
    }
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

async function loadSide() {
  const r = `?root=${root.value}`
  stats.value = await api<OutputStats>(`/${sid.value}/output/stats${r}`).catch(() => null)
  categories.value = await api<Category[]>(`/${sid.value}/output/categories${r}`).catch(() => [])
}

async function loadRoots() {
  roots.value = await api<ViewerRoot[]>(`/${sid.value}/output/roots`).catch(() => [])
  if (root.value && !roots.value.some((r) => r.id === root.value)) root.value = ''
}

const rootMenu = computed(() => [
  roots.value.map((r) => ({
    label: r.label,
    icon: r.external ? 'i-ri-folder-3-line' : 'i-ri-archive-line',
    type: 'checkbox' as const,
    checked: r.id === root.value,
    onUpdateChecked: () => (root.value = r.id),
  })),
  [
    {
      label: 'Ouvrir un dossier…',
      icon: 'i-ri-folder-add-line',
      onSelect: () => (folderOpen.value = true),
    },
  ],
])

// switching folder starts the browser over
watch(root, async () => {
  viewerRoot.value = root.value
  active.value = null
  model.value = null
  modelError.value = ''
  compare.value = false
  q.value = cat.value = ''
  kind.value = ''
  items.value = []
  loaded.value = false
  sync()
  await Promise.all([fetchPage(true), loadSide()])
})

// only while this page is the current one (async callbacks can resume after the user navigated away)
const sync = () => route.path.endsWith('/viewer') && router.replace({ query: query() })
const reload = debounce(() => fetchPage(true), 220)
const active = ref<OutputItem | null>(null)
const inspectorTab = ref(String(route.query.tab ?? 'summary'))
const query = () => ({
  ...(root.value && { root: root.value }),
  ...(q.value && { q: q.value }),
  ...(kind.value && { kind: kind.value }),
  ...(cat.value && { cat: cat.value }),
  ...(sort.value !== 'recent' && { sort: sort.value }),
  ...(active.value && { m: active.value.path }),
  ...(inspectorTab.value !== 'summary' && { tab: inspectorTab.value }),
})
watch([q, kind, cat, sort], () => {
  reload()
  sync()
})
watch(inspectorTab, () => sync())

// ---- Garry's Mod: start it on a map with the selected model ready to spawn
const gmodBusy = ref(false)
async function openInGmod() {
  if (!active.value) return
  gmodBusy.value = true
  try {
    const r = await api<{ hint: string }>(`/${sid.value}/gmod/launch`, {
      method: 'POST',
      body: {
        model: active.value.path,
        kind: active.value.kind === 'character' ? 'player' : 'prop',
      },
    })
    useToast().add({ title: 'Garry’s Mod', description: r.hint, icon: 'i-ri-gamepad-line' })
  } catch (e) {
    useToast().add({ title: 'Ouverture impossible', description: apiError(e), color: 'error' })
  } finally {
    gmodBusy.value = false
  }
}

// ---- selected model
const model = ref<OutputModel | null>(null)
const modelError = ref('')
const modelLoading = ref(false)
const skin = ref(0)
const groups = ref<number[]>([])
const lod = ref(0)
const bone = ref(-1)
let pick = 0

async function select(item: OutputItem, opts: { keepVariants?: boolean } = {}) {
  const mine = ++pick
  active.value = item
  mobilePreview.value = true
  sync()
  modelLoading.value = true
  modelError.value = ''
  if (!opts.keepVariants) model.value = null
  try {
    const m = await api<OutputModel>(
      `/${sid.value}/output/model?path=${encodeURIComponent(item.path)}&root=${root.value}`,
    )
    if (mine !== pick) return
    if (!opts.keepVariants) {
      skin.value = 0
      lod.value = 0
      bone.value = -1
      compare.value = false
    }
    groups.value =
      opts.keepVariants && groups.value.length === m.model.bodyparts.length
        ? groups.value
        : m.model.bodyparts.map(() => 0)
    model.value = m
  } catch (e) {
    if (mine === pick) modelError.value = apiError(e)
  } finally {
    if (mine === pick) modelLoading.value = false
  }
}

const gmodPath = computed(() => active.value?.path ?? '')
const glb = computed(() =>
  active.value && model.value
    ? outputUrl(sid.value, 'glb', {
        path: active.value.path,
        lod: lod.value,
        v: Math.round(active.value.mtime),
      })
    : undefined,
)
const dims = computed(() => (model.value ? dimsCm(model.value) : null))
const activeIndex = computed(() => items.value.findIndex((p) => p.path === active.value?.path))

function step(delta: number) {
  if (!items.value.length) return
  const i = Math.min(
    items.value.length - 1,
    Math.max(0, (activeIndex.value < 0 ? (delta > 0 ? -1 : 1) : activeIndex.value) + delta),
  )
  select(items.value[i]!)
  scroll.value?.virtualizer?.scrollToIndex(i, { align: 'auto' })
  more()
}

// ---- variants (bodygroups / skins) applied to the loaded GLB
let mats = new Map<number, Promise<THREE.Material>>()
function applyVariants() {
  const v = viewer.value
  const m = model.value
  const root = v?.root
  const parser = v?.parser
  if (!m || !root || !parser) return
  const row = m.skin_materials[skin.value] ?? m.skin_materials[0] ?? []
  root.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    const [b, mi, , ref] = mesh.name.split('|').map(Number)
    if (b === undefined || mi === undefined || ref === undefined) return
    mesh.visible = (groups.value[b] ?? 0) === mi
    const index = row[ref]
    if (index !== undefined && mesh.visible) {
      if (!mats.has(index))
        mats.set(index, parser.getDependency('material', index) as Promise<THREE.Material>)
      mats.get(index)?.then((mat) => {
        mesh.material = mat
        v.sync()
      })
    }
  })
  v.sync()
}
watch([skin, groups], applyVariants, { deep: true })

// ---- debug layers
const LAYERS: { key: LayerName; label: string; icon: string; kbd?: string }[] = [
  { key: 'skeleton', label: 'Squelette', icon: 'i-ri-body-scan-line', kbd: 'S' },
  { key: 'hitboxes', label: 'Hitboxes', icon: 'i-ri-shield-line', kbd: 'H' },
  { key: 'attachments', label: 'Attachements', icon: 'i-ri-crosshair-2-line' },
  { key: 'bounds', label: 'Boîtes englobantes et œil', icon: 'i-ri-fullscreen-line' },
  { key: 'collision', label: 'Collision physique', icon: 'i-ri-shape-line', kbd: 'C' },
  { key: 'player', label: 'Joueur (échelle)', icon: 'i-ri-user-3-line' },
]
const layers = reactive<Record<LayerName, boolean>>({
  skeleton: false,
  hitboxes: false,
  attachments: false,
  bounds: false,
  collision: false,
  player: false,
})
const objects: Partial<Record<LayerName, THREE.Object3D>> = {}
let markerObj: THREE.Mesh | null = null
const shading = ref<'textured' | 'clay' | 'normals' | 'checker'>('textured')
const compare = ref(false)
const split = ref(0.5)
const collisionState = reactive({ loading: false, missing: false })

function clearLayers() {
  for (const k of Object.keys(objects) as LayerName[]) {
    disposeGroup(objects[k]!)
    delete objects[k]
  }
  if (markerObj) {
    disposeGroup(markerObj)
    markerObj = null
  }
}

function showLayers() {
  for (const k of LAYERS.map((l) => l.key)) if (objects[k]) objects[k]!.visible = layers[k]
  viewer.value?.sync()
}

async function onLoaded() {
  const v = viewer.value
  const m = model.value
  if (!v || !m) return
  mats = new Map()
  clearLayers()
  const scene = v.getScene()
  const built = buildLayers(m)
  for (const [k, g] of Object.entries(built)) {
    objects[k as LayerName] = g
    scene.add(g)
  }
  const player = playerScale(v.getBox())
  objects.player = player
  scene.add(player)
  markerObj = boneMarker()
  scene.add(markerObj)
  applyVariants()
  showLayers()
  if (layers.collision) await loadCollision()
  moveMarker()
}

async function loadCollision() {
  const m = model.value
  if (!m || objects.collision || collisionState.loading) return
  collisionState.loading = true
  collisionState.missing = false
  try {
    const r = await api<{ pieces: number[][] }>(
      `/${sid.value}/output/collision?path=${encodeURIComponent(m.path)}&root=${root.value}`,
    )
    const g = collisionLayer(r.pieces)
    objects.collision = g
    viewer.value?.getScene().add(g)
  } catch {
    collisionState.missing = true
    layers.collision = false
    toast.add({
      title: 'Pas de collision à afficher',
      description: 'Ce modèle n’a pas de fichier .phy lisible.',
      icon: 'i-ri-shape-line',
    })
  } finally {
    collisionState.loading = false
    showLayers()
  }
}

watch(layers, async () => {
  if (layers.collision) await loadCollision()
  showLayers()
})
function toggle(k: LayerName) {
  layers[k] = !layers[k]
}

function moveMarker() {
  const b = model.value?.model.bones[bone.value]
  if (!markerObj) return
  markerObj.visible = !!b
  if (b) markerObj.position.set(...b.pos)
  viewer.value?.sync()
}
watch(bone, moveMarker)
watch(model, () => {
  if (!model.value) clearLayers()
})
onBeforeUnmount(clearLayers)

const compareUrl = computed(() =>
  model.value?.source ? `/api/${sid.value}/props/${model.value.source.key}/glb` : undefined,
)
watch(compareUrl, (u) => {
  if (!u) compare.value = false
})

const SHADINGS = [
  { label: 'Textures', value: 'textured', icon: 'i-ri-image-line' },
  { label: 'Argile (sans texture)', value: 'clay', icon: 'i-ri-contrast-drop-line' },
  { label: 'Normales', value: 'normals', icon: 'i-ri-palette-line' },
  { label: 'Damier (étirement des UV)', value: 'checker', icon: 'i-ri-layout-grid-line' },
] as const
const shadingMenu = computed(() =>
  SHADINGS.map((s) => ({
    label: s.label,
    icon: s.icon,
    type: 'checkbox' as const,
    checked: shading.value === s.value,
    onUpdateChecked: () => (shading.value = s.value),
  })),
)

// ---- textures
const texOpen = ref(false)
const texIndex = ref(0)
const texEntries = computed(() => {
  const m = model.value
  if (!m || !active.value) return []
  const row = m.skin_materials[skin.value] ?? []
  // the skin slot that draws a material (for the UV overlay): current skin first, then any skin
  const slotOf = (mi: number) => {
    for (const r of [row, ...m.skin_materials]) if (r.includes(mi)) return r.indexOf(mi)
    return null
  }
  return outputEntries(sid.value, active.value.path, m.materials, slotOf)
})
function openTexture(mi: number, ti: number) {
  const m = model.value
  const mat = m?.materials[mi]
  const t = mat?.textures[ti]
  if (!m || !t || !mat) return
  const i = texEntries.value.findIndex((e) => e.id === `${mat.name}:${t.param}:${t.path}`)
  texIndex.value = Math.max(0, i)
  texOpen.value = true
}

// ---- reconversion of a prop with other settings, and live refresh
const convertOpen = ref(false)
function reconvert() {
  if (model.value?.source) convertOpen.value = true
}
async function started(job: string) {
  toast.add({
    title: 'Conversion lancée',
    description: 'La sortie se met à jour toute seule à la fin.',
    icon: 'i-ri-play-large-line',
  })
  await jobsState.track(job)
  jobsState.open.value = true
}

const finished = computed(() =>
  jobsState.jobs.value
    .filter((j) => j.phase === 'done')
    .map((j) => j.id)
    .join(','),
)
watch(finished, async () => {
  if (external.value) return
  await fetchPage(true, true)
  loadSide()
  const a = active.value
  if (!a) return
  const fresh = items.value.find((i) => i.path === a.path)
  if (fresh && fresh.mtime !== a.mtime) select(fresh, { keepVariants: true })
})

// ---- boot
onBeforeUnmount(() => (viewerRoot.value = ''))
onMounted(async () => {
  await loadRoots()
  viewerRoot.value = root.value
  await Promise.all([fetchPage(true), loadSide()])
  const m = String(route.query.m ?? '')
  if (m) {
    const r = await api<OutputPage>(
      `/${sid.value}/output?q=${encodeURIComponent(leaf(m).replace(/\.mdl$/, ''))}&limit=50&root=${root.value}`,
    ).catch(() => null)
    const found = r?.items.find((i) => i.path === m)
    if (found) select(found)
    else modelError.value = `Ce modèle n’existe pas (encore) dans le dossier : ${m}`
  }
})

const SEGMENTS = computed(
  () =>
    [
      { label: 'Tout', value: '', n: kinds.value.prop + kinds.value.character + kinds.value.model },
      { label: 'Props', value: 'prop', n: kinds.value.prop },
      { label: 'Personnages', value: 'character', n: kinds.value.character },
    ] as const,
)
const SORTS = [
  { label: 'Plus récents', value: 'recent' },
  { label: 'Nom', value: 'name' },
  { label: 'Poids', value: 'size' },
]

defineShortcuts({
  '/': () => search.value?.inputRef?.focus(),
  arrowdown: { usingInput: true, handler: () => step(1) },
  arrowup: { usingInput: true, handler: () => step(-1) },
  s: () => toggle('skeleton'),
  h: () => toggle('hitboxes'),
  c: () => toggle('collision'),
  v: () => compareUrl.value && (compare.value = !compare.value),
  escape: () => (mobilePreview.value = false),
})
</script>

<template>
  <div class="flex h-full gap-3 px-3 pb-3 pt-[4.75rem] sm:pt-20">
    <!-- folders (very wide screens) -->
    <UCard class="w-60 shrink-0 max-2xl:hidden" :ui="{ root: 'flex min-h-0 flex-col', body: 'min-h-0 flex-1 p-2 sm:p-2' }">
      <OCategoryTree v-model="cat" :categories="categories" root-label="Tous les modèles" />
    </UCard>

    <!-- results -->
    <UCard class="w-full min-w-0 lg:w-[23rem] lg:shrink-0" :ui="panelUi">
      <template #header>
        <UDropdownMenu :items="rootMenu" :content="{ align: 'start' }" :ui="{ content: 'w-80' }">
          <UButton
            :icon="external ? 'i-ri-folder-3-line' : 'i-ri-archive-line'"
            :label="currentRoot?.label ?? 'Sortie omni'"
            trailing-icon="i-ri-arrow-down-s-line"
            color="neutral"
            variant="outline"
            block
            class="justify-start"
            :ui="{ label: 'flex-1 truncate text-left', trailingIcon: 'ml-auto' }"
          />
        </UDropdownMenu>

        <UInput ref="search" v-model="q" icon="i-ri-search-line" placeholder="Chercher un modèle…" size="lg" class="w-full" :loading="loading">
          <template #trailing>
            <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
            <UKbd v-else value="/" />
          </template>
        </UInput>

        <UFieldGroup v-if="!external" class="flex w-full">
          <UButton
            v-for="s in SEGMENTS"
            :key="s.value"
            size="xs"
            color="neutral"
            :variant="kind === s.value ? 'solid' : 'outline'"
            class="flex-1 justify-center"
            @click="kind = s.value"
          >
            {{ s.label }}
            <span class="tabular-nums opacity-60">{{ s.n.toLocaleString('fr-FR') }}</span>
          </UButton>
        </UFieldGroup>

        <div class="flex items-center gap-2">
          <UButton class="2xl:hidden" icon="i-ri-folder-3-line" label="Dossiers" size="xs" color="neutral" variant="outline" @click="treeOpen = true" />
          <UBadge v-if="cat" :label="titleCase(leaf(cat))" color="primary" variant="soft" class="max-w-32">
            <template #trailing>
              <UButton icon="i-ri-close-line" color="primary" variant="link" size="xs" class="-mr-1 p-0" aria-label="Retirer le filtre" @click="cat = ''" />
            </template>
          </UBadge>
          <USelect v-model="sort" :items="SORTS" size="xs" class="ml-auto w-36" icon="i-ri-sort-desc" />
        </div>

        <p class="text-xs tabular-nums text-muted">{{ total.toLocaleString('fr-FR') }} modèle{{ total > 1 ? 's' : '' }}</p>
      </template>

      <UEmpty v-if="failed" icon="i-ri-error-warning-line" title="Chargement impossible" :description="failed" class="absolute inset-0" :actions="[{ label: 'Réessayer', onClick: () => fetchPage(true) }]" />
      <UEmpty
        v-else-if="!items.length && loaded && !loading && !q && !cat && !kind && external"
        icon="i-ri-folder-warning-line"
        title="Aucun modèle dans ce dossier"
        description="Je cherche des fichiers .mdl dans tous les sous-dossiers. Vérifie que le dossier est bien celui de l’addon décompilé."
        class="absolute inset-0"
        :actions="[{ label: 'Ouvrir un autre dossier', icon: 'i-ri-folder-add-line', onClick: () => (folderOpen = true) }]"
      />
      <UEmpty
        v-else-if="!items.length && loaded && !loading && !q && !cat && !kind"
        icon="i-ri-archive-line"
        title="Rien n’a encore été converti"
        description="Convertis un prop ou construis un personnage : la sortie apparaît ici, avec ses textures et ses métadonnées."
        class="absolute inset-0"
        :actions="[{ label: 'Choisir des props', icon: 'i-ri-box-3-line', to: `/${sid}/props` }, { label: 'Ouvrir un dossier', icon: 'i-ri-folder-add-line', color: 'neutral', variant: 'outline', onClick: () => (folderOpen = true) }]"
      />
      <UEmpty
        v-else-if="!items.length && loaded && !loading"
        icon="i-ri-search-eye-line"
        title="Aucun résultat"
        description="Essaie un autre mot, ou retire un filtre."
        class="absolute inset-0"
        :actions="[{ label: 'Réinitialiser', color: 'neutral', variant: 'outline', onClick: () => { q = ''; cat = ''; kind = '' } }]"
      />
      <div v-else-if="!items.length" class="space-y-2 p-3">
        <USkeleton v-for="n in 9" :key="n" class="h-12 w-full" />
      </div>
      <UScrollArea
        v-else
        ref="scroll"
        v-slot="{ item }"
        :items="items"
        :virtualize="{ estimateSize: 58, overscan: 10, getItemKey: (i: number) => items[i]?.path ?? i }"
        class="absolute inset-0 overflow-y-auto"
        @scroll="(s: boolean) => !s && more()"
      >
        <div
          role="option"
          :aria-selected="active?.path === (item as OutputItem).path"
          class="flex cursor-pointer items-center gap-3 border-b border-default px-3 py-2 transition-colors"
          :class="active?.path === (item as OutputItem).path ? 'bg-primary/10' : 'hover:bg-elevated'"
          @click="select(item as OutputItem)"
        >
          <UUser
            :name="outputTitle(item as OutputItem)"
            :description="(item as OutputItem).kind === 'character' ? 'Personnage' : crumb((item as OutputItem).folder + '/x') || (external ? 'Racine du dossier' : 'Prop')"
            :avatar="{ icon: (item as OutputItem).kind === 'character' ? 'i-ri-user-3-line' : 'i-ri-box-3-line' }"
            class="min-w-0 flex-1"
            :ui="{ wrapper: 'min-w-0', name: 'truncate', description: 'truncate' }"
          />
          <div class="shrink-0 text-right text-xs tabular-nums text-muted">
            <p>{{ fmtBytes((item as OutputItem).bytes) }}</p>
            <p>{{ timeAgo((item as OutputItem).mtime) }}</p>
          </div>
        </div>
      </UScrollArea>

      <template v-if="stats" #footer>
        <div class="flex items-center gap-2 text-xs text-muted">
          <UIcon name="i-ri-archive-line" class="size-4 shrink-0" />
          <p class="min-w-0 flex-1 truncate" :title="stats.path">
            {{ external ? 'Dossier' : 'Addon' }} : {{ stats.models.toLocaleString('fr-FR') }} modèles<template v-if="stats.textures"> · {{ stats.textures.toLocaleString('fr-FR') }} textures</template>
            · {{ fmtBytes(stats.bytes + (stats.texture_bytes ?? 0)) }}
          </p>
          <UTooltip text="Actualiser la liste">
            <UButton icon="i-ri-refresh-line" size="xs" color="neutral" variant="ghost" aria-label="Actualiser" @click="fetchPage(true, true); loadSide()" />
          </UTooltip>
        </div>
      </template>
    </UCard>

    <!-- viewport -->
    <UCard
      class="min-w-0 flex-1 max-lg:fixed max-lg:inset-x-3 max-lg:bottom-3 max-lg:top-[4.75rem] max-lg:z-30"
      :class="!mobilePreview && 'max-lg:hidden'"
      :ui="{ root: 'relative flex flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }"
    >
      <OViewer
        ref="viewer"
        v-model:split="split"
        :url="glb"
        :busy="modelLoading && !model ? 'Lecture du modèle compilé…' : ''"
        :mode="model?.kind === 'character' ? 'character' : 'object'"
        :compare="compare"
        :compare-url="compareUrl"
        :shading="shading"
        @loaded="onLoaded"
      >
        <template #overlay>
          <WGlassCard v-if="active" :halo="false" class="max-w-md" body-class="p-3 sm:p-3">
            <p class="truncate font-semibold text-highlighted">{{ outputTitle(active) }}</p>
            <p class="truncate font-mono text-xs text-muted" :title="active.path">{{ active.path.replace(/^models\//, '') }}</p>
            <div v-if="model" class="mt-2 flex flex-wrap gap-1.5">
              <UBadge :label="VERDICT[model.verdict].label" :color="VERDICT[model.verdict].color" variant="subtle" size="sm" :icon="VERDICT[model.verdict].icon" />
              <UBadge :label="`${(model.lods[lod]?.triangles ?? model.triangles).toLocaleString('fr-FR')} tri`" color="neutral" variant="subtle" size="sm" />
              <UBadge v-if="dims" :label="dims" color="neutral" variant="subtle" size="sm" />
              <UBadge v-if="model.lods.length > 1" :label="`LOD ${lod}/${model.lods.length - 1}`" color="neutral" variant="outline" size="sm" />
            </div>
          </WGlassCard>
        </template>
        <template #overlay-end>
          <UButton class="lg:hidden" icon="i-ri-close-line" color="neutral" variant="subtle" aria-label="Fermer l’aperçu" @click="mobilePreview = false" />
          <WGlassCard v-if="model" :halo="false" body-class="flex flex-col gap-0.5 p-1 sm:p-1">
            <UTooltip v-for="l in LAYERS" :key="l.key" :text="l.label" :kbds="l.kbd ? [l.kbd] : undefined" :content="{ side: 'left' }">
              <UButton
                :icon="l.icon"
                size="sm"
                :color="layers[l.key] ? 'primary' : 'neutral'"
                :variant="layers[l.key] ? 'soft' : 'ghost'"
                :loading="l.key === 'collision' && collisionState.loading"
                :aria-label="l.label"
                :aria-pressed="layers[l.key]"
                @click="toggle(l.key)"
              />
            </UTooltip>
            <USeparator class="my-0.5" />
            <UDropdownMenu :items="shadingMenu" :content="{ side: 'left', align: 'start' }">
              <UButton icon="i-ri-contrast-drop-line" size="sm" :color="shading === 'textured' ? 'neutral' : 'primary'" :variant="shading === 'textured' ? 'ghost' : 'soft'" aria-label="Mode de rendu" />
            </UDropdownMenu>
            <UTooltip v-if="compareUrl" text="Comparer avec l’original du jeu" :kbds="['V']" :content="{ side: 'left' }">
              <UButton icon="i-ri-arrow-left-right-line" size="sm" :color="compare ? 'primary' : 'neutral'" :variant="compare ? 'soft' : 'ghost'" aria-label="Comparer avec l’original" @click="compare = !compare" />
            </UTooltip>
            <UTooltip v-if="active && !currentRoot?.external" text="Ouvrir dans Garry’s Mod" :content="{ side: 'left' }">
              <UButton icon="i-ri-gamepad-line" size="sm" color="neutral" variant="ghost" aria-label="Ouvrir dans Garry’s Mod" :loading="gmodBusy" @click="openInGmod" />
            </UTooltip>
            <UTooltip text="Détails du modèle" :content="{ side: 'left' }">
              <UButton class="xl:hidden" icon="i-ri-layout-right-line" size="sm" color="neutral" variant="ghost" aria-label="Ouvrir l’inspecteur" @click="inspectorOpen = true" />
            </UTooltip>
          </WGlassCard>
        </template>
        <template #empty>
          <UAlert v-if="modelError" color="error" variant="subtle" icon="i-ri-error-warning-line" title="Modèle illisible" :description="modelError" class="max-w-md" />
          <UEmpty v-else icon="i-ri-cursor-line" title="Choisis un modèle" description="Le modèle apparaît tel que GMod le chargera, avec son squelette, ses hitboxes et sa collision à la demande." />
        </template>
      </OViewer>
    </UCard>

    <!-- inspector (wide screens) -->
    <UCard class="w-[24rem] shrink-0 max-xl:hidden 2xl:w-[26rem]" :ui="{ root: 'relative flex min-h-0 flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }">
      <OOutputInspector
        v-if="model"
        v-model:tab="inspectorTab"
        v-model:skin="skin"
        v-model:groups="groups"
        v-model:lod="lod"
        v-model:bone="bone"
        :model="model"
        :gmod-path="gmodPath"
        @texture="openTexture"
        @reconvert="reconvert"
      />
      <USkeleton v-else-if="modelLoading" class="m-3 h-40" />
      <UEmpty v-else icon="i-ri-stethoscope-line" title="Inspecteur" description="Contrôle qualité, variantes, matériaux, squelette et fichiers du modèle choisi." class="absolute inset-0" />
    </UCard>

    <USlideover v-model:open="inspectorOpen" side="right" title="Détails du modèle" :ui="{ content: 'max-w-md', body: 'p-0 sm:p-0' }">
      <template #body>
        <OOutputInspector
          v-if="model"
          v-model:tab="inspectorTab"
          v-model:skin="skin"
          v-model:groups="groups"
          v-model:lod="lod"
          v-model:bone="bone"
          :model="model"
          :gmod-path="gmodPath"
          @texture="openTexture"
          @reconvert="reconvert"
        />
      </template>
    </USlideover>

    <USlideover v-model:open="treeOpen" side="left" title="Dossiers" :ui="{ content: 'max-w-xs' }">
      <template #body>
        <OCategoryTree v-model="cat" :categories="categories" root-label="Tous les modèles" class="h-[calc(100dvh-9rem)]" @update:model-value="treeOpen = false" />
      </template>
    </USlideover>

    <OFolderModal v-model:open="folderOpen" :roots="roots" @added="async (id: string) => { await loadRoots(); root = id }" @removed="async (id: string) => { await loadRoots(); if (root === id) root = '' }" />
    <OTextureViewer v-model:open="texOpen" v-model:index="texIndex" :entries="texEntries" />
    <OConvertModal v-if="model?.source" v-model:open="convertOpen" :keys="[model.source.key]" @started="started" />
  </div>
</template>
