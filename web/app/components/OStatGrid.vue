<script setup lang="ts">
/** Key figures in small cards (inspectors, home page). */
defineProps<{ stats: { label: string; value: string; icon: string; hint?: string }[]; cols?: number }>()
</script>

<template>
  <div class="grid gap-2" :class="cols === 3 ? 'grid-cols-3' : cols === 4 ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2'">
    <UCard v-for="s in stats" :key="s.label" :ui="{ body: 'p-2.5 sm:p-2.5' }">
      <p class="flex items-center gap-1.5 text-xs text-muted"><UIcon :name="s.icon" class="size-3.5 shrink-0" /><span class="truncate">{{ s.label }}</span></p>
      <p class="mt-0.5 truncate text-sm font-semibold tabular-nums text-highlighted" :title="s.hint ?? s.value">{{ s.value }}</p>
    </UCard>
  </div>
</template>
