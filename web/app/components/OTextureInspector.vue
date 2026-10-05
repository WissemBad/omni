<script setup lang="ts">
import type { TextureDetail } from '~/utils/types'

/** A game texture: facts, what the conversion does with it, the materials and models using it. */
const props = defineProps<{ detail: TextureDetail }>()
const emit = defineEmits<{ export: []; full: [] }>()
const sid = useSourceId()
const d = computed(() => props.detail)

const stats = computed(() => [
  { label: 'Dimensions', value: dimsLabel(d.value.width, d.value.height), icon: 'i-ri-ruler-line' },
  { label: 'Format du jeu', value: d.value.fmt, icon: 'i-ri-file-zip-line' },
  { label: 'Niveaux de mip', value: String(d.value.mips), icon: 'i-ri-stack-line' },
  { label: 'Poids (jeu)', value: fmtBytes(d.value.bytes), icon: 'i-ri-hard-drive-2-line' },
  { label: 'Matériaux', value: String(d.value.materials.length), icon: 'i-ri-palette-line' },
  { label: 'Modèles', value: String(d.value.model_count), icon: 'i-ri-box-3-line' },
])
const converted = computed(() => d.value.models.filter((m) => m.converted).length)
</script>

<template>
  <div class="space-y-4">
    <OStatGrid :stats="stats" />

    <UAlert color="neutral" variant="subtle" icon="i-ri-swap-line" title="Vers GMod" :description="FORMAT_HINT[d.fmt] ?? 'Convertie en DXT pour Source.'" />

    <div class="flex flex-wrap gap-2">
      <UButton icon="i-ri-fullscreen-line" label="Plein écran" size="sm" color="neutral" variant="outline" @click="emit('full')" />
      <UButton icon="i-ri-download-2-line" label="PNG" size="sm" color="neutral" variant="outline" :to="`/api/${sid}/textures/${d.key}/download`" external target="_blank" />
      <UButton icon="i-ri-folder-download-line" label="Exporter" size="sm" color="neutral" variant="outline" @click="emit('export')" />
      <UButton icon="i-ri-file-copy-line" label="Clé" size="sm" color="neutral" variant="ghost" @click="copy(d.key, 'Clé copiée')" />
    </div>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold text-highlighted">Matériaux <span class="font-normal text-muted">({{ d.materials.length }})</span></h3>
      <p v-if="!d.materials.length" class="text-xs text-muted">Aucun matériau ne référence cette texture (texture d’interface, de terrain ou inutilisée).</p>
      <ul class="space-y-1">
        <li v-for="m in d.materials.slice(0, 60)" :key="m.key + m.slot" class="rounded-md px-2 py-1.5 hover:bg-elevated">
          <p class="truncate text-sm text-toned" :title="m.source">{{ m.name }}</p>
          <p class="truncate text-xs text-muted">{{ m.slot }} · {{ texRoleLabel(m.role) }}<template v-if="m.cls"> · {{ m.cls }}</template></p>
        </li>
      </ul>
    </section>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold text-highlighted">
        Modèles <span class="font-normal text-muted">({{ d.model_count }}<template v-if="d.models.length"> · {{ converted }} converti{{ converted > 1 ? 's' : '' }}</template>)</span>
      </h3>
      <ul class="space-y-0.5">
        <li v-for="m in d.models.slice(0, 80)" :key="m.key">
          <NuxtLink :to="{ path: `/${sid}/models`, query: { tab: 'props', k: m.key } }" class="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-elevated">
            <UIcon name="i-ri-box-3-line" class="size-4 shrink-0 text-muted" />
            <span class="min-w-0 flex-1 truncate text-sm text-toned">{{ titleCase(leaf(m.rel)) }}</span>
            <UIcon v-if="m.converted" name="i-ri-checkbox-circle-fill" class="size-4 shrink-0 text-success" />
          </NuxtLink>
        </li>
      </ul>
      <p v-if="d.model_count > d.models.length" class="text-xs text-muted">… et {{ d.model_count - d.models.length }} autre(s) (personnages, décors non catalogués).</p>
    </section>

    <section v-if="d.game_path" class="space-y-1">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Chemin dans le jeu</h3>
      <p class="break-all rounded-lg bg-elevated p-2 font-mono text-[11px] text-toned">{{ d.game_path }}</p>
    </section>
  </div>
</template>
