<script setup lang="ts">
definePageMeta({ key: (r) => `${r.params.source}/models` })

/** Models: the game's props and characters, one workbench each, the same inspector layout. */
const sid = useSourceId()
const route = useRoute()
const router = useRouter()
const { sources } = useSources()
const caps = computed(() => sources.value.find((s) => s.id === sid.value)?.capabilities ?? [])
const has = computed(() => ({
  props: caps.value.includes('props'),
  characters: caps.value.includes('characters'),
}))

const tab = ref<'props' | 'characters'>(route.query.tab === 'characters' ? 'characters' : 'props')
watch(
  has,
  (h) => {
    if (tab.value === 'props' && !h.props && h.characters) tab.value = 'characters'
    if (tab.value === 'characters' && !h.characters && h.props) tab.value = 'props'
  },
  { immediate: true },
)
// switching kind starts from a clean URL (each bench keeps its own filters there)
watch(tab, (t) => router.replace({ query: { tab: t } }))
</script>

<template>
  <OPropsBench v-if="tab === 'props' && has.props" :key="`${sid}-props`">
    <template #switch><OModelSwitch v-model="tab" :has="has" /></template>
  </OPropsBench>
  <OCharactersBench v-else-if="has.characters" :key="`${sid}-characters`">
    <template #switch><OModelSwitch v-model="tab" :has="has" /></template>
  </OCharactersBench>
  <div v-else class="flex h-full items-center justify-center p-6">
    <UEmpty icon="i-ri-box-3-line" title="Pas de modèles" description="Cette source n’expose ni props ni personnages." />
  </div>
</template>
