<script setup lang="ts">
import type { Settings } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/settings` })

/**
 * Settings, stored by the server (workspace/settings.json): the conversions, exports and previews started from
 * any page use them. Every change is saved at once. Player and layout preferences stay in this browser.
 */
const sid = useSourceId()
const { values, saving, load, save, reset } = useSettings()
const { info: system, load: loadSystem } = useSystem()
const toast = useToast()

const s = ref<Settings | null>(null)
const failed = ref('')
onMounted(async () => {
  try {
    s.value = structuredClone(toRaw(await load(true)))
  } catch (e) {
    failed.value = apiError(e)
  }
  loadSystem()
  loadStorage()
})

let ready = false
const persist = debounce(async () => {
  if (!s.value) return
  try {
    await save(s.value as never)
  } catch (e) {
    toast.add({ title: 'Enregistrement impossible', description: apiError(e), color: 'error' })
  }
}, 400)
watch(
  s,
  () => {
    if (!ready) {
      ready = true
      return
    }
    persist()
  },
  { deep: true },
)

async function resetAll() {
  if (!confirm('Revenir à tous les réglages d’origine ?')) return
  ready = false
  await reset()
  s.value = structuredClone(toRaw(values.value!))
  toast.add({ title: 'Réglages d’origine rétablis', icon: 'i-ri-restart-line' })
}

const player = usePersisted('sound-player', { volume: 0.8, loop: false, next: true })

const QUALITIES = [
  { label: 'Maximum', value: 'max', description: 'Résolution du jeu pour toutes les textures (jusqu’à 8K), normales en 2K. Le plus fidèle, le plus lourd.' },
  { label: 'Élevée', value: 'high', description: 'Couleur jusqu’à 4K, normales et masques en 2K.' },
  { label: 'Équilibrée', value: 'balanced', description: 'Couleur 2K, normales 1K, masques 512. Bon compromis pour un serveur.' },
  { label: 'Légère', value: 'light', description: 'Couleur 1K, normales 512 : le plus petit addon.' },
]
const ENCODERS = ['Rapide', 'Bon', 'Meilleur']
const COLLISIONS = [
  { label: 'Celle du jeu (recommandé)', value: 'game' },
  { label: 'Par pièces', value: 'parts' },
  { label: 'Enveloppe unique', value: 'hull' },
  { label: 'Précise (CoACD, très lent)', value: 'coacd' },
]
const FORMATS = [
  { label: 'Automatique : ogg sans réencodage + flac', value: 'auto' },
  { label: 'FLAC (tout sans perte)', value: 'flac' },
  { label: 'WAV (non compressé)', value: 'wav' },
  { label: 'MP3 V0 (nécessite ffmpeg)', value: 'mp3' },
]
const LANGS = [
  { label: 'Toutes les pistes de voix', value: 'all' },
  { label: 'Anglais seulement', value: 'english' },
  { label: 'Piste neutre (xx) seulement', value: 'neutral' },
]
const TRIS = [
  { label: '30 000 triangles', value: 30000 },
  { label: '60 000 triangles', value: 60000 },
  { label: '90 000 triangles', value: 90000 },
  { label: '150 000 triangles', value: 150000 },
]
const SIZES = [256, 512, 1024, 2048].map((v) => ({ label: `${v} px`, value: v }))

// ---- storage & maintenance
const storage = ref<{ addon: number; previews: number; audio: number; cache: number; path: string } | null>(null)
const loadStorage = async () => (storage.value = await api<typeof storage.value>(`/${sid.value}/storage`).catch(() => null))
const sizes = computed(() =>
  storage.value
    ? [
        { label: 'Addon converti', value: fmtBytes(storage.value.addon), icon: 'i-ri-archive-line' },
        { label: 'Sons exportés', value: fmtBytes(storage.value.audio), icon: 'i-ri-music-2-line' },
        { label: 'Aperçus 3D', value: fmtBytes(storage.value.previews), icon: 'i-ri-eye-line' },
        { label: 'Caches', value: fmtBytes(storage.value.cache), icon: 'i-ri-database-2-line' },
      ]
    : [],
)
async function clearPreviews() {
  if (!confirm('Supprimer les aperçus 3D et les miniatures en cache ? Ils seront recréés à la demande.')) return
  await api(`/${sid.value}/previews/clear`, { method: 'POST' })
  toast.add({ title: 'Aperçus supprimés', icon: 'i-ri-delete-bin-line' })
  loadStorage()
}
async function stop() {
  if (!confirm('Arrêter omni ? La page ne répondra plus jusqu’au prochain lancement (Omni.cmd).')) return
  await api('/shutdown', { method: 'POST' }).catch(() => {})
  toast.add({ title: 'omni est arrêté', description: 'Relance-le avec Omni.cmd.', icon: 'i-ri-shut-down-line' })
}
const MAINTENANCE = computed(() => [
  { label: 'Réindexer les props', icon: 'i-ri-box-3-line', run: () => startJob(`/${sid.value}/catalog/rebuild`, { open: true }) },
  { label: 'Réindexer les textures', icon: 'i-ri-image-2-line', run: () => startJob(`/${sid.value}/textures/rebuild`, { open: true }) },
  { label: 'Relister les sons', icon: 'i-ri-music-2-line', run: () => startJob(`/${sid.value}/sounds/relist`, { open: true }) },
  { label: 'Recompiler le cœur Rust (WebAssembly)', icon: 'i-ri-cpu-line', run: () => startJob('/native/build', { open: true }) },
])
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-8 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-5xl space-y-3">
      <div class="flex flex-wrap items-center gap-3">
        <div class="min-w-0 flex-1">
          <h1 class="text-xl font-semibold text-highlighted">Réglages</h1>
          <p class="text-sm text-muted">Enregistrés sur ce poste et appliqués à toutes les conversions, exports et aperçus.</p>
        </div>
        <UBadge :label="saving ? 'Enregistrement…' : 'Enregistré'" :color="saving ? 'neutral' : 'success'" variant="subtle" :icon="saving ? 'i-ri-loader-4-line' : 'i-ri-check-line'" />
        <UButton label="Réglages d’origine" icon="i-ri-restart-line" color="neutral" variant="outline" size="sm" @click="resetAll" />
      </div>

      <UAlert v-if="failed" color="error" variant="subtle" icon="i-ri-error-warning-line" title="Réglages indisponibles" :description="failed" />
      <div v-if="!s && !failed" class="grid gap-3 lg:grid-cols-2">
        <USkeleton v-for="i in 4" :key="i" class="h-56" />
      </div>

      <template v-if="s">
        <div class="grid gap-3 lg:grid-cols-2">
          <UCard class="lg:col-span-2" :ui="{ body: 'grid gap-6 p-4 sm:p-4 md:grid-cols-[1.4fr_1fr]' }">
            <template #header>
              <h2 class="text-base font-semibold text-highlighted">Qualité des textures</h2>
              <p class="text-sm text-muted">Props et playermodels. Les textures BC1/BC3 du jeu sont copiées sans perte quelle que soit la qualité.</p>
            </template>
            <URadioGroup v-model="s.textures.quality" :items="QUALITIES" variant="card" />
            <div class="space-y-5">
              <UFormField :label="`Effort de compression DXT : ${ENCODERS[s.textures.encoder]}`" description="« Meilleur » gagne quelques dixièmes de dB pour un encodage plus long.">
                <USlider v-model="s.textures.encoder" :min="0" :max="2" :step="1" />
              </UFormField>
              <USwitch v-model="s.textures.lossless_normals" label="Normales sans compression" description="BGRA8888 : aucun artefact de bloc, fichiers 4× plus lourds." />
              <UFormField label="Taille des textures des aperçus 3D" description="Props de l’atelier Modèles.">
                <USelect v-model="s.viewer.texture_size" :items="SIZES" class="w-full" />
              </UFormField>
            </div>
          </UCard>

          <UCard :ui="{ body: 'space-y-5 p-4 sm:p-4' }">
            <template #header><h2 class="text-base font-semibold text-highlighted">Props</h2></template>
            <USwitch v-model="s.props.physics" label="Collision" description="Sans collision, le prop traverse le décor et les joueurs." />
            <UFormField v-if="s.props.physics" label="Forme de collision"><USelect v-model="s.props.collision" :items="COLLISIONS" class="w-full" /></UFormField>
            <UFormField :label="`Conversions en parallèle : ${s.props.workers}`" :description="`Processus StudioMDL simultanés (${system?.cpus ?? '?'} cœurs sur ce PC).`">
              <USlider v-model="s.props.workers" :min="1" :max="Math.max(4, system?.cpus ?? 16)" :step="1" />
            </UFormField>
            <USwitch v-model="s.props.blend" label="Produire aussi un .blend" description="Un fichier Blender par modèle (nécessite Blender)." />
          </UCard>

          <UCard :ui="{ body: 'space-y-5 p-4 sm:p-4' }">
            <template #header><h2 class="text-base font-semibold text-highlighted">Playermodels</h2></template>
            <UFormField label="Budget de géométrie" description="Si la tenue la plus lourde dépasse, un niveau de détail plus bas est choisi.">
              <USelect v-model="s.characters.max_tris" :items="TRIS" class="w-full" />
            </UFormField>
            <UFormField label="Textures de l’aperçu 3D" description="L’aperçu convertit les matériaux en qualité légère, dans un addon temporaire.">
              <USelect v-model="s.characters.preview_size" :items="SIZES" class="w-full" />
            </UFormField>
          </UCard>

          <UCard :ui="{ body: 'space-y-5 p-4 sm:p-4' }">
            <template #header>
              <h2 class="text-base font-semibold text-highlighted">Sons</h2>
              <p class="text-sm text-muted">Convertis par le cœur Rust, en parallèle.</p>
            </template>
            <UFormField label="Format"><USelect v-model="s.sounds.format" :items="FORMATS" class="w-full" /></UFormField>
            <UFormField label="Dialogues"><USelect v-model="s.sounds.languages" :items="LANGS" class="w-full" /></UFormField>
            <UFormField :label="`Fils de conversion : ${s.sounds.workers}`"><USlider v-model="s.sounds.workers" :min="1" :max="Math.max(4, (system?.cpus ?? 16) * 2)" :step="1" /></UFormField>
            <USwitch v-model="s.sounds.tags" label="Écrire les tags" description="Titre, événement ou conversation, locuteur, langue : lisibles dans tout lecteur." />
            <USwitch v-model="s.sounds.skip_stubs" label="Ignorer les amorces de musique" description="Les banques gardent les premières secondes des musiques streamées : la version complète est exportée à part." />
          </UCard>

          <UCard :ui="{ body: 'space-y-5 p-4 sm:p-4' }">
            <template #header><h2 class="text-base font-semibold text-highlighted">Général</h2></template>
            <USwitch v-model="s.general.open_browser" label="Ouvrir le navigateur au lancement" description="Omni.cmd ouvre l’interface tout seul." />
            <UFormField label="Dossier de Garry’s Mod" description="Vide : le dossier Steam par défaut.">
              <UInput v-model="s.paths.gmod" class="w-full" placeholder="C:\Program Files (x86)\Steam\steamapps\common\GarrysMod" />
            </UFormField>
            <UFormField label="Lecteur de sons (ce navigateur)">
              <div class="space-y-3">
                <USlider v-model="player.volume" :min="0" :max="1" :step="0.05" />
                <USwitch v-model="player.next" label="Enchaîner automatiquement" />
                <USwitch v-model="player.loop" label="Répéter le son en cours" />
              </div>
            </UFormField>
          </UCard>
        </div>

        <UCard :ui="{ body: 'space-y-4 p-4 sm:p-4' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Stockage et maintenance</h2>
            <p v-if="storage" class="truncate text-sm text-muted" :title="storage.path">Espace de travail : {{ storage.path }}</p>
          </template>
          <OStatGrid v-if="sizes.length" :stats="sizes" :cols="4" />
          <div class="flex flex-wrap gap-2">
            <UButton v-for="m in MAINTENANCE" :key="m.label" :label="m.label" :icon="m.icon" size="sm" color="neutral" variant="outline" @click="m.run()" />
            <UButton label="Vider les aperçus" icon="i-ri-delete-bin-line" size="sm" color="neutral" variant="outline" @click="clearPreviews" />
            <UButton label="Arrêter omni" icon="i-ri-shut-down-line" size="sm" color="error" variant="soft" class="ml-auto" @click="stop" />
          </div>
        </UCard>
      </template>
    </div>
  </div>
</template>
