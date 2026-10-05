<script setup lang="ts">
import type { Channel } from '~/utils/textures'

/** One texture the viewer can show: a game texture (before conversion) or a converted VTF. */
export interface TexEntry {
  id: string
  /** What the texture is for ("Couleur", "Normale"...). */
  label: string
  /** File / texture name. */
  name: string
  material?: string
  width?: number
  height?: number
  /** URL of the image for a channel and a size. */
  src: (channel: Channel, size: number) => string
  /** Facts shown in the side panel. */
  info: [string, string | number][]
  flags?: string[]
  /** UV layout of the model drawn over the texture, when it is known. */
  uv?: string | null
  /** Game texture key: link to the Textures workbench. */
  gameKey?: string
  /** Converted file path (copy / show in Explorer). */
  path?: string
  /** Full-resolution download URL. */
  download?: string
}

/**
 * Full-size look at a texture: zoom and pan, colour channels, size, background and the model's UV layout over it.
 * ← → walk through the textures of the same model.
 */
const props = defineProps<{ entries: TexEntry[] }>()
const open = defineModel<boolean>('open', { default: false })
const index = defineModel<number>('index', { default: 0 })
const sid = useSourceId()

const cur = computed(() => props.entries[index.value])
const channel = ref<Channel>('rgb')
const quality = ref(2048)
const bg = ref<'checker' | 'dark' | 'light'>('checker')
const uv = ref(false)
const loading = ref(true)
const failed = ref(false)

const zoom = ref(1)
const pan = reactive({ x: 0, y: 0 })
const stage = useTemplateRef<HTMLElement>('stage')

const CHANNELS = [
  { label: 'Couleur', value: 'rgb', tip: 'Couleur sans transparence' },
  { label: 'Alpha+', value: 'rgba', tip: 'Couleur avec transparence' },
  { label: 'R', value: 'r', tip: 'Canal rouge' },
  { label: 'V', value: 'g', tip: 'Canal vert' },
  { label: 'B', value: 'b', tip: 'Canal bleu' },
  { label: 'A', value: 'a', tip: 'Canal alpha' },
] as const
const QUALITIES = [
  { label: 'Aperçu (1024)', value: 1024 },
  { label: 'Détaillé (2048)', value: 2048 },
  { label: 'Pleine résolution', value: 8192 },
]
const BACKGROUNDS = [
  { label: 'Damier', value: 'checker' },
  { label: 'Sombre', value: 'dark' },
  { label: 'Clair', value: 'light' },
]

const src = computed(() => (cur.value ? cur.value.src(channel.value, quality.value) : ''))
const uvSrc = computed(() => (cur.value && uv.value && cur.value.uv ? cur.value.uv : ''))
const dims = computed(() => ({ w: cur.value?.width || 1024, h: cur.value?.height || 1024 }))
const stageStyle = computed(() => {
  if (bg.value === 'dark') return { background: '#0b0b10' }
  if (bg.value === 'light') return { background: '#f4f4f7' }
  return {
    backgroundColor: '#2a2a33',
    backgroundImage: 'conic-gradient(#3a3a46 25%, transparent 0 50%, #3a3a46 0 75%, transparent 0)',
    backgroundSize: '20px 20px',
  }
})

function fit() {
  const el = stage.value
  if (!el) return
  zoom.value = Math.min(el.clientWidth / dims.value.w, el.clientHeight / dims.value.h) * 0.94
  pan.x = pan.y = 0
}

function actual() {
  zoom.value = 1
  pan.x = pan.y = 0
}

watch([index, open], async () => {
  failed.value = false
  loading.value = true
  await nextTick()
  fit()
})
watch([channel, quality], () => (loading.value = true))

function wheel(e: WheelEvent) {
  const el = stage.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const px = e.clientX - r.left - r.width / 2
  const py = e.clientY - r.top - r.height / 2
  const next = Math.min(32, Math.max(0.05, zoom.value * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))
  const k = next / zoom.value
  pan.x = px - (px - pan.x) * k
  pan.y = py - (py - pan.y) * k
  zoom.value = next
}

let drag: { x: number; y: number; px: number; py: number } | null = null
function down(e: PointerEvent) {
  drag = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}
function move(e: PointerEvent) {
  if (!drag) return
  pan.x = drag.px + e.clientX - drag.x
  pan.y = drag.py + e.clientY - drag.y
}
const up = () => (drag = null)

function step(d: number) {
  const n = props.entries.length
  if (n) index.value = (index.value + d + n) % n
}

defineShortcuts({
  arrowleft: { usingInput: false, handler: () => open.value && step(-1) },
  arrowright: { usingInput: false, handler: () => open.value && step(1) },
  f: () => open.value && fit(),
  '1': () => open.value && actual(),
  u: () => open.value && !!cur.value?.uv && (uv.value = !uv.value),
})

function goTexture() {
  const key = cur.value?.gameKey
  if (!key) return
  open.value = false
  // after the modal has closed (its focus trap and scroll lock are released)
  setTimeout(() => navigateTo({ path: `/${sid.value}/textures`, query: { k: key } }), 180)
}
</script>

<template>
  <UModal
    v-model:open="open"
    :title="cur ? `${cur.label} · ${leaf(cur.name)}` : 'Texture'"
    :description="cur ? cur.name : ''"
    :ui="{ content: 'h-[90dvh] w-[calc(100vw-1.5rem)] sm:max-w-6xl', body: 'flex min-h-0 flex-1 flex-col p-0 sm:p-0 md:flex-row' }"
  >
    <template #body>
      <div
        ref="stage"
        class="relative min-h-0 flex-1 cursor-grab touch-none overflow-hidden active:cursor-grabbing"
        :style="stageStyle"
        @wheel.prevent="wheel"
        @pointerdown="down"
        @pointermove="move"
        @pointerup="up"
        @pointercancel="up"
        @dblclick="zoom === 1 ? fit() : actual()"
      >
        <div
          v-if="cur"
          class="absolute left-1/2 top-1/2 origin-center"
          :style="{ width: `${dims.w}px`, height: `${dims.h}px`, transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }"
        >
          <img
            :key="src"
            :src="src"
            alt=""
            draggable="false"
            class="size-full select-none object-fill"
            :class="zoom > 3 && '[image-rendering:pixelated]'"
            @load="loading = false"
            @error="failed = true; loading = false"
          />
          <img v-if="uvSrc" :src="uvSrc" alt="" draggable="false" class="pointer-events-none absolute inset-0 size-full select-none" />
        </div>

        <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-default/40">
          <UIcon name="i-ri-loader-4-line" class="size-7 animate-spin text-primary" />
        </div>
        <UEmpty v-if="failed" icon="i-ri-image-line" title="Texture illisible" description="Le format de ce fichier n’est pas pris en charge par l’aperçu." class="absolute inset-0 bg-default" />

        <div class="pointer-events-none absolute bottom-3 left-3">
          <WGlassCard :halo="false" body-class="px-2.5 py-1 sm:px-2.5 sm:py-1">
            <span class="text-xs tabular-nums text-toned">{{ Math.round(zoom * 100) }} %</span>
          </WGlassCard>
        </div>
        <div class="absolute bottom-3 right-3" @pointerdown.stop @dblclick.stop>
          <WGlassCard :halo="false" body-class="flex items-center gap-1 p-1 sm:p-1">
            <UButton icon="i-ri-focus-3-line" label="Ajuster" size="xs" color="neutral" variant="ghost" @click="fit" />
            <UButton label="100 %" size="xs" color="neutral" variant="ghost" @click="actual" />
          </WGlassCard>
        </div>
      </div>

      <aside class="flex max-h-[45%] shrink-0 flex-col gap-4 overflow-y-auto border-t border-default p-4 md:max-h-none md:w-72 md:border-l md:border-t-0">
        <section class="space-y-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Canal</h3>
          <UFieldGroup class="flex w-full">
            <UTooltip v-for="c in CHANNELS" :key="c.value" :text="c.tip">
              <UButton :label="c.label" size="xs" color="neutral" :variant="channel === c.value ? 'solid' : 'outline'" class="flex-1 justify-center" @click="channel = c.value" />
            </UTooltip>
          </UFieldGroup>
        </section>

        <section class="space-y-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Affichage</h3>
          <USelect v-model="quality" :items="QUALITIES" size="sm" class="w-full" />
          <USelect v-model="bg" :items="BACKGROUNDS" size="sm" class="w-full" icon="i-ri-contrast-2-line" />
          <USwitch
            v-model="uv"
            size="sm"
            label="Superposer les UV"
            :description="cur?.uv ? 'Le dépliage du modèle sur la texture.' : 'Pas de dépliage connu pour cette texture.'"
            :disabled="!cur?.uv"
          />
        </section>

        <section class="space-y-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Fichier</h3>
          <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <template v-for="[k, v] in cur?.info ?? []" :key="k">
              <dt class="text-muted">{{ k }}</dt>
              <dd class="truncate text-right tabular-nums text-toned" :title="String(v)">{{ v }}</dd>
            </template>
          </dl>
          <div v-if="cur?.flags?.length" class="flex flex-wrap gap-1">
            <UBadge v-for="f in cur.flags" :key="f" :label="f" size="sm" color="neutral" variant="subtle" />
          </div>
          <div class="flex flex-wrap gap-2 pt-1">
            <UButton v-if="cur?.gameKey" icon="i-ri-image-2-line" label="Dans Textures" size="xs" color="primary" variant="soft" @click="goTexture" />
            <UButton v-if="cur?.download" icon="i-ri-download-2-line" label="PNG" size="xs" color="neutral" variant="outline" :to="cur.download" external target="_blank" />
            <UButton v-if="cur?.path" icon="i-ri-file-copy-line" label="Copier" size="xs" color="neutral" variant="outline" @click="cur && copy(cur.path!, 'Chemin copié')" />
            <UButton v-if="cur?.path" icon="i-ri-folder-open-line" label="Afficher" size="xs" color="neutral" variant="outline" @click="cur && reveal(sid, cur.path!)" />
          </div>
        </section>

        <section v-if="entries.length > 1" class="space-y-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted">Textures du modèle ({{ index + 1 }} / {{ entries.length }})</h3>
          <div class="grid grid-cols-4 gap-1.5">
            <button
              v-for="(e, i) in entries"
              :key="e.id"
              type="button"
              class="aspect-square overflow-hidden rounded-md border bg-elevated transition"
              :class="i === index ? 'border-primary ring-2 ring-primary/40' : 'border-default hover:border-accented'"
              :title="`${e.label} · ${leaf(e.name)}`"
              @click="index = i"
            >
              <img :src="e.src('rgb', 64)" alt="" loading="lazy" class="size-full object-cover" />
            </button>
          </div>
        </section>
      </aside>
    </template>
  </UModal>
</template>
