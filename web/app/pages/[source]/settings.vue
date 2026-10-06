<script setup lang="ts">
import type { Settings } from '~/utils/types'

definePageMeta({ key: (r) => `${r.params.source}/settings` })

/**
 * Settings, stored by the server (workspace/config/settings.json): the conversions, exports and previews started from
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
  loadHome()
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

const confirm = useConfirm()
const updates = useUpdate()
const updateInfo = updates.info
const checking = ref(false)
async function checkNow() {
  checking.value = true
  try {
    await save({ updates: { check: s.value!.updates.check, token: s.value!.updates.token } })
    await updates.check(true)
  } finally {
    checking.value = false
  }
}
async function resetAll() {
  if (
    !(await confirm({
      title: 'Réinitialiser les réglages ?',
      description:
        'Tous les réglages (qualité, dossiers, fenêtre…) reviennent à leur valeur par défaut. Tes exports, conversions et jeux ne sont pas touchés.',
      confirmLabel: 'Réinitialiser les réglages',
      destructive: true,
    }))
  )
    return
  ready = false
  await reset()
  s.value = structuredClone(toRaw(values.value!))
  toast.add({ title: 'Réglages réinitialisés', icon: 'i-ri-restart-line' })
}
async function resetData() {
  const p = await api<{ count: number; exports: string; bytes: number }>('/reset/data').catch(
    () => null,
  )
  if (
    !(await confirm({
      title: 'Réinitialiser les données ?',
      description: `Supprime tous les exports (addon Garry’s Mod, glTF, sons, textures${p?.exports ? ` : ${p.exports}` : ''}), les conversions, les aperçus et les caches${p ? ` (${p.count} éléments, au moins ${fmtBytes(p.bytes)})` : ''}. Les réglages, la bibliothèque de jeux, les outils et les noms sont gardés. Ferme Garry’s Mod avant. Irréversible.`,
      confirmLabel: 'Supprimer les données',
      destructive: true,
    }))
  )
    return
  await startJob('/reset/data', {
    body: { confirm: true, exports: true },
    title: 'Réinitialisation des données',
    open: true,
  })
}

const player = usePersisted('sound-player', { volume: 0.8, loop: false, next: true })

const QUALITIES = [
  {
    label: 'Maximum',
    value: 'max',
    description:
      'Résolution du jeu pour toutes les textures (jusqu’à 8K), normales en 2K. Le plus fidèle, le plus lourd.',
  },
  { label: 'Élevée', value: 'high', description: 'Couleur jusqu’à 4K, normales et masques en 2K.' },
  {
    label: 'Équilibrée',
    value: 'balanced',
    description: 'Couleur 2K, normales 1K, masques 512. Bon compromis pour un serveur.',
  },
  {
    label: 'Légère',
    value: 'light',
    description: 'Couleur 1K, normales 512 : le plus petit addon.',
  },
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

// ---- Omni folder (exports + workspace)
interface HomeInfo {
  home: string
  workspace: string
  exports: string
  default: string
  pending: string
  synced: boolean
}
const home = ref<HomeInfo | null>(null)
const loadHome = async () => (home.value = await api<HomeInfo>('/home').catch(() => null))
const openFolder = (target: 'home' | 'exports' | 'workspace') =>
  api('/reveal', { method: 'POST', body: { target } }).catch((e) =>
    toast.add({ title: 'Ouverture impossible', description: apiError(e), color: 'error' }),
  )
async function chooseHome() {
  const r = await api<{ path: string }>('/home/pick', { method: 'POST' }).catch((e) => {
    toast.add({ title: 'Sélecteur indisponible', description: apiError(e), color: 'error' })
    return null
  })
  if (!r?.path) return
  if (
    !(await confirm({
      title: 'Déplacer le dossier Omni ?',
      description: `Les exports et l’espace de travail seront déplacés vers ${r.path} au prochain démarrage d’omni. Ferme Garry’s Mod avant de relancer.`,
      confirmLabel: 'Programmer le déplacement',
    }))
  )
    return
  try {
    home.value = await api<HomeInfo>('/home/move', { method: 'POST', body: { path: r.path } })
    toast.add({
      title: 'Déplacement programmé',
      description: 'Relance omni pour le terminer.',
      icon: 'i-ri-folder-transfer-line',
    })
  } catch (e) {
    toast.add({ title: 'Déplacement impossible', description: apiError(e), color: 'error' })
  }
}
async function cancelMove() {
  home.value = await api<HomeInfo>('/home/cancel', { method: 'POST' })
}

// ---- storage & maintenance
const storage = ref<{
  addon: number
  previews: number
  audio: number
  cache: number
  path: string
  exports: string
} | null>(null)
const loadStorage = async () =>
  (storage.value = await api<typeof storage.value>(`/${sid.value}/storage`).catch(() => null))
const sizes = computed(() =>
  storage.value
    ? [
        {
          label: 'Addon converti',
          value: fmtBytes(storage.value.addon),
          icon: 'i-ri-archive-line',
        },
        { label: 'Sons exportés', value: fmtBytes(storage.value.audio), icon: 'i-ri-music-2-line' },
        { label: 'Aperçus 3D', value: fmtBytes(storage.value.previews), icon: 'i-ri-eye-line' },
        { label: 'Caches', value: fmtBytes(storage.value.cache), icon: 'i-ri-database-2-line' },
      ]
    : [],
)
async function clearPreviews() {
  if (
    !(await confirm({
      title: 'Supprimer les aperçus ?',
      description: 'Les aperçus 3D et les miniatures en cache seront recréés à la demande.',
      confirmLabel: 'Supprimer',
      destructive: true,
    }))
  )
    return
  try {
    await api(`/${sid.value}/previews/clear`, { method: 'POST' })
    toast.add({ title: 'Aperçus supprimés', icon: 'i-ri-delete-bin-line' })
    loadStorage()
  } catch (e) {
    toast.add({ title: 'Suppression impossible', description: apiError(e), color: 'error' })
  }
}
async function stop() {
  if (
    !(await confirm({
      title: 'Arrêter omni ?',
      description: 'Il ne répondra plus jusqu’au prochain lancement.',
      confirmLabel: 'Arrêter',
      destructive: true,
    }))
  )
    return
  try {
    await api('/shutdown', { method: 'POST' })
  } catch (e) {
    // a conversion is running: stopping now cuts it short (it is recorded as interrupted and can be resumed)
    const busy = (e as { statusCode?: number }).statusCode === 409
    if (
      !busy ||
      !(await confirm({
        title: 'Un travail est en cours',
        description: `${apiError(e)}. Il sera interrompu ; « Reprendre » le relancera au prochain lancement.`,
        confirmLabel: 'Arrêter quand même',
        destructive: true,
      }))
    ) {
      if (!busy) toast.add({ title: 'Arrêt impossible', description: apiError(e), color: 'error' })
      return
    }
    await api('/shutdown?force=true', { method: 'POST' }).catch(() => {})
  }
  toast.add({
    title: 'omni est arrêté',
    description: 'Relance-le pour l’utiliser de nouveau.',
    icon: 'i-ri-shut-down-line',
  })
}
const MAINTENANCE = computed(() => [
  {
    label: 'Réindexer les props',
    icon: 'i-ri-box-3-line',
    run: () => startJob(`/${sid.value}/catalog/rebuild`, { open: true }),
  },
  {
    label: 'Réindexer les textures',
    icon: 'i-ri-image-2-line',
    run: () => startJob(`/${sid.value}/textures/rebuild`, { open: true }),
  },
  {
    label: 'Relister les sons',
    icon: 'i-ri-music-2-line',
    run: () => startJob(`/${sid.value}/sounds/relist`, { open: true }),
  },
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
        <UButton label="Réinitialiser les réglages" icon="i-ri-restart-line" color="neutral" variant="outline" size="sm" @click="resetAll" />
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
            <USwitch v-model="s.props.lods" label="Niveaux de détail (LOD)" description="Activé : les LOD du jeu deviennent des $lod Source (affichage allégé de loin). Désactivé : seul le niveau le plus détaillé est gardé." />
            <UFormField v-if="s.props.physics" label="Forme de collision"><USelect v-model="s.props.collision" :items="COLLISIONS" class="w-full" /></UFormField>
            <UFormField :label="`Conversions en parallèle : ${s.props.workers}`" :description="`Processus StudioMDL simultanés (${system?.cpus ?? '?'} cœurs sur ce PC).`">
              <USlider v-model="s.props.workers" :min="1" :max="Math.max(4, system?.cpus ?? 16)" :step="1" />
            </UFormField>
            <USwitch v-model="s.props.blend" label="Produire aussi un .blend" description="Un fichier Blender par modèle (nécessite Blender)." />
            <USwitch v-model="s.props.gltf" label="Produire aussi un .glb (glTF)" description="Un fichier glTF par modèle (Blender, Godot, Unity…), sans logiciel externe." />
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
            <div class="space-y-3 rounded-md border border-default p-3">
              <div class="flex items-center justify-between gap-2">
                <h3 class="text-sm font-medium text-highlighted">Mises à jour</h3>
                <UButton label="Vérifier maintenant" icon="i-ri-refresh-line" size="xs" color="neutral" variant="soft" :loading="checking" @click="checkNow" />
              </div>
              <USwitch v-model="s.updates.check" label="Chercher une mise à jour au lancement" />
              <UFormField label="Jeton GitHub" description="Nécessaire tant que le dépôt est privé (lecture des versions).">
                <UInput v-model="s.updates.token" type="password" class="w-full" placeholder="ghp_…" autocomplete="off" />
              </UFormField>
              <p v-if="updateInfo" class="text-xs" :class="updateInfo.error ? 'text-error' : 'text-muted'">
                {{ updateInfo.error || (updateInfo.available ? `omni ${updateInfo.latest} est disponible.` : `omni ${updateInfo.current} est à jour.`) }}
              </p>
            </div>
            <USwitch v-model="s.general.open_browser" label="Ouvrir le navigateur au lancement" description="Pour la commande « omni ui » (l’application a sa propre fenêtre)." />
            <UFormField label="Dossier des modèles et matériaux dans l’addon" description="Champ libre : models/&lt;dossier&gt;/&lt;jeu&gt; et materials/&lt;dossier&gt;/&lt;jeu&gt;. Exemples : omni, wissem/omni, import/wissem. Reconvertis les modèles après un changement.">
              <UInput v-model="s.general.namespace" class="w-full" placeholder="omni" />
            </UFormField>
            <UFormField label="Dossier de Garry’s Mod" description="Vide : le dossier Steam par défaut.">
              <UInput v-model="s.paths.gmod" class="w-full" placeholder="C:\Program Files (x86)\Steam\steamapps\common\GarrysMod" />
            </UFormField>
            <UFormField v-if="s.texts" label="Clé des textes du jeu (LOCR)" description="32 chiffres hexadécimaux. Les textes d’affichage (noms de tenues, de lieux) sont chiffrés dans 007 First Light : sans clé, omni utilise les noms internes des tenues.">
              <UInput v-model="s.texts.locr_key" class="w-full font-mono" placeholder="00112233445566778899aabbccddeeff" autocomplete="off" />
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
            <h2 class="text-base font-semibold text-highlighted">Dossiers</h2>
            <p class="text-sm text-muted">Tout ce qu’omni écrit tient dans un seul dossier : tes exports d’un côté, ce que l’application gère de l’autre.</p>
          </template>
          <div v-if="home" class="space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-default p-3">
              <div class="min-w-0">
                <h3 class="text-sm font-medium text-highlighted">Dossier Omni</h3>
                <p class="truncate font-mono text-xs text-muted" :title="home.home">{{ home.home }}</p>
              </div>
              <div class="flex gap-2">
                <UButton label="Ouvrir" icon="i-ri-folder-open-line" size="xs" color="neutral" variant="soft" @click="openFolder('home')" />
                <UButton label="Déplacer…" icon="i-ri-folder-transfer-line" size="xs" color="neutral" variant="outline" @click="chooseHome" />
              </div>
            </div>
            <UAlert v-if="home.pending" color="info" variant="subtle" icon="i-ri-time-line" title="Déplacement programmé" :description="`Vers ${home.pending} au prochain démarrage d’omni.`" :actions="[{ label: 'Annuler', color: 'neutral', variant: 'outline', onClick: cancelMove }]" />
            <UAlert v-if="home.synced" color="warning" variant="subtle" icon="i-ri-cloud-line" title="Dossier synchronisé" description="Ce dossier semble synchronisé dans le cloud (OneDrive…). Les exports sont très volumineux : déplace-le vers un disque local." />
            <div class="grid gap-3 sm:grid-cols-2">
              <UFormField label="Exports" description="Addon Garry’s Mod, glTF, sons, textures. Vide : exports dans le dossier Omni.">
                <div class="flex gap-2">
                  <UInput v-model="s.paths.exports" class="w-full" :placeholder="home.exports" />
                  <UButton icon="i-ri-folder-open-line" color="neutral" variant="soft" aria-label="Ouvrir les exports" @click="openFolder('exports')" />
                </div>
              </UFormField>
              <UFormField label="Espace de travail" description="Géré par omni : réglages, catalogues, caches, outils, journaux.">
                <div class="flex gap-2">
                  <UInput :model-value="home.workspace" class="w-full" readonly />
                  <UButton icon="i-ri-folder-open-line" color="neutral" variant="soft" aria-label="Ouvrir l’espace de travail" @click="openFolder('workspace')" />
                </div>
              </UFormField>
              <UFormField label="Ressources extraites (007)" description="Seulement si tu as extrait le jeu toi-même. Vide : lecture directe des paquets du jeu.">
                <UInput v-model="s.paths.assets" class="w-full" placeholder="…\Assets\Sorted" />
              </UFormField>
            </div>
          </div>
        </UCard>

        <UCard :ui="{ body: 'space-y-4 p-4 sm:p-4' }">
          <template #header>
            <h2 class="text-base font-semibold text-highlighted">Stockage et maintenance</h2>
            <p v-if="storage" class="truncate text-sm text-muted" :title="storage.path">Espace de travail : {{ storage.path }} · Exports : {{ storage.exports }}</p>
          </template>
          <OStatGrid v-if="sizes.length" :stats="sizes" :cols="4" />
          <div class="flex flex-wrap gap-2">
            <UButton v-for="m in MAINTENANCE" :key="m.label" :label="m.label" :icon="m.icon" size="sm" color="neutral" variant="outline" @click="m.run()" />
            <UButton label="Vider les aperçus" icon="i-ri-delete-bin-line" size="sm" color="neutral" variant="outline" @click="clearPreviews" />
            <UButton label="Réinitialiser les données" icon="i-ri-eraser-line" size="sm" color="error" variant="outline" @click="resetData" />
            <UButton label="Arrêter omni" icon="i-ri-shut-down-line" size="sm" color="error" variant="soft" class="ml-auto" @click="stop" />
          </div>
        </UCard>
      </template>
    </div>
  </div>
</template>
