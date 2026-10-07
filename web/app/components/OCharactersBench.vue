<script setup lang="ts">
import type * as THREE from 'three'
import type { GLTF } from 'three/addons/loaders/GLTFLoader.js'
import type {
  Character,
  CharacterPage,
  CharacterPreview,
  Facet,
  PreviewBuilding,
} from '~/utils/types'

/** Models › Characters: outfit families of the game, their variations (bodygroups, skins) and the playermodel build. */

const sid = useSourceId()
const route = useRoute()
const router = useRouter()
const jobs = useJobs()
const toast = useToast()

// ---- list + facets (URL-synced)
const q = ref(String(route.query.q ?? ''))
const body = ref(String(route.query.body ?? ''))
const role = ref(String(route.query.role ?? ''))
const mission = ref(String(route.query.mission ?? ''))
const kind = ref(String(route.query.kind ?? ''))
const built = ref(String(route.query.built ?? ''))

const page = ref<CharacterPage>({
  total: 0,
  items: [],
  facets: { mission: [], role: [], body: [], kind: [] },
})
const loading = ref(false)
const loaded = ref(false)
const failed = ref('')
const search = useTemplateRef<{ inputRef?: HTMLInputElement }>('search')

let seq = 0
async function fetchList() {
  const mine = ++seq
  loading.value = true
  failed.value = ''
  try {
    const params = new URLSearchParams({
      q: q.value,
      body: body.value,
      role: role.value,
      mission: mission.value,
      kind: kind.value,
      built: built.value,
      limit: '2000',
    })
    const r = await api<CharacterPage>(`/${sid.value}/characters?${params}`)
    if (mine === seq) page.value = r
  } catch (e) {
    if (mine === seq) failed.value = apiError(e)
  } finally {
    if (mine === seq) {
      loading.value = false
      loaded.value = true
    }
  }
}
const reload = debounce(fetchList, 200)
const query = () => ({
  tab: 'characters',
  ...Object.fromEntries(
    Object.entries({
      q: q.value,
      body: body.value,
      role: role.value,
      mission: mission.value,
      kind: kind.value,
      built: built.value,
      id: active.value?.id ?? '',
      t: tab.value !== 'look' ? tab.value : '',
    }).filter(([, v]) => v),
  ),
})
const sync = () => route.path.endsWith('/models') && router.replace({ query: query() })
watch([q, body, role, mission, kind, built], () => {
  reload()
  sync()
})
onMounted(async () => {
  await fetchList()
  // a shared link or a reload reopens the same character
  const id = String(route.query.id ?? '')
  if (!id) return
  const r = await api<CharacterPage>(
    `/${sid.value}/characters?q=${encodeURIComponent(id)}&limit=5`,
  ).catch(() => null)
  const found = r?.items.find((i) => i.id === id)
  if (found) select(found)
})

const facetItems = (facets: Facet[], all: string, label: (v: string) => string) => [
  { label: all, value: '' },
  ...facets.map((f) => ({ label: `${label(f.value)} (${f.n})`, value: f.value })),
]
const roleItems = computed(() => facetItems(page.value.facets.role, 'Tous les rôles', roleLabel))
const missionItems = computed(() =>
  facetItems(page.value.facets.mission, 'Tous les lieux', titleCase),
)
const kindItems = computed(() =>
  facetItems(page.value.facets.kind, 'Tous les types', (k) => KIND_LABEL[k] ?? titleCase(k)),
)
const bodyCount = (b: string) => page.value.facets.body.find((f) => f.value === b)?.n
const BODIES = ['', 'male_reg', 'fem_reg', 'male_large']

type Head = { type: 'head'; id: string; label: string; n: number }
type Entry = { type: 'item'; id: string; c: Character }
type Row = Head | Entry
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  let last = ''
  const count = new Map<string, number>()
  for (const c of page.value.items) count.set(c.mission, (count.get(c.mission) ?? 0) + 1)
  for (const c of page.value.items) {
    if (c.mission !== last) {
      last = c.mission
      out.push({
        type: 'head',
        id: `h:${c.mission}`,
        label: titleCase(c.mission || 'Divers'),
        n: count.get(c.mission) ?? 0,
      })
    }
    out.push({ type: 'item', id: c.id, c })
  }
  return out
})

// ---- preview
const viewer = useTemplateRef<{
  parser: GLTF['parser'] | null
  root: THREE.Object3D | null
  sync: () => void
}>('viewer')
const active = ref<Character | null>(null)
const meta = ref<CharacterPreview | null>(null)
const glb = ref<string>()
const busy = ref('')
const previewError = ref('')
const tab = ref(String(route.query.t ?? 'look'))
watch(tab, () => sync())
const state = reactive({ preset: 0, skin: 0, groups: [] as number[] })
let mats = new Map<number, Promise<THREE.Material>>()
let pick = 0

// leaving the page ends the wait for a preview that is still being built
onBeforeUnmount(() => pick++)

async function select(c: Character) {
  const mine = ++pick
  active.value = c
  sync()
  meta.value = null
  glb.value = undefined
  previewError.value = ''
  busy.value =
    'Préparation du personnage (matériaux, pose du squelette)… quelques secondes la première fois.'
  try {
    const url = `/${sid.value}/characters/${encodeURIComponent(c.id)}/preview`
    const t0 = Date.now()
    let m: CharacterPreview | PreviewBuilding
    // the first visit of a family builds its preview in the background: poll until it is ready
    while ((m = await api<CharacterPreview | PreviewBuilding>(url)).status === 'building') {
      if (mine !== pick) return
      const s = Math.round((Date.now() - t0) / 1000)
      busy.value = `Préparation du personnage : ${m.stage || 'matériaux, pose du squelette'}… ${s} s. Une seule fois par personnage, ensuite c’est instantané.`
      await new Promise((r) => setTimeout(r, 1000))
    }
    if (mine !== pick || m.status !== 'ready') return
    meta.value = m
    included.value = m.variants.map((v) => v.v)
    defaultVariant.value = m.variants[0]?.v ?? 0
    title.value = ''
    glb.value = `/api/${sid.value}/characters/${encodeURIComponent(c.id)}/glb?t=${Date.now()}`
  } catch (e) {
    if (mine === pick) previewError.value = apiError(e)
  } finally {
    if (mine === pick) busy.value = ''
  }
}

// a finished build makes the converted model viewable from here
watch(
  () => jobs.jobs.value.filter((j) => j.phase === 'done' && j.kind === 'character').length,
  async () => {
    const c = active.value
    if (!c || !meta.value) return
    const m = await api<CharacterPreview | PreviewBuilding>(
      `/${sid.value}/characters/${encodeURIComponent(c.id)}/preview`,
    ).catch(() => null)
    if (m?.status === 'ready' && meta.value) meta.value.built = m.built
  },
)

function onLoaded() {
  mats = new Map()
  applyPreset(0)
}

function applyPreset(i: number) {
  const m = meta.value
  const p = m?.presets[i]
  if (!m || !p) return
  state.preset = i
  state.skin = p.skin
  state.groups = m.groups.map((g) => p.bodygroups[g.name] ?? 0)
  apply()
}

function setSkin(i: number) {
  state.skin = i
  apply()
}
function setGroup(gi: number, v: number) {
  state.groups[gi] = v
  apply()
}

function apply() {
  const m = meta.value
  const v = viewer.value
  const root = v?.root
  const parser = v?.parser
  if (!m || !root || !parser) return
  const skin = m.skin_materials[state.skin] ?? {}
  root.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    const [tag, col] = mesh.name.split('|')
    const g = /^g(\d+)o(\d+)$/.exec(tag ?? '')
    mesh.visible = tag === 'base' || (!!g && state.groups[Number(g[1])] === Number(g[2]))
    const index = col ? skin[col] : undefined
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

const presetTip = (i: number) => {
  const m = meta.value
  const p = m?.presets[i]
  if (!m || !p) return ''
  const parts = m.groups.map(
    (g, _gi) => `${g.name} : ${g.options[p.bodygroups[g.name] ?? 0] ?? '—'}`,
  )
  return [`Variation ${p.variant} · skin ${p.skin}`, ...parts].join('\n')
}

// ---- export
const included = ref<number[]>([])
const defaultVariant = ref(0)
const title = ref('')
// shared with the Réglages page (server settings)
const quality = reactive({ tex: 'max', tris: 60000 })
const settingsStore = useSettings()
onMounted(async () => {
  const st = await settingsStore.load().catch(() => null)
  if (st) Object.assign(quality, { tex: st.textures.quality, tris: st.characters.max_tris })
})
const building = ref(false)

const allIncluded = computed(
  () => !!meta.value && included.value.length === meta.value.variants.length,
)
function toggleIncluded(v: number) {
  included.value = included.value.includes(v)
    ? included.value.filter((x) => x !== v)
    : [...included.value, v]
  if (!included.value.includes(defaultVariant.value)) defaultVariant.value = included.value[0] ?? 0
}

async function build() {
  const c = active.value
  if (!c || !included.value.length) return
  building.value = true
  try {
    const order = [
      defaultVariant.value,
      ...included.value.filter((v) => v !== defaultVariant.value),
    ]
    const { job } = await api<{ job: string }>(
      `/${sid.value}/characters/${encodeURIComponent(c.id)}/build`,
      {
        method: 'POST',
        body: {
          title: title.value,
          variants: order,
          tex_quality: quality.tex,
          max_tris: quality.tris,
        },
      },
    )
    settingsStore
      .save({ textures: { quality: quality.tex }, characters: { max_tris: quality.tris } })
      .catch(() => {})
    toast.add({
      title: 'Playermodel en cours de création',
      description: 'Compilation et textures : une à deux minutes.',
      icon: 'i-ri-play-large-line',
    })
    await jobs.track(job)
    jobs.open.value = true
  } catch (e) {
    toast.add({ title: 'Création impossible', description: apiError(e), color: 'error' })
  } finally {
    building.value = false
  }
}

function step(delta: number) {
  const items = page.value.items
  if (!items.length) return
  const i = items.findIndex((c) => c.id === active.value?.id)
  const next = items[Math.min(items.length - 1, Math.max(0, i < 0 ? 0 : i + delta))]
  if (next) select(next)
}
defineShortcuts({
  '/': () => search.value?.inputRef?.focus(),
  arrowdown: { usingInput: true, handler: () => step(1) },
  arrowup: { usingInput: true, handler: () => step(-1) },
})

const TABS = [
  { label: 'Apparence', value: 'look', icon: 'i-ri-t-shirt-line' },
  { label: 'Matériaux', value: 'materials', icon: 'i-ri-palette-line' },
  { label: 'Export GMod', value: 'export', icon: 'i-ri-hammer-line' },
]
const BUILT = [
  { label: 'Tous', value: '' },
  { label: 'Créés', value: 'yes' },
  { label: 'À créer', value: 'no' },
]

// ---- game materials and their raw textures
const texOpen = ref(false)
const texIndex = ref(0)
const materials = computed(() => meta.value?.materials ?? [])
const texEntries = computed(() => gameEntries(sid.value, materials.value))
function openTexture(mi: number, ti: number) {
  texIndex.value = gameEntryIndex(materials.value, mi, ti)
  texOpen.value = true
}
const QUALITY = [
  { label: 'Léger', value: 'light' },
  { label: 'Équilibré', value: 'balanced' },
  { label: 'Élevé', value: 'high' },
  { label: 'Maximum', value: 'max' },
]
const TRIS = [
  { label: 'Léger · 30 000 triangles', value: 30000 },
  { label: 'Standard · 60 000 triangles', value: 60000 },
  { label: 'Détaillé · 90 000 triangles', value: 90000 },
]
</script>

<template>
  <div class="flex h-full gap-3 px-3 pb-3 pt-[4.75rem] sm:pt-20">
    <!-- browser -->
    <UCard class="w-full min-w-0 lg:w-[23rem] lg:shrink-0" :class="active && 'max-lg:hidden'" :ui="panelUi">
      <template #header>
        <slot name="switch" />
        <UInput ref="search" v-model="q" icon="i-ri-search-line" size="lg" class="w-full" placeholder="Bond, garde, barman…" :loading="loading">
          <template #trailing>
            <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
            <UKbd v-else value="/" />
          </template>
        </UInput>

        <UFieldGroup class="w-full">
          <UButton
            v-for="b in BODIES"
            :key="b"
            :label="b ? `${bodyLabel(b)} ${bodyCount(b) ?? ''}`.trim() : 'Tous'"
            size="xs"
            color="neutral"
            class="flex-1 justify-center"
            :variant="body === b ? 'solid' : 'outline'"
            :disabled="!!b && !bodyCount(b) && body !== b"
            @click="body = b"
          />
        </UFieldGroup>

        <div class="grid grid-cols-2 gap-2">
          <USelectMenu v-model="role" :items="roleItems" value-key="value" size="sm" :search-input="false" class="w-full" />
          <USelectMenu v-model="mission" :items="missionItems" value-key="value" size="sm" :search-input="false" class="w-full" />
        </div>
        <div class="grid grid-cols-2 gap-2">
          <USelectMenu v-model="kind" :items="kindItems" value-key="value" size="sm" :search-input="false" class="w-full" />
          <USelectMenu v-model="built" :items="BUILT" size="sm" class="w-full" icon="i-ri-checkbox-circle-line" value-key="value" :search-input="false" />
        </div>

        <p class="text-xs tabular-nums text-muted">
          <template v-if="loading && !page.total">Chargement de l’index des tenues…</template>
          <template v-else>{{ page.total.toLocaleString('fr-FR') }} personnage{{ page.total > 1 ? 's' : '' }}</template>
        </p>
      </template>

      <UEmpty v-if="failed" icon="i-ri-error-warning-line" title="Chargement impossible" :description="failed" class="absolute inset-0" :actions="[{ label: 'Réessayer', onClick: fetchList }]" />
      <UEmpty
        v-else-if="!rows.length && loaded && !loading"
        icon="i-ri-user-search-line"
        title="Aucun personnage"
        description="Retire un filtre ou change de mot-clé."
        class="absolute inset-0"
        :actions="[{ label: 'Réinitialiser', color: 'neutral', variant: 'outline', onClick: () => { q = ''; body = ''; role = ''; mission = ''; kind = '' } }]"
      />
      <div v-else-if="!rows.length" class="space-y-2 p-3">
        <USkeleton v-for="n in 8" :key="n" class="h-12 w-full" />
      </div>
      <UScrollArea
        v-else
        v-slot="{ item }"
        :items="rows"
        :virtualize="{ estimateSize: 56, overscan: 10, getItemKey: (i: number) => rows[i]?.id ?? i }"
        class="absolute inset-0 overflow-y-auto"
      >
        <USeparator v-if="(item as Row).type === 'head'" :label="`${(item as Head).label} · ${(item as Head).n}`" class="bg-elevated px-3 py-1.5" :ui="{ label: 'text-xs font-semibold uppercase text-muted' }" />
        <div
          v-else
          role="option"
          :aria-selected="active?.id === (item as Entry).c.id"
          class="flex cursor-pointer items-center gap-3 border-b border-default px-3 py-2 transition-colors"
          :class="active?.id === (item as Entry).c.id ? 'bg-primary/10' : 'hover:bg-elevated'"
          @click="select((item as Entry).c)"
        >
          <UUser
            :name="titleCase((item as Entry).c.title)"
            :description="`${roleLabel((item as Entry).c.role)} · ${bodyLabel((item as Entry).c.body)}${(item as Entry).c.kind !== 'outfit' ? ` · ${KIND_LABEL[(item as Entry).c.kind] ?? (item as Entry).c.kind}` : ''}`"
            :avatar="{ text: (item as Entry).c.body === 'fem_reg' ? 'F' : 'H' }"
            class="min-w-0 flex-1"
            :ui="{ wrapper: 'min-w-0', name: 'truncate', description: 'truncate' }"
          />
          <UTooltip v-if="(item as Entry).c.reward" text="Tenue de récompense">
            <UIcon name="i-ri-trophy-line" class="size-4 shrink-0 text-warning" />
          </UTooltip>
          <UTooltip v-if="(item as Entry).c.built" text="Playermodel créé">
            <UIcon name="i-ri-checkbox-circle-fill" class="size-4 shrink-0 text-success" />
          </UTooltip>
          <UBadge :label="`${(item as Entry).c.count} var.`" color="neutral" variant="subtle" size="sm" />
        </div>
      </UScrollArea>
    </UCard>

    <!-- viewport -->
    <UCard class="min-w-0 flex-1" :class="!active && 'max-lg:hidden'" :ui="{ root: 'relative flex flex-col', body: 'relative min-h-0 flex-1 p-0 sm:p-0' }">
      <OViewer ref="viewer" mode="character" :url="glb" :busy="busy" @loaded="onLoaded">
        <template #overlay>
          <div class="flex items-start gap-2">
            <UButton class="lg:hidden" icon="i-ri-arrow-left-line" color="neutral" variant="subtle" aria-label="Retour à la liste" @click="active = null; sync()" />
            <WGlassCard v-if="active" :halo="false" class="min-w-0" body-class="px-3 py-2 sm:px-3 sm:py-2">
              <p class="truncate font-semibold text-highlighted">{{ titleCase(active.title) }}</p>
              <p class="truncate text-xs text-muted">
                {{ titleCase(active.mission) }} · {{ roleLabel(active.role) }} · {{ bodyLabel(active.body) }}
                <template v-if="meta"> · LOD {{ meta.lod }}</template>
              </p>
              <p v-if="active.variation" class="truncate text-xs text-dimmed" title="Nom de la tenue dans le jeu">{{ active.variation }}</p>
            </WGlassCard>
          </div>
        </template>
        <template #empty>
          <UEmpty
            v-if="previewError"
            icon="i-ri-error-warning-line"
            title="Aperçu impossible"
            :description="previewError"
            :actions="active ? [{ label: 'Réessayer', onClick: () => select(active!) }] : []"
          />
          <UEmpty v-else icon="i-ri-user-3-line" title="Choisis un personnage" description="Chaque personnage regroupe les variations de la tenue du jeu : bodygroups, skins et préréglages." />
        </template>
      </OViewer>
    </UCard>

    <!-- inspector -->
    <UCard
      v-if="meta && active"
      class="w-[24rem] shrink-0 max-xl:fixed max-xl:inset-x-3 max-xl:bottom-3 max-xl:z-30 max-xl:max-h-[55dvh] max-xl:w-auto 2xl:w-[26rem]"
      :ui="{ root: 'relative flex min-h-0 flex-col', header: 'p-2 sm:p-2', body: 'relative min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-4', footer: 'p-3 sm:p-3' }"
    >
      <template #header>
        <UTabs v-model="tab" :items="TABS" :content="false" size="sm" :ui="{ list: 'w-full' }" />
      </template>

      <template v-if="tab === 'look'">
        <section class="space-y-2">
          <div class="flex items-baseline justify-between">
            <h3 class="text-sm font-semibold text-highlighted">Préréglages</h3>
            <span class="text-xs text-muted">{{ meta.presets.length }} variation{{ meta.presets.length > 1 ? 's' : '' }} du jeu</span>
          </div>
          <div class="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
            <UTooltip v-for="(p, i) in meta.presets" :key="p.variant" :text="presetTip(i)" :ui="{ content: 'whitespace-pre-line' }">
              <UButton :label="`v${p.variant}`" size="xs" color="neutral" :variant="state.preset === i ? 'solid' : 'outline'" @click="applyPreset(i)" />
            </UTooltip>
          </div>
        </section>

        <section v-if="meta.skins > 1" class="space-y-2">
          <h3 class="text-sm font-semibold text-highlighted">Skin <span class="font-normal text-muted">({{ meta.skins }} jeux de couleurs)</span></h3>
          <div class="flex flex-wrap gap-1.5">
            <UButton v-for="s in meta.skins" :key="s" :label="String(s - 1)" size="xs" color="neutral" class="min-w-8 justify-center" :variant="state.skin === s - 1 ? 'solid' : 'outline'" @click="setSkin(s - 1)" />
          </div>
        </section>

        <section v-if="meta.groups.length" class="space-y-3">
          <h3 class="text-sm font-semibold text-highlighted">Bodygroups</h3>
          <UFormField v-for="(g, gi) in meta.groups" :key="g.name" :label="g.name" size="sm">
            <UFieldGroup v-if="g.options.length <= 3" class="w-full">
              <UButton
                v-for="(o, oi) in g.options"
                :key="oi"
                :label="o"
                size="xs"
                color="neutral"
                class="min-w-0 flex-1 justify-center"
                :variant="state.groups[gi] === oi ? 'solid' : 'outline'"
                @click="setGroup(gi, oi)"
              />
            </UFieldGroup>
            <USelect v-else :model-value="state.groups[gi]" :items="g.options.map((label, value) => ({ label, value }))" value-key="value" class="w-full" @update:model-value="setGroup(gi, Number($event))" />
          </UFormField>
        </section>
        <UAlert v-else color="neutral" variant="subtle" icon="i-ri-information-line" title="Pas de bodygroup" description="Toutes les variations portent les mêmes pièces : seules les couleurs changent." />
      </template>

      <OGameMaterials
        v-else-if="tab === 'materials'"
        :materials="materials"
        hint="Matériaux des tenues tels que le jeu les stocke (textures brutes, paramètres de couleur). Clique une texture pour la voir en grand."
        @texture="openTexture"
      />

      <template v-else>
        <UFormField label="Nom dans GMod" description="Affiché dans le menu des playermodels.">
          <UInput v-model="title" class="w-full" :placeholder="titleCase(active.title)" />
        </UFormField>

        <section class="space-y-2">
          <div class="flex items-baseline justify-between">
            <h3 class="text-sm font-semibold text-highlighted">Variations incluses</h3>
            <UButton :label="allIncluded ? 'Aucune' : 'Toutes'" size="xs" color="neutral" variant="link" @click="included = allIncluded ? [defaultVariant] : meta.variants.map((v) => v.v)" />
          </div>
          <p class="text-xs text-muted">Seules les pièces et couleurs portées par ces variations sont compilées : moins de variations, un modèle plus léger. L’étoile marque l’apparence au spawn.</p>
          <ul class="max-h-56 space-y-0.5 overflow-y-auto">
            <li v-for="v in meta.variants" :key="v.v" class="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-elevated">
              <UCheckbox :model-value="included.includes(v.v)" :label="`Variation ${v.v}`" class="flex-1" @update:model-value="toggleIncluded(v.v)" />
              <UButton
                :icon="defaultVariant === v.v ? 'i-ri-star-fill' : 'i-ri-star-line'"
                size="xs"
                :color="defaultVariant === v.v ? 'primary' : 'neutral'"
                variant="ghost"
                :disabled="!included.includes(v.v)"
                aria-label="Apparence par défaut"
                @click="defaultVariant = v.v"
              />
            </li>
          </ul>
        </section>

        <UFormField label="Qualité des textures">
          <UFieldGroup class="w-full">
            <UButton v-for="o in QUALITY" :key="o.value" :label="o.label" size="sm" color="neutral" class="flex-1 justify-center" :variant="quality.tex === o.value ? 'solid' : 'outline'" @click="quality.tex = o.value" />
          </UFieldGroup>
        </UFormField>
        <UFormField label="Budget de géométrie" description="Si la tenue la plus lourde dépasse, un niveau de détail plus bas est choisi.">
          <USelect v-model="quality.tris" :items="TRIS" value-key="value" class="w-full" />
        </UFormField>

        <UAlert color="neutral" variant="subtle" icon="i-ri-information-line" :title="meta.model" :description="`${meta.groups.length} bodygroup(s) · ${meta.skins} skin(s) · squelette ValveBiped : animations de GMod, ragdoll et hitboxes natifs.`" />
      </template>

      <template v-if="tab === 'export' || meta.built" #footer>
        <div class="flex flex-col gap-2">
          <UButton v-if="tab === 'export'" label="Créer le playermodel" icon="i-ri-hammer-line" color="primary" variant="solid" size="lg" block :loading="building" :disabled="!included.length" @click="build" />
          <UButton
            v-if="meta.built"
            label="Voir le playermodel converti"
            icon="i-ri-archive-line"
            color="success"
            variant="soft"
            :size="tab === 'export' ? 'md' : 'lg'"
            block
            :to="{ path: `/${sid}/viewer`, query: { m: meta.model } }"
          />
        </div>
      </template>
    </UCard>

    <OTextureViewer v-model:open="texOpen" v-model:index="texIndex" :entries="texEntries" />
  </div>
</template>
