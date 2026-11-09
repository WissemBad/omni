<script setup lang="ts">
/**
 * The game everything else refers to: its overview, the other games (keeping the current page when the other game
 * has it) and the library.
 */
const { sources } = useSources()
const sid = useSourceId()
const lastSid = useState('last-source', () => '')
const route = useRoute()

const id = computed(() => sid.value || lastSid.value)
const current = computed(() => sources.value.find((s) => s.id === id.value))
const section = computed(() => String(route.path.split('/')[2] ?? ''))
const NEEDS: Record<string, string[]> = {
  models: ['props', 'characters'],
  viewer: ['props', 'characters'],
  textures: ['textures'],
  sounds: ['sounds'],
  exports: [],
}

/** Same page in the other game when it has it, else its overview. */
function go(target: string) {
  const s = sources.value.find((x) => x.id === target)
  if (!s) return
  const need = NEEDS[section.value]
  const keep =
    sid.value && need && (!need.length || need.some((c) => s.capabilities.includes(c as never)))
  navigateTo(keep ? `/${target}/${section.value}` : `/${target}`)
}

const menu = computed(() => [
  current.value
    ? [{ label: 'Vue d’ensemble', icon: 'i-ri-dashboard-line', to: `/${current.value.id}` }]
    : [],
  [
    { type: 'label' as const, label: 'Jeux' },
    ...sources.value.map((s) => ({
      label: s.title,
      icon: 'i-ri-gamepad-line',
      type: 'checkbox' as const,
      checked: s.id === id.value,
      onUpdateChecked: () => go(s.id),
      onSelect: (e: Event) => e.preventDefault(),
    })),
  ],
  [{ label: 'Gérer les jeux…', icon: 'i-ri-folder-add-line', to: '/games' }],
])
</script>

<template>
  <UDropdownMenu :items="menu" :content="{ align: 'start', sideOffset: 10 }">
    <UButton
      icon="i-ri-gamepad-line"
      color="neutral"
      variant="ghost"
      trailing-icon="i-ri-arrow-down-s-line"
      class="h-11 rounded-full font-semibold sm:h-9"
      :aria-label="`Jeu : ${current?.title ?? 'aucun'}`"
    >
      <span class="max-w-40 truncate max-md:sr-only">{{ current?.title ?? 'Choisir un jeu' }}</span>
    </UButton>
  </UDropdownMenu>
</template>
