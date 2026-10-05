<script setup lang="ts">
/** Game selector: every source the API exposes, keeping the current workbench when the new game has it. */
const { sources } = useSources()
const sid = useSourceId()
const route = useRoute()

const current = computed(() => sources.value.find((s) => s.id === sid.value))
const section = computed(() => String(route.path.split('/')[2] ?? ''))
const NEEDS: Record<string, string[]> = {
  models: ['props', 'characters'],
  viewer: ['props', 'characters'],
  textures: ['textures'],
  sounds: ['sounds'],
  settings: [],
}

/** Same workbench in the other game when it has it, else its home page. */
function go(id: string) {
  const target = sources.value.find((s) => s.id === id)
  if (!target) return
  const need = NEEDS[section.value]
  const keep = need && (!need.length || need.some((c) => target.capabilities.includes(c as never)))
  navigateTo(keep ? `/${id}/${section.value}` : `/${id}`)
}

const menu = computed(() => [
  [{ type: 'label' as const, label: 'Source' }],
  sources.value.map((s) => ({
    label: s.title,
    icon: 'i-ri-gamepad-line',
    type: 'checkbox' as const,
    checked: s.id === sid.value,
    onUpdateChecked: () => go(s.id),
    onSelect: (e: Event) => e.preventDefault(),
  })),
])
</script>

<template>
  <UDropdownMenu :items="menu" :content="{ align: 'end', sideOffset: 10 }">
    <UButton
      icon="i-ri-gamepad-line"
      color="neutral"
      variant="ghost"
      trailing-icon="i-ri-arrow-down-s-line"
      class="h-11 rounded-full sm:h-9"
      :aria-label="`Source : ${current?.title ?? ''}`"
    >
      <span class="max-w-32 truncate max-md:sr-only">{{ current?.title ?? 'Source' }}</span>
    </UButton>
  </UDropdownMenu>
</template>
