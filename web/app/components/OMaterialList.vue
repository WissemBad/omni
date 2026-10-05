<script setup lang="ts">
import type { OutputMaterial } from '~/utils/types'

/** Materials of a converted model: shader, flags, textures (click for a full-size look) and the VMT itself. */
const props = defineProps<{
  materials: OutputMaterial[]
  used: number[]
  skin: number
  skins: number
}>()
const emit = defineEmits<{ texture: [material: number, texture: number] }>()
const sid = useSourceId()

const q = ref('')
const onlySkin = ref(false)
const openItems = ref<string[]>([])

const rows = computed(() =>
  props.materials
    .map((m, i) => ({ m, i, inSkin: props.used.includes(i) }))
    .filter(
      (r) =>
        (!onlySkin.value || r.inSkin) &&
        (!q.value || r.m.name.toLowerCase().includes(q.value.toLowerCase())),
    ),
)
const items = computed(() =>
  rows.value.map((r) => ({ label: r.m.name, value: String(r.i), row: r })),
)

const thumb = (m: OutputMaterial) => {
  const t = m.textures.find((x) => x.param === '$basetexture' && x.exists)
  return t ? outputUrl(sid.value, 'texture', { path: t.path, size: 64 }) : ''
}

type Row = { m: OutputMaterial; i: number; inSkin: boolean }
const R = (item: unknown) => (item as { row: Row }).row

const ALPHA = { OPAQUE: '', MASK: 'alpha test', BLEND: 'translucide' } as const
const skipParams = new Set(['$basetexture', '$bumpmap'])
const shown = (m: OutputMaterial) => Object.entries(m.params).filter(([k]) => !skipParams.has(k))
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center gap-2">
      <UInput v-model="q" icon="i-ri-search-line" size="sm" class="min-w-0 flex-1" :placeholder="`Chercher parmi ${materials.length} matériau${materials.length > 1 ? 'x' : ''}…`">
        <template #trailing>
          <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
        </template>
      </UInput>
    </div>
    <USwitch v-if="skins > 1" v-model="onlySkin" size="sm" :label="`Seulement ceux du skin ${skin}`" />

    <UEmpty v-if="!rows.length" icon="i-ri-palette-line" title="Aucun matériau" description="Change de recherche." />
    <UAccordion v-else v-model="openItems" type="multiple" :items="items" :unmount-on-hide="true" :ui="{ trigger: 'py-2' }">
      <template #leading="{ item }">
        <img
          v-if="thumb(R(item).m)"
          :src="thumb(R(item).m)"
          alt=""
          loading="lazy"
          class="size-9 shrink-0 rounded-md border border-default object-cover"
        />
        <div v-else class="flex size-9 shrink-0 items-center justify-center rounded-md border border-default bg-elevated">
          <UIcon name="i-ri-image-line" class="size-4 text-muted" />
        </div>
      </template>
      <template #default="{ item }">
        <span class="min-w-0 flex-1 text-left" :class="!R(item).inSkin && skins > 1 && 'opacity-50'">
          <span class="block truncate text-sm font-medium text-highlighted">{{ R(item).m.name }}</span>
          <span class="mt-0.5 flex flex-wrap items-center gap-1">
            <UBadge v-if="!R(item).m.vmt" label="VMT introuvable" color="error" variant="subtle" size="sm" />
            <UBadge v-else :label="R(item).m.shader" color="neutral" variant="outline" size="sm" />
            <UBadge v-for="f in R(item).m.flags.filter((x: string) => x !== 'proxies').slice(0, 3)" :key="f" :label="f" color="neutral" variant="subtle" size="sm" />
          </span>
        </span>
      </template>
      <template #body="{ item }">
        <div class="space-y-3 pb-1">
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="(t, ti) in R(item).m.textures"
              :key="t.param"
              type="button"
              class="group overflow-hidden rounded-lg border border-default bg-elevated text-left transition hover:border-accented disabled:opacity-60"
              :disabled="!t.exists"
              @click="emit('texture', R(item).i, ti)"
            >
              <div class="relative aspect-video overflow-hidden bg-[conic-gradient(#8882_25%,transparent_0_50%,#8882_0_75%,transparent_0)] bg-[length:12px_12px]">
                <img
                  v-if="t.exists"
                  :src="outputUrl(sid, 'texture', { path: t.path, size: 256, channel: 'rgba' })"
                  alt=""
                  loading="lazy"
                  class="size-full object-contain transition group-hover:scale-105"
                />
                <div v-else class="flex size-full items-center justify-center text-xs text-muted">
                  {{ t.stock ? 'texture du jeu' : 'manquante' }}
                </div>
              </div>
              <div class="px-2 py-1.5">
                <p class="truncate text-xs font-medium text-toned">{{ t.label }}</p>
                <p class="truncate text-[11px] tabular-nums text-muted">
                  <template v-if="t.exists">{{ t.width }} × {{ t.height }} · {{ t.format }}</template>
                  <template v-else>{{ leaf(t.name) }}</template>
                </p>
              </div>
            </button>
          </div>

          <div v-if="R(item).m.origin" class="space-y-1.5 rounded-lg border border-default p-2">
            <p class="flex items-center gap-1.5 text-xs text-muted">
              <UIcon name="i-ri-gamepad-line" class="size-3.5" />
              Matériau du jeu : <span class="truncate font-medium text-toned" :title="R(item).m.origin!.source">{{ R(item).m.origin!.name }}</span>
            </p>
            <div class="flex flex-wrap gap-1.5">
              <UTooltip v-for="t in R(item).m.origin!.textures" :key="t.key + t.slot" :text="`${texRoleLabel(t.role)} · ${t.name} · ${dimsLabel(t.width, t.height)} ${t.fmt}`">
                <NuxtLink :to="{ path: `/${sid}/textures`, query: { k: t.key } }" class="block size-11 overflow-hidden rounded-md border border-default bg-elevated transition hover:border-primary">
                  <img :src="gameTextureUrl(sid, t.key, { size: 64, role: t.role })" alt="" loading="lazy" class="size-full object-cover" />
                </NuxtLink>
              </UTooltip>
            </div>
          </div>

          <dl v-if="shown(R(item).m).length" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-lg bg-elevated p-2 font-mono text-[11px]">
            <template v-for="[k, v] in shown(R(item).m)" :key="k">
              <dt class="text-muted">{{ k }}</dt>
              <dd class="truncate text-right text-toned" :title="v">{{ v }}</dd>
            </template>
          </dl>

          <UCollapsible v-if="R(item).m.raw">
            <UButton label="Voir le .vmt" icon="i-ri-file-list-3-line" size="xs" color="neutral" variant="ghost" trailing-icon="i-ri-arrow-down-s-line" />
            <template #content>
              <pre class="mt-2 max-h-56 overflow-auto rounded-lg bg-elevated p-2 font-mono text-[11px] leading-relaxed text-toned">{{ R(item).m.raw }}</pre>
              <UButton class="mt-1" icon="i-ri-file-copy-line" label="Copier le chemin" size="xs" color="neutral" variant="ghost" @click="copy(R(item).m.vmt ?? '', 'Chemin copié')" />
            </template>
          </UCollapsible>
          <p v-if="R(item).m.alpha !== 'OPAQUE'" class="text-xs text-muted">Transparence : {{ ALPHA[R(item).m.alpha as keyof typeof ALPHA] }}</p>
        </div>
      </template>
    </UAccordion>
  </div>
</template>
