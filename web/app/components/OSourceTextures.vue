<script setup lang="ts">
interface SrcTexture { role: string; slot: string; key: string; width: number; height: number; format: string; found: boolean }
interface SrcMaterial { name: string; source_name: string; flags: string[]; textures: SrcTexture[] }

/** The game's own textures of a prop (colour, normal, SRM...) before conversion, with a full-size look. */
const props = defineProps<{ propKey: string }>()
const open = defineModel<boolean>('open', { default: false })
const sid = useSourceId()

const materials = ref<SrcMaterial[]>([])
const loading = ref(false)
const failed = ref('')
const big = ref<SrcTexture | null>(null)
const channel = ref<'rgb' | 'rgba' | 'r' | 'g' | 'b' | 'a'>('rgb')
const CHANNELS = ['rgb', 'rgba', 'r', 'g', 'b', 'a'] as const

const ROLE: Record<string, string> = {
  base: 'Couleur', normal: 'Normale', srm: 'SRM (rugosité / métal / AO)', alpha: 'Alpha', emissive: 'Émission',
  ao: 'Occlusion', detail_normal: 'Normale de détail', other: 'Autre',
}

watch([open, () => props.propKey], async () => {
  if (!open.value || !props.propKey) return
  loading.value = true
  failed.value = ''
  materials.value = []
  try {
    materials.value = await api<SrcMaterial[]>(`/${sid.value}/props/${props.propKey}/textures`)
  } catch (e) {
    failed.value = apiError(e)
  } finally {
    loading.value = false
  }
})

const url = (t: SrcTexture, size: number, ch = 'rgb') =>
  `/api/${sid.value}/props/${props.propKey}/texture/${t.key}?size=${size}&channel=${ch}&normal=${t.role === 'normal' ? 1 : 0}`
const bigOpen = computed({ get: () => !!big.value, set: (v: boolean) => !v && (big.value = null) })
</script>

<template>
  <USlideover v-model:open="open" side="right" title="Textures d’origine" description="Ce que le jeu utilise avant conversion." :ui="{ content: 'max-w-md' }">
    <template #body>
      <USkeleton v-if="loading" class="h-40 w-full" />
      <UAlert v-else-if="failed" color="error" variant="subtle" icon="i-ri-error-warning-line" :description="failed" />
      <UEmpty v-else-if="!materials.length" icon="i-ri-image-line" title="Aucune texture" />
      <div v-else class="space-y-3">
        <UCard v-for="m in materials" :key="m.name" :ui="{ body: 'space-y-3 p-3 sm:p-3' }">
          <div>
            <p class="truncate text-sm font-semibold text-highlighted">{{ m.name }}</p>
            <p class="break-all font-mono text-[11px] text-muted">{{ m.source_name }}</p>
            <div v-if="m.flags.length" class="mt-1 flex flex-wrap gap-1">
              <UBadge v-for="f in m.flags" :key="f" :label="f" size="sm" color="neutral" variant="subtle" />
            </div>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="t in m.textures"
              :key="t.slot"
              type="button"
              class="group overflow-hidden rounded-lg border border-default bg-elevated text-left transition hover:border-accented disabled:opacity-50"
              :disabled="!t.found"
              @click="big = t"
            >
              <div class="aspect-video overflow-hidden bg-[conic-gradient(#8882_25%,transparent_0_50%,#8882_0_75%,transparent_0)] bg-[length:12px_12px]">
                <img v-if="t.found" :src="url(t, 256, 'rgba')" alt="" loading="lazy" class="size-full object-contain transition group-hover:scale-105" />
              </div>
              <div class="px-2 py-1.5">
                <p class="truncate text-xs font-medium text-toned">{{ ROLE[t.role] ?? t.role }}</p>
                <p class="truncate text-[11px] tabular-nums text-muted">{{ t.found ? `${t.width} × ${t.height} · ${t.format}` : 'introuvable' }} · {{ t.slot }}</p>
              </div>
            </button>
          </div>
        </UCard>
      </div>
    </template>
  </USlideover>

  <UModal v-model:open="bigOpen" :title="big ? (ROLE[big.role] ?? big.role) : ''" :description="big ? `${big.slot} · ${big.width} × ${big.height} · ${big.format}` : ''" :ui="{ content: 'sm:max-w-4xl' }">
    <template #body>
      <div v-if="big" class="space-y-3">
        <UFieldGroup>
          <UButton v-for="c in CHANNELS" :key="c" :label="c === 'g' ? 'V' : c.toUpperCase()" size="xs" color="neutral" :variant="channel === c ? 'solid' : 'outline'" @click="channel = c" />
        </UFieldGroup>
        <div class="overflow-auto rounded-lg bg-[conic-gradient(#8882_25%,transparent_0_50%,#8882_0_75%,transparent_0)] bg-[length:16px_16px]">
          <img :key="channel + big.key" :src="url(big, 2048, channel)" alt="" class="mx-auto max-h-[65dvh] object-contain" />
        </div>
      </div>
    </template>
  </UModal>
</template>
