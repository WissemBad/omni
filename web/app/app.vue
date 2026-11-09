<script setup lang="ts">
const { sources, load } = useSources()
const sid = useSourceId()
const { refresh } = useSourceDetails()
const { running, open, refresh: refreshJobs } = useJobs()
const update = useUpdate()
const tour = useState('tour', () => false)
onMounted(async () => {
  update.check()
  // opened on a file (`omni view <path>`): the viewer first, the tour can wait for the next launch
  if (isViewPath(route.path)) return
  tour.value = await api<{ needed: boolean }>('/onboarding')
    .then((r) => r.needed)
    .catch(() => false)
})

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

watch(sid, (v) => v !== VIEW_ID && refresh(), { immediate: true })

// Pages outside a game (settings, library) keep the tabs of the last game opened.
const lastSid = useState('last-source', () => '')
watch(sid, (v) => v && v !== VIEW_ID && (lastSid.value = v), { immediate: true })
const current = computed(() =>
  sources.value.find(
    (s) => s.id === (sid.value === VIEW_ID ? lastSid.value : sid.value || lastSid.value),
  ),
)

// What the current game offers: browse it (models, textures, sounds), look at what omni made of it, take it away.
const route = useRoute()
const items = computed(() => {
  const c = current.value
  if (route.path === '/setup')
    return [{ label: 'Installation', icon: 'i-ri-install-line', to: '/setup' }]
  if (!c) return []
  const caps = c.capabilities
  const models = caps.includes('props') || caps.includes('characters')
  const rows = []
  if (models) rows.push({ label: 'Modèles', icon: 'i-ri-box-3-line', to: `/${c.id}/models` })
  if (caps.includes('textures'))
    rows.push({ label: 'Textures', icon: 'i-ri-image-2-line', to: `/${c.id}/textures` })
  if (caps.includes('sounds'))
    rows.push({ label: 'Sons', icon: 'i-ri-music-2-line', to: `/${c.id}/sounds` })
  if (models) rows.push({ label: 'Visionneuse', icon: 'i-ri-eye-line', to: `/${c.id}/viewer` })
  rows.push({ label: 'Exports', icon: 'i-ri-download-2-line', to: `/${c.id}/exports` })
  return rows
})
const showGames = computed(() => sources.value.length > 0 && route.path !== '/setup')
</script>

<template>
  <UApp>
    <div v-if="showGames" class="pointer-events-none fixed left-4 top-4 z-50 max-xl:hidden">
      <div class="wi-glass wi-glass--pill pointer-events-auto p-1">
        <OSourceMenu />
      </div>
    </div>
    <WNavbar :items="items" label="Navigation principale">
      <template #trailing>
        <OSourceMenu v-if="showGames" class="xl:hidden" />
        <UTooltip v-if="update.info.value?.available" :text="`omni ${update.info.value.latest} est disponible`">
          <UButton label="Mise à jour" icon="i-ri-download-cloud-2-line" size="sm" color="primary" variant="soft" @click="update.install()" />
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
        <OAppMenu />
      </template>
    </WNavbar>

    <main class="h-dvh">
      <div v-if="apiDown" class="flex h-full items-center justify-center p-6">
        <UEmpty
          icon="i-ri-plug-line"
          title="API omni injoignable"
          :description="apiDown"
          :actions="[{ label: 'Réessayer', icon: 'i-ri-refresh-line', onClick: () => reloadNuxtApp() }]"
        />
      </div>
      <NuxtPage v-else />
    </main>
    <OJobsPanel />
    <OConfirm />
    <OOnboarding v-model:open="tour" />
  </UApp>
</template>
