<script setup lang="ts">
import type { Job } from '~/utils/types'

definePageMeta({ key: 'setup' })

/**
 * Installation: omni ships without any game data. This page finds the game, extracts the resources omni reads, downloads
 * the readable names and the model compiler, and checks Garry's Mod. Every step can be redone from Réglages.
 */
const { status, running, load } = useSetup()
const jobs = useJobs()
const toast = useToast()

const failed = ref('')
const busy = ref('')
const gamePath = ref('')
const gmodPath = ref('')
const estimate = ref<{ bytes: number; files: number } | null>(null)

async function refresh() {
  try {
    const s = await load()
    if (s) {
      gamePath.value = gamePath.value || s.game.path
      gmodPath.value = gmodPath.value || s.gmod.path
    }
  } catch (e) {
    failed.value = apiError(e)
  }
}
onMounted(async () => {
  await refresh()
  if (status.value && !status.value.game.ok && !status.value.assets.ok) await detect(true)
})

// the running step moves the page forward on its own
// the job list is pushed by the server (see useJobs): a finished setup step refreshes the status, nothing polls
watch(
  () =>
    jobs.jobs.value
      .filter((j) => j.kind === 'setup' && !['queued', 'running'].includes(j.phase))
      .map((j) => j.id + j.phase)
      .join(),
  () => refresh(),
)

async function detect(silent = false) {
  busy.value = 'detect'
  try {
    const r = await api<{ found: Record<string, string>; status: typeof status.value }>(
      '/setup/detect',
      { method: 'POST' },
    )
    status.value = r.status
    if (r.found.game) gamePath.value = r.found.game
    if (r.found.gmod) gmodPath.value = r.found.gmod
    if (!silent) {
      toast.add({
        title: Object.keys(r.found).length ? 'Détection terminée' : 'Rien trouvé automatiquement',
        description: Object.keys(r.found).length
          ? Object.values(r.found).join('\n')
          : 'Choisis les dossiers à la main.',
        icon: 'i-ri-search-eye-line',
      })
    }
    await loadEstimate()
  } finally {
    busy.value = ''
  }
}

async function pick(kind: 'game' | 'gmod') {
  try {
    const { path } = await api<{ path: string }>(`/setup/pick?kind=${kind}`, { method: 'POST' })
    if (!path) return
    if (kind === 'game') gamePath.value = path
    if (kind === 'gmod') gmodPath.value = path
    await savePath(kind)
  } catch (e) {
    toast.add({ title: 'Sélecteur indisponible', description: apiError(e), color: 'error' })
  }
}

async function savePath(kind: 'game' | 'gmod') {
  const value = { game: gamePath, gmod: gmodPath }[kind].value
  try {
    status.value = await api('/setup/paths', { method: 'POST', body: { [kind]: value } })
    if (kind === 'game') {
      gamePath.value = status.value!.game.path || value
      await loadEstimate()
    }
  } catch (e) {
    toast.add({ title: 'Chemin refusé', description: apiError(e), color: 'error' })
  }
}

async function loadEstimate() {
  estimate.value = null
  if (!status.value?.game.ok) return
  estimate.value = await api<{ bytes: number; files: number }>('/setup/estimate').catch(() => null)
}
watch(
  () => status.value?.game.ok,
  (ok) => ok && !estimate.value && loadEstimate(),
  { immediate: true },
)

async function start(step: 'extract' | 'names' | 'studiomdl' | 'auto') {
  busy.value = step
  try {
    const { job } = await api<{ job: string }>(`/setup/${step}`, { method: 'POST' })
    await jobs.track(job)
  } catch (e) {
    toast.add({ title: 'Impossible de lancer', description: apiError(e), color: 'error' })
  } finally {
    busy.value = ''
  }
}

const spaceShort = computed(
  () => !!estimate.value && !!status.value && status.value.free_bytes < estimate.value.bytes * 1.1,
)
const step = computed(() => {
  const s = status.value
  if (!s) return 0
  if (!s.assets.ok) return 1
  if (!s.names.ok) return 2
  return s.can_convert ? 5 : 3
})
const pct = (j: Job) => (j.total ? Math.round((j.done / j.total) * 100) : 0)
const last = (j: Job) => j.last || ''
const finish = () => navigateTo('/')
</script>

<template>
  <div class="h-full overflow-y-auto px-3 pb-10 pt-[4.75rem] sm:pt-20">
    <div class="mx-auto max-w-3xl space-y-3">
      <div class="space-y-1 px-1">
        <h1 class="text-2xl font-semibold text-highlighted">Installation d’omni</h1>
        <p class="text-sm text-muted">
          omni est livré sans aucun fichier du jeu. Il lit ceux de <em>ta</em> copie du jeu, sur ce PC, et ne les envoie nulle part.
          Quatre étapes, une seule fois.
        </p>
      </div>

      <UAlert v-if="failed" color="error" variant="subtle" icon="i-ri-error-warning-line" title="Installation indisponible" :description="failed" />

      <!-- one button: everything that is missing, in order -->
      <UCard v-if="status?.game.ok && !status.can_convert" :ui="{ body: 'space-y-3 p-4 sm:p-5' }">
        <div class="flex flex-wrap items-center gap-3">
          <div class="min-w-0 flex-1">
            <h2 class="text-base font-semibold text-highlighted">Installer automatiquement</h2>
            <p class="text-sm text-muted">Extraction des ressources, noms, compilateur de modèles, Garry’s Mod : tout ce qui manque, dans l’ordre. Interruptible et reprenable.</p>
          </div>
          <UButton label="Tout installer" icon="i-ri-magic-line" size="lg" :loading="busy === 'auto' || !!running" :disabled="spaceShort || !!running" @click="start('auto')" />
        </div>
        <template v-if="running">
          <UProgress :model-value="running.total ? running.done : null" :max="running.total || undefined" />
          <p class="truncate text-xs text-muted">{{ pct(running) }} % · {{ last(running) }}</p>
        </template>
      </UCard>

      <!-- 1. game + assets -->
      <UCard :ui="{ body: 'space-y-4 p-4 sm:p-5' }">
        <template #header>
          <div class="flex items-center gap-3">
            <UBadge :label="status?.assets.ok ? '✓' : '1'" :color="status?.assets.ok ? 'success' : 'primary'" variant="subtle" class="size-7 justify-center rounded-full" />
            <div class="min-w-0 flex-1">
              <h2 class="text-base font-semibold text-highlighted">Le jeu et ses ressources</h2>
              <p class="text-sm text-muted">omni extrait seulement ce qu’il utilise (modèles, textures, matériaux, sons) dans son propre dossier.</p>
            </div>
          </div>
        </template>

        <template v-if="status?.assets.ok">
          <UAlert color="success" variant="subtle" icon="i-ri-checkbox-circle-line" title="Ressources prêtes" :description="status.assets.path" />
          <UButton v-if="status.game.ok" label="Ré-extraire (mise à jour du jeu)" icon="i-ri-refresh-line" size="sm" color="neutral" variant="outline" :loading="busy === 'extract'" :disabled="!!running" @click="start('extract')" />
        </template>
        <template v-else>
          <UFormField label="Dossier du jeu" description="Celui qui contient Runtime\chunk0.rpkg (Steam : steamapps\common\007 First Light).">
            <div class="flex gap-2">
              <UInput v-model="gamePath" class="min-w-0 flex-1" placeholder="C:\Program Files (x86)\Steam\steamapps\common\007 First Light" @change="savePath('game')" />
              <UButton icon="i-ri-folder-open-line" label="Parcourir" color="neutral" variant="outline" @click="pick('game')" />
              <UButton icon="i-ri-search-eye-line" label="Détecter" color="neutral" variant="outline" :loading="busy === 'detect'" @click="detect()" />
            </div>
          </UFormField>
          <UAlert
            v-if="status?.game.ok"
            color="success"
            variant="subtle"
            icon="i-ri-gamepad-line"
            :title="`Jeu trouvé · ${status.game.packages.length} package(s)`"
            :description="status.game.packages.join(', ')"
          />
          <div v-if="status?.game.ok && estimate" class="grid grid-cols-3 gap-2 text-center">
            <UCard :ui="{ body: 'p-3 sm:p-3' }"><p class="text-xs text-muted">À extraire</p><p class="font-semibold tabular-nums text-highlighted">{{ fmtBytes(estimate.bytes) }}</p></UCard>
            <UCard :ui="{ body: 'p-3 sm:p-3' }"><p class="text-xs text-muted">Fichiers</p><p class="font-semibold tabular-nums text-highlighted">{{ estimate.files.toLocaleString('fr-FR') }}</p></UCard>
            <UCard :ui="{ body: 'p-3 sm:p-3' }"><p class="text-xs text-muted">Espace libre</p><p class="font-semibold tabular-nums" :class="spaceShort ? 'text-error' : 'text-highlighted'">{{ fmtBytes(status.free_bytes) }}</p></UCard>
          </div>
          <UAlert v-if="spaceShort" color="warning" variant="subtle" icon="i-ri-hard-drive-2-line" title="Espace disque insuffisant" description="Libère de la place ou déplace le dossier Omni (Réglages, Dossiers) vers un disque plus grand." />

          <template v-if="running && running.label.startsWith('Extraction')">
            <UProgress :model-value="running.total ? running.done : null" :max="running.total || undefined" />
            <div class="flex items-center justify-between text-xs tabular-nums text-muted">
              <span>{{ running.done.toLocaleString('fr-FR') }} / {{ running.total.toLocaleString('fr-FR') }} ressources · {{ pct(running) }} %</span>
              <UButton label="Annuler" size="xs" color="neutral" variant="ghost" icon="i-ri-stop-circle-line" @click="jobs.cancel(running.id)" />
            </div>
            <p class="truncate text-xs text-muted">{{ last(running) }}</p>
          </template>
          <UButton v-else label="Extraire les ressources" icon="i-ri-download-2-line" color="primary" block :disabled="!status?.game.ok || spaceShort || !!running" :loading="busy === 'extract'" @click="start('extract')" />

        </template>
      </UCard>

      <!-- 2. names -->
      <UCard :ui="{ body: 'space-y-3 p-4 sm:p-5' }" :class="step < 2 && !status?.names.ok && 'opacity-60'">
        <template #header>
          <div class="flex items-center gap-3">
            <UBadge :label="status?.names.ok ? '✓' : '2'" :color="status?.names.ok ? 'success' : 'primary'" variant="subtle" class="size-7 justify-center rounded-full" />
            <div class="min-w-0 flex-1">
              <h2 class="text-base font-semibold text-highlighted">Les noms des ressources</h2>
              <p class="text-sm text-muted">Les chemins lisibles (« props/industrial/crane… ») viennent de la liste communautaire Bond-Hashes (licence MIT, ≈ 9 Mo).</p>
            </div>
          </div>
        </template>
        <UAlert v-if="status?.names.ok" color="success" variant="subtle" icon="i-ri-checkbox-circle-line" title="Noms disponibles" :description="`${fmtBytes(status.names.bytes)} · ${status.names.path}`" />
        <template v-if="running && running.label.startsWith('Liste')">
          <UProgress :model-value="running.total ? running.done : null" :max="running.total || undefined" />
          <p class="truncate text-xs text-muted">{{ last(running) }}</p>
        </template>
        <UButton v-else :label="status?.names.ok ? 'Mettre à jour la liste' : 'Télécharger la liste'" icon="i-ri-download-cloud-2-line" :color="status?.names.ok ? 'neutral' : 'primary'" :variant="status?.names.ok ? 'outline' : 'solid'" size="sm" :loading="busy === 'names'" :disabled="!!running" @click="start('names')" />
      </UCard>

      <!-- 3. gmod -->
      <UCard :ui="{ body: 'space-y-3 p-4 sm:p-5' }">
        <template #header>
          <div class="flex items-center gap-3">
            <UBadge :label="status?.gmod.ok ? '✓' : '3'" :color="status?.gmod.ok ? 'success' : 'warning'" variant="subtle" class="size-7 justify-center rounded-full" />
            <div class="min-w-0 flex-1">
              <h2 class="text-base font-semibold text-highlighted">Garry’s Mod</h2>
              <p class="text-sm text-muted">Pour les animations des playermodels et pour lier l’addon généré. Sans lui, tu peux tout explorer mais pas convertir de modèles.</p>
            </div>
          </div>
        </template>
        <UFormField label="Dossier de Garry’s Mod" :description="status?.gmod.ok ? 'Trouvé.' : 'Il contient garrysmod\\garrysmod_dir.vpk.'">
          <div class="flex gap-2">
            <UInput v-model="gmodPath" class="min-w-0 flex-1" placeholder="C:\Program Files (x86)\Steam\steamapps\common\GarrysMod" @change="savePath('gmod')" />
            <UButton icon="i-ri-folder-open-line" label="Parcourir" color="neutral" variant="outline" @click="pick('gmod')" />
          </div>
        </UFormField>
      </UCard>

      <!-- 4. compiler -->
      <UCard :ui="{ body: 'space-y-3 p-4 sm:p-5' }">
        <template #header>
          <div class="flex items-center gap-3">
            <UBadge :label="status?.studiomdl.ok ? '✓' : '4'" :color="status?.studiomdl.ok ? 'success' : 'warning'" variant="subtle" class="size-7 justify-center rounded-full" />
            <div class="min-w-0 flex-1">
              <h2 class="text-base font-semibold text-highlighted">Compilateur de modèles</h2>
              <p class="text-sm text-muted">StudioMDL-CE (projet communautaire de DeadZoneLuna) transforme les modèles en .mdl. Téléchargé depuis GitHub, vérifié par somme SHA-256 (≈ 14 Mo).</p>
            </div>
          </div>
        </template>
        <UAlert v-if="status?.studiomdl.ok" color="success" variant="subtle" icon="i-ri-checkbox-circle-line" title="Compilateur prêt" :description="status.studiomdl.path" />
        <template v-if="running && running.label.startsWith('Compilateur')">
          <UProgress :model-value="running.total ? running.done : null" :max="running.total || undefined" />
          <p class="truncate text-xs text-muted">{{ last(running) }}</p>
        </template>
        <UButton v-else-if="!status?.studiomdl.ok" label="Télécharger StudioMDL-CE" icon="i-ri-download-cloud-2-line" size="sm" :loading="busy === 'studiomdl'" :disabled="!!running" @click="start('studiomdl')" />
      </UCard>

      <div class="flex flex-wrap items-center justify-between gap-3 px-1">
        <p class="text-xs text-muted">Données d’omni : <span class="font-mono">{{ status?.data_dir }}</span></p>
        <div class="flex gap-2">
          <UButton v-if="status?.ready && !status.can_convert" label="Continuer sans conversion" color="neutral" variant="outline" @click="finish" />
          <UButton label="Terminer" icon="i-ri-arrow-right-line" trailing :disabled="!status?.ready" @click="finish" />
        </div>
      </div>
    </div>
  </div>
</template>
