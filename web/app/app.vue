<script setup lang="ts">
const { sources, load } = useSources()
const sid = useSourceId()
const { details, refresh, toggleDeploy } = useSourceDetails()
const { running, open, refresh: refreshJobs } = useJobs()

const apiDown = ref('')
async function boot() {
  apiDown.value = ''
  try {
    await load()
    await refreshJobs()
  } catch (e) {
    apiDown.value = apiError(e)
  }
}
await boot()

watch(sid, refresh, { immediate: true })

const current = computed(() => sources.value.find((s) => s.id === sid.value))

// Accueil · Modèles · Textures · Visionneuse · Sons · Réglages (only what the source offers)
const route = useRoute()
const items = computed(() => {
  const c = current.value
  if (route.path === '/setup')
    return [{ label: 'Installation', icon: 'i-ri-install-line', to: '/setup' }]
  if (!c) return []
  const caps = c.capabilities
  const rows = [{ label: 'Accueil', icon: 'i-ri-home-5-line', to: `/${c.id}` }]
  if (caps.includes('props') || caps.includes('characters'))
    rows.push({ label: 'Modèles', icon: 'i-ri-box-3-line', to: `/${c.id}/models` })
  if (caps.includes('textures'))
    rows.push({ label: 'Textures', icon: 'i-ri-image-2-line', to: `/${c.id}/textures` })
  if (caps.includes('props') || caps.includes('characters'))
    rows.push({ label: 'Visionneuse', icon: 'i-ri-eye-line', to: `/${c.id}/viewer` })
  if (caps.includes('sounds'))
    rows.push({ label: 'Sons', icon: 'i-ri-music-2-line', to: `/${c.id}/sounds` })
  rows.push({ label: 'Réglages', icon: 'i-ri-settings-3-line', to: `/${c.id}/settings` })
  return rows
})
</script>

<template>
  <UApp>
    <WNavbar :items="items" label="Navigation principale">
      <template #trailing>
        <OSourceMenu v-if="sources.length && route.path !== '/setup'" />
        <UTooltip :text="details?.deployed ? 'Addon lié à GMod : cliquer pour délier' : 'Lier l’addon à GMod'">
          <UButton
            :icon="details?.deployed ? 'i-ri-link' : 'i-ri-link-unlink'"
            :color="details?.deployed ? 'success' : 'neutral'"
            variant="ghost"
            class="size-11 justify-center rounded-full sm:size-9"
            aria-label="Lier l’addon à GMod"
            :disabled="!current"
            @click="toggleDeploy"
          />
        </UTooltip>
        <UChip :show="running > 0" :text="running" size="3xl" color="primary" inset>
          <UButton
            icon="i-ri-list-check-3"
            color="neutral"
            variant="ghost"
            class="size-11 justify-center rounded-full sm:size-9"
            aria-label="Travaux en cours"
            @click="open = true"
          />
        </UChip>
        <WColorModeButton />
      </template>
    </WNavbar>

    <main class="h-dvh">
      <div v-if="apiDown" class="flex h-full items-center justify-center p-6">
        <UEmpty
          icon="i-ri-plug-line"
          title="API omni injoignable"
          :description="`${apiDown} — lance « uv run python -m omni ui » dans le dossier omni.`"
          :actions="[{ label: 'Réessayer', icon: 'i-ri-refresh-line', onClick: () => reloadNuxtApp() }]"
        />
      </div>
      <NuxtPage v-else />
    </main>
    <OJobsPanel />
  </UApp>
</template>
