<script setup lang="ts">
import type { Job } from '~/utils/types'

// Progress of a running job: the current step (own counter when it has one), the count and the time left.
const props = defineProps<{ job: Job }>()

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onBeforeUnmount(() => clearInterval(timer))

const stage = computed(() => props.job.stage ?? null)
const own = computed(() => !!stage.value && stage.value.total > 0)
const done = computed(() => (own.value ? stage.value!.done : props.job.done))
const total = computed(() => (own.value ? stage.value!.total : props.job.total))
const left = computed(() => {
  const j = props.job
  if (j.eta == null) return null
  return Math.max(0, j.eta - (now.value / 1000 - (j.eta_at ?? now.value / 1000)))
})
const n = (v: number) => v.toLocaleString('fr-FR')
const head = computed(() => {
  const s = stage.value
  if (!s) return ''
  return s.of > 1 ? `Étape ${s.index}/${s.of} · ${s.label}` : s.label
})
</script>

<template>
  <div class="space-y-1.5">
    <p v-if="head" class="truncate text-xs font-medium text-default">{{ head }}</p>
    <UProgress size="sm" :model-value="total ? done : null" :max="total || undefined" aria-label="Progression du travail" />
    <div class="flex items-center justify-between gap-2 text-xs tabular-nums text-muted">
      <span>{{ total ? `${n(done)} / ${n(total)}` : 'en cours…' }}</span>
      <span>{{ fmtEta(left) }}</span>
    </div>
  </div>
</template>
