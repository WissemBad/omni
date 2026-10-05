<script setup lang="ts">
import type { Facet, Sound, SoundPage, SoundStatus } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/sounds` })

const sid = useSourceId()
const jobs = useJobs()
const toast = useToast()

const status = ref<SoundStatus | null>(null)
const { load: loadSettings, save: saveSettings } = useSettings()
const format = reactive({ value: 'auto', match: '', languages: 'all', force: false })
const starting = ref(false)
onMounted(async () => {
  const st = await loadSettings().catch(() => null)
  if (st) Object.assign(format, { value: st.sounds.format, languages: st.sounds.languages })
})

const running = computed(() => jobs.jobs.value.find((j) => j.kind === 'sounds' && j.source === sid.value && j.phase === 'running'))

async function loadStatus() {
  status.value = await api<SoundStatus>(`/${sid.value}/sounds/status`).catch(() => null)
}

const LANGS = [
  { label: 'Toutes les pistes de voix', value: 'all' },
  { label: 'Anglais seulement', value: 'english' },
  { label: 'Piste neutre (xx) seulement', value: 'neutral' },
]
const FORMATS = [
  {
    label: 'Automatique (recommandé)',
    value: 'auto',
    description: 'Vorbis du jeu en .ogg sans réencodage (qualité identique), le reste en .flac sans perte. ≈ 13 Go pour tout le jeu.',
  },
  { label: 'FLAC', value: 'flac', description: 'Tout décodé, sans perte : un seul format, ≈ 80 Go.' },
  { label: 'WAV', value: 'wav', description: 'Tout décodé, non compressé : pour le montage, ≈ 140 Go.' },
  { label: 'MP3 (V0)', value: 'mp3', description: 'Réencode avec perte : ≈ 19 Go. Seulement si un outil n’accepte que du MP3.' },
]

async function start() {
  starting.value = true
  try {
    saveSettings({ sounds: { format: format.value, languages: format.languages } }).catch(() => {})
    const { job } = await api<{ job: string }>(`/${sid.value}/sounds/export`, {
      method: 'POST',
      body: { format: format.value, match: format.match, languages: format.languages, force: format.force, clean: format.force && !format.match },
    })
    toast.add({ title: 'Export des sons lancé', description: 'Reprise automatique si interrompu.', icon: 'i-ri-play-large-line' })
    await jobs.track(job)
    jobs.open.value = true
    jobs.schedule()
  } catch (e) {
    toast.add({ title: 'Export impossible', description: apiError(e), color: 'error' })
  } finally {
    starting.value = false
  }
}

// ---- explorer
const q = ref('')
const top = ref('')
const lang = ref('')
const onlyNamed = ref(false)
const langs = ref<Facet[]>([])
const items = ref<Sound[]>([])
const total = ref(0)
const tops = ref<Facet[]>([])
const loading = ref(false)
const loaded = ref(false)
const scroll = useTemplateRef<{ $el: HTMLElement }>('scroll')
const search = useTemplateRef<{ inputRef?: HTMLInputElement }>('search')

let seq = 0
async function fetchPage(reset: boolean) {
  const mine = ++seq
  loading.value = true
  try {
    const params = new URLSearchParams({ q: q.value, top: top.value, lang: lang.value, named: onlyNamed.value ? '1' : '0', limit: '200', offset: reset ? '0' : String(items.value.length) })
    const r = await api<SoundPage>(`/${sid.value}/sounds?${params}`)
    if (mine !== seq) return
    total.value = r.total
    tops.value = r.tops
    langs.value = r.langs ?? []
    items.value = reset ? r.items : [...items.value, ...r.items]
    if (reset) scroll.value?.$el?.scrollTo({ top: 0 })
  } finally {
    if (mine === seq) {
      loading.value = false
      loaded.value = true
    }
  }
}
function more() {
  const el = scroll.value?.$el
  if (el && !loading.value && items.value.length < total.value && el.scrollTop + el.clientHeight > el.scrollHeight - 900) fetchPage(false)
}
const reload = debounce(() => fetchPage(true), 220)
watch([q, top, lang, onlyNamed], reload)

onMounted(async () => {
  await loadStatus()
  if (status.value?.exported) fetchPage(true)
})
watch(running, async (now, before) => {
  if (before && !now) {
    await loadStatus()
    fetchPage(true)
  }
})

const topTabs = computed(() => [
  { label: `Tout ${status.value?.count ? `(${status.value.count.toLocaleString('fr-FR')})` : ''}`, value: '' },
  ...tops.value.map((t) => ({ label: `${titleCase(t.value)} (${t.n.toLocaleString('fr-FR')})`, value: t.value })),
])
const topModel = computed({ get: () => top.value, set: (v: string) => (top.value = v ?? '') })

// ---- playback
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref<string | null>(null)
const time = ref(0)
const length = ref(0)
const volume = usePersisted('sound-player', { volume: 0.8, loop: false, next: true })

/** Best readable name: the file's own, else the first event name that points at it, else the hash. */
const niceName = (s: Sound) => (s.named ? titleCase(leaf(s.file).replace(/\.[a-z0-9]+$/i, '')) : s.title || titleCase(leaf(s.aliases[0] ?? s.file).replace(/\.[a-z0-9]+$/i, '')))
const describe = (s: Sound) => {
  if (s.album && s.album !== niceName(s)) return [s.album, s.language].filter(Boolean).join(' · ')
  if (s.named) return s.file.split('/').slice(0, -1).join('/')
  return s.alias_count > 1 ? `${s.alias_count} noms d’événements` : 'Identifiant seul : aucun nom retrouvé'
}
const langItems = computed(() => [{ label: 'Toutes les langues', value: '' }, ...langs.value.map((l) => ({ label: `${l.value} (${l.n.toLocaleString('fr-FR')})`, value: l.value }))])
const current = computed(() => items.value.find((i) => i.file === playing.value) ?? null)

function play(s: Sound) {
  const a = audio.value
  if (!a) return
  a.src = `/api/${sid.value}/sounds/file?path=${encodeURIComponent(s.file)}`
  a.volume = volume.value.volume
  a.play().catch(() => (playing.value = null))
  playing.value = s.file
}
function toggle(s: Sound) {
  if (playing.value === s.file) pause()
  else play(s)
}
function pause() {
  const a = audio.value
  if (!a) return
  if (a.paused) a.play().catch(() => {})
  else a.pause()
}
function stop() {
  audio.value?.pause()
  playing.value = null
}
function skip(d: number) {
  const i = items.value.findIndex((x) => x.file === playing.value)
  const n = items.value[i + d]
  if (n) play(n)
  else if (d > 0) more()
}
function ended() {
  if (volume.value.loop && current.value) play(current.value)
  else if (volume.value.next) skip(1)
  else playing.value = null
}
const seek = (v: number | undefined) => {
  if (audio.value && v !== undefined) audio.value.currentTime = v
}
watch(() => volume.value.volume, (v) => audio.value && (audio.value.volume = v))
const paused = ref(false)
const summaryRows = computed(() => {
  const s = status.value?.summary as Record<string, number> | null
  if (!s) return []
  return [
    ['Sons uniques', Number(s.unique).toLocaleString('fr-FR')],
    ['Poids', fmtBytes(Number(s.bytes))],
    ['Erreurs', String(s.errors)],
    ...(s.stubs_skipped ? [['Amorces ignorées', Number(s.stubs_skipped).toLocaleString('fr-FR')]] : []),
    ...(s.engine ? [['Moteur', String(s.engine) === 'rust' ? 'cœur Rust' : 'processus externes']] : []),
    ['Durée de l’export', `${Math.round(Number(s.seconds) / 60)} min`],
  ]
})

const revealSound = (path: string) => api(`/${sid.value}/sounds/reveal?path=${encodeURIComponent(path)}`, { method: 'POST' }).catch(() => {})

defineShortcuts({
  '/': () => search.value?.inputRef?.focus(),
  escape: stop,
  ' ': () => playing.value && pause(),
  arrowright: () => skip(1),
  arrowleft: () => skip(-1),
})
</script>

<template>
  <div class="flex h-full gap-3 px-3 pb-3 pt-[4.75rem] sm:pt-20 max-lg:flex-col max-lg:overflow-y-auto">
    <!-- export -->
    <UCard
      class="w-full shrink-0 lg:w-[26rem]"
      :ui="{ root: 'flex min-h-0 flex-col', header: 'p-4 sm:px-4', body: 'min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-4', footer: 'p-4 sm:px-4' }"
    >
      <template #header>
        <h2 class="text-base font-semibold text-highlighted">Exporter les sons</h2>
        <p class="mt-1 text-sm text-muted">
          Tout l’audio du jeu dans une arborescence nommée (voix par langue et conversation, effets par événement), sans doublons, avec un index.
        </p>
      </template>

      <UAlert v-if="status?.exported" color="success" variant="subtle" icon="i-ri-checkbox-circle-line" :title="`${status.count.toLocaleString('fr-FR')} sons exportés`">
        <template #description>
          <p class="break-all">{{ status.path }}</p>
          <dl v-if="summaryRows.length" class="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-xs">
            <template v-for="[k, v] in summaryRows" :key="k">
              <dt class="text-muted">{{ k }}</dt>
              <dd class="text-right tabular-nums text-toned">{{ v }}</dd>
            </template>
          </dl>
        </template>
      </UAlert>

      <UFormField label="Format">
        <URadioGroup v-model="format.value" :items="FORMATS" variant="card" />
      </UFormField>

      <UFormField label="Dialogues">
        <USelect v-model="format.languages" :items="LANGS" class="w-full" />
      </UFormField>

      <UFormField label="Filtre (facultatif)" description="Ne garder que les chemins contenant ce texte, par exemple « voices/english » ou « events/mx ».">
        <UInput v-model="format.match" class="w-full" icon="i-ri-filter-3-line" placeholder="Tout exporter" />
      </UFormField>

      <USwitch v-if="status?.exported" v-model="format.force" label="Tout réécrire" description="Applique les nouveaux noms (musique, banques) et les tags aux fichiers déjà exportés ; retire les fichiers de l’ancien nommage." />

      <div v-if="running" class="space-y-2">
        <UProgress size="sm" :model-value="running.total ? running.done : null" :max="running.total || undefined" />
        <div class="flex items-center justify-between gap-2 text-xs tabular-nums text-muted">
          <span>{{ running.total ? `${running.done.toLocaleString('fr-FR')} / ${running.total.toLocaleString('fr-FR')}` : 'Préparation…' }}</span>
          <UButton label="Annuler" icon="i-ri-stop-circle-line" size="xs" color="neutral" variant="outline" @click="jobs.cancel(running.id)" />
        </div>
        <p class="truncate text-xs text-muted">{{ running.last || 'Démarrage…' }}</p>
      </div>

      <template #footer>
        <UButton
          :label="running ? 'Export en cours…' : status?.exported ? 'Relancer / compléter l’export' : 'Lancer l’export'"
          icon="i-ri-download-2-line"
          color="primary"
          variant="solid"
          size="lg"
          block
          :loading="starting || !!running"
          @click="start"
        />
      </template>
    </UCard>

    <!-- explorer -->
    <UCard class="min-w-0 flex-1 max-lg:min-h-[28rem]" :ui="panelUi">
      <template v-if="status?.exported" #header>
        <UInput ref="search" v-model="q" icon="i-ri-search-line" size="lg" class="w-full" placeholder="Rechercher un son (nom, locuteur, événement)…" :loading="loading">
          <template #trailing>
            <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
            <UKbd v-else value="/" />
          </template>
        </UInput>
        <UTabs v-model="topModel" :items="topTabs" :content="false" size="sm" :ui="{ list: 'overflow-x-auto' }" />
        <div class="flex items-center gap-2">
          <p class="text-xs tabular-nums text-muted">{{ total.toLocaleString('fr-FR') }} son{{ total > 1 ? 's' : '' }}</p>
          <USelectMenu v-if="langs.length" v-model="lang" :items="langItems" size="xs" class="ml-auto w-44" icon="i-ri-translate-2" value-key="value" :search-input="false" />
          <USwitch v-model="onlyNamed" size="sm" label="Nommés" :class="!langs.length && 'ml-auto'" />
          <UTooltip text="Ouvrir le dossier">
            <UButton icon="i-ri-folder-open-line" size="xs" color="neutral" variant="ghost" aria-label="Ouvrir le dossier" @click="revealSound('')" />
          </UTooltip>
        </div>
      </template>

      <template v-if="status?.exported">
        <UEmpty v-if="!items.length && loaded && !loading" icon="i-ri-music-2-line" title="Aucun son" description="Change de recherche ou de dossier." class="absolute inset-0" />
        <div v-else-if="!items.length" class="space-y-2 p-3">
          <USkeleton v-for="n in 9" :key="n" class="h-12 w-full" />
        </div>
        <UScrollArea
          v-else
          ref="scroll"
          v-slot="{ item }"
          :items="items"
          :virtualize="{ estimateSize: 52, overscan: 12, getItemKey: (i: number) => items[i]?.file ?? i }"
          class="absolute inset-0 overflow-y-auto"
          @scroll="(s: boolean) => !s && more()"
        >
          <div class="flex items-center gap-3 border-b border-default px-3 py-2 transition-colors" :class="playing === (item as Sound).file ? 'bg-primary/10' : 'hover:bg-elevated'">
            <UButton
              :icon="playing === (item as Sound).file ? 'i-ri-pause-fill' : 'i-ri-play-fill'"
              :color="playing === (item as Sound).file ? 'primary' : 'neutral'"
              :variant="playing === (item as Sound).file ? 'solid' : 'soft'"
              size="sm"
              aria-label="Écouter"
              @click="toggle(item as Sound)"
            />
            <UUser
              :name="niceName(item as Sound)"
              :description="describe(item as Sound)"
              class="min-w-0 flex-1"
              :ui="{ wrapper: 'min-w-0', name: 'truncate', description: 'truncate' }"
            />
            <span class="hidden text-xs tabular-nums text-muted sm:block">{{ fmtDuration((item as Sound).seconds) }}</span>
            <UBadge v-if="!(item as Sound).named && !(item as Sound).aliases.length" label="sans nom" color="warning" variant="subtle" size="sm" class="hidden sm:inline-flex" />
            <UBadge :label="(item as Sound).file.split('.').pop()?.toUpperCase()" color="neutral" variant="outline" size="sm" class="hidden sm:inline-flex" />
            <UTooltip text="Copier le chemin">
              <UButton icon="i-ri-file-copy-line" color="neutral" variant="ghost" size="xs" aria-label="Copier le chemin" @click="copy((item as Sound).file, 'Chemin copié')" />
            </UTooltip>
            <UTooltip text="Afficher dans l’Explorateur">
              <UButton icon="i-ri-folder-open-line" color="neutral" variant="ghost" size="xs" aria-label="Afficher" @click="revealSound((item as Sound).file)" />
            </UTooltip>
          </div>
        </UScrollArea>
      </template>
      <UEmpty
        v-else
        icon="i-ri-music-2-line"
        title="Aucun son exporté pour l’instant"
        description="Lance l’export à gauche : l’explorateur apparaît ici, avec l’écoute directe de chaque son."
        class="absolute inset-0"
      />

      <template v-if="playing" #footer>
        <div class="space-y-2">
          <div class="flex items-center gap-2">
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium text-highlighted">{{ current ? niceName(current) : leaf(playing) }}</p>
              <p class="truncate text-xs text-muted">{{ current?.aliases.slice(0, 2).join(' · ') || playing }}</p>
            </div>
            <UButton icon="i-ri-skip-back-fill" size="sm" color="neutral" variant="ghost" aria-label="Précédent" @click="skip(-1)" />
            <UButton :icon="paused ? 'i-ri-play-fill' : 'i-ri-pause-fill'" size="sm" color="primary" aria-label="Lecture / pause" @click="pause" />
            <UButton icon="i-ri-skip-forward-fill" size="sm" color="neutral" variant="ghost" aria-label="Suivant" @click="skip(1)" />
            <UTooltip text="Répéter">
              <UButton icon="i-ri-repeat-line" size="sm" :color="volume.loop ? 'primary' : 'neutral'" :variant="volume.loop ? 'soft' : 'ghost'" aria-label="Répéter" @click="volume.loop = !volume.loop" />
            </UTooltip>
            <UTooltip text="Enchaîner automatiquement">
              <UButton icon="i-ri-play-list-line" size="sm" :color="volume.next ? 'primary' : 'neutral'" :variant="volume.next ? 'soft' : 'ghost'" aria-label="Enchaîner" @click="volume.next = !volume.next" />
            </UTooltip>
            <UButton icon="i-ri-stop-fill" size="sm" color="neutral" variant="ghost" aria-label="Arrêter" @click="stop" />
          </div>
          <div class="flex items-center gap-3">
            <span class="w-10 text-xs tabular-nums text-muted">{{ fmtDuration(time) }}</span>
            <USlider :model-value="time" :max="length || 1" :step="0.01" size="xs" class="flex-1" @update:model-value="seek($event as number)" />
            <span class="w-10 text-right text-xs tabular-nums text-muted">{{ fmtDuration(length) }}</span>
            <UIcon name="i-ri-volume-up-line" class="size-4 text-muted" />
            <USlider v-model="volume.volume" :min="0" :max="1" :step="0.05" size="xs" class="w-20" />
          </div>
        </div>
      </template>
    </UCard>

    <audio ref="audio" class="hidden" @ended="ended" @play="paused = false" @pause="paused = true" @timeupdate="time = audio?.currentTime ?? 0" @loadedmetadata="length = audio?.duration ?? 0" />
  </div>
</template>
