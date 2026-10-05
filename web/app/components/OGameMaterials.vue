<script setup lang="ts">
import type { SourceMaterial } from '~/utils/types'

/**
 * Materials of a game model before conversion: shader class, flags, the raw textures the game uses (click: full
 * view) and the material's parameters. Shared by the props and characters inspectors.
 */
const props = defineProps<{ materials: SourceMaterial[]; hint?: string }>()
const emit = defineEmits<{ texture: [material: number, texture: number] }>()
const sid = useSourceId()

const q = ref('')
const openItems = ref<string[]>(props.materials.length === 1 ? ['0'] : [])
const rows = computed(() =>
  props.materials
    .map((m, i) => ({ m, i }))
    .filter(
      (r) =>
        !q.value || `${r.m.name} ${r.m.source_name}`.toLowerCase().includes(q.value.toLowerCase()),
    ),
)
const items = computed(() =>
  rows.value.map((r) => ({ label: r.m.name, value: String(r.i), row: r })),
)
type Row = { m: SourceMaterial; i: number }
const R = (item: unknown) => (item as { row: Row }).row
const thumb = (m: SourceMaterial) => {
  const t =
    m.textures.find((x) => x.role === 'base' && x.found !== false) ??
    m.textures.find((x) => x.found !== false)
  return t ? gameTextureUrl(sid.value, t.key, { size: 64, role: t.role }) : ''
}
const params = (m: SourceMaterial) => Object.entries(m.params).slice(0, 40)
const fmtParam = (v: number[]) => v.map((x) => (Number.isInteger(x) ? x : x.toFixed(3))).join(' ')
</script>

<template>
  <div class="space-y-3">
    <UInput v-if="materials.length > 4" v-model="q" icon="i-ri-search-line" size="sm" class="w-full" :placeholder="`Chercher parmi ${materials.length} matériaux…`" />
    <p v-if="hint" class="text-xs text-muted">{{ hint }}</p>
    <UEmpty v-if="!rows.length" icon="i-ri-palette-line" title="Aucun matériau" />
    <UAccordion v-else v-model="openItems" type="multiple" :items="items" :unmount-on-hide="true" :ui="{ trigger: 'py-2' }">
      <template #leading="{ item }">
        <img v-if="thumb(R(item).m)" :src="thumb(R(item).m)" alt="" loading="lazy" class="size-9 shrink-0 rounded-md border border-default object-cover" />
        <div v-else class="flex size-9 shrink-0 items-center justify-center rounded-md border border-default bg-elevated">
          <UIcon name="i-ri-image-line" class="size-4 text-muted" />
        </div>
      </template>
      <template #default="{ item }">
        <span class="min-w-0 flex-1 text-left">
          <span class="block truncate text-sm font-medium text-highlighted">{{ R(item).m.name }}</span>
          <span class="mt-0.5 flex flex-wrap items-center gap-1">
            <UBadge v-if="R(item).m.class" :label="R(item).m.class" color="neutral" variant="outline" size="sm" />
            <UBadge :label="`${R(item).m.textures.length} texture${R(item).m.textures.length > 1 ? 's' : ''}`" color="neutral" variant="subtle" size="sm" />
            <UBadge v-for="f in (R(item).m.flags ?? []).slice(0, 2)" :key="f" :label="f" color="neutral" variant="subtle" size="sm" />
          </span>
        </span>
      </template>
      <template #body="{ item }">
        <div class="space-y-3 pb-1">
          <p v-if="R(item).m.source_name" class="break-all font-mono text-[11px] text-muted">{{ R(item).m.source_name }}</p>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="(t, ti) in R(item).m.textures"
              :key="t.slot + t.key"
              type="button"
              class="group overflow-hidden rounded-lg border border-default bg-elevated text-left transition hover:border-accented disabled:opacity-60"
              :disabled="t.found === false"
              @click="emit('texture', R(item).i, ti)"
            >
              <div class="relative aspect-video overflow-hidden bg-[conic-gradient(#8882_25%,transparent_0_50%,#8882_0_75%,transparent_0)] bg-[length:12px_12px]">
                <img
                  v-if="t.found !== false"
                  :src="gameTextureUrl(sid, t.key, { size: 256, channel: 'rgba', role: t.role })"
                  alt=""
                  loading="lazy"
                  class="size-full object-contain transition group-hover:scale-105"
                />
                <div v-else class="flex size-full items-center justify-center text-xs text-muted">introuvable</div>
              </div>
              <div class="px-2 py-1.5">
                <p class="truncate text-xs font-medium text-toned">{{ texRoleLabel(t.role) }}</p>
                <p class="truncate text-[11px] tabular-nums text-muted">{{ t.found !== false ? `${dimsLabel(t.width, t.height)} · ${t.format ?? ''}` : t.slot }}</p>
              </div>
            </button>
          </div>
          <p v-if="R(item).m.unknown_slots?.length" class="text-xs text-warning">Emplacements non reconnus : {{ R(item).m.unknown_slots!.join(', ') }}</p>
          <UCollapsible v-if="params(R(item).m).length">
            <UButton :label="`Paramètres du matériau (${Object.keys(R(item).m.params).length})`" icon="i-ri-equalizer-line" size="xs" color="neutral" variant="ghost" trailing-icon="i-ri-arrow-down-s-line" />
            <template #content>
              <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-lg bg-elevated p-2 font-mono text-[11px]">
                <template v-for="[k, v] in params(R(item).m)" :key="k">
                  <dt class="truncate text-muted" :title="k">{{ k }}</dt>
                  <dd class="truncate text-right text-toned">{{ fmtParam(v) }}</dd>
                </template>
              </dl>
            </template>
          </UCollapsible>
        </div>
      </template>
    </UAccordion>
  </div>
</template>
