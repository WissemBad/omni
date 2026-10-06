<script setup lang="ts">
/** Charge de la machine en direct : CPU, mémoire, GPU NVIDIA (si présent), une minute d'historique. */
interface Sample {
  t: number
  cpu: number
  memory: { total: number; used: number; percent: number }
  gpu: {
    name: string
    load: number | null
    temp: number | null
    mem_used: number | null
    mem_total: number | null
    power: number | null
  } | null
  cpu_temp: number | null
}

const POINTS = 60
const last = ref<Sample | null>(null)
const cpu = ref<number[]>([])
const ram = ref<number[]>([])
const gpuLoad = ref<number[]>([])
const failed = ref(false)

const push = (a: Ref<number[]>, v: number | null | undefined) => {
  a.value = [...a.value, v ?? 0].slice(-POINTS)
}

async function poll() {
  if (document.hidden) return
  try {
    const s = await api<Sample>('/monitor')
    last.value = s
    failed.value = false
    push(cpu, s.cpu)
    push(ram, s.memory.percent)
    push(gpuLoad, s.gpu?.load)
  } catch {
    failed.value = true
  }
}

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  poll()
  timer = setInterval(poll, 2000)
})
onBeforeUnmount(() => clearInterval(timer))

const W = 240
const H = 56
function path(values: number[], max: number, close = false) {
  if (values.length < 2) return ''
  const step = W / (POINTS - 1)
  const x0 = W - (values.length - 1) * step
  const pts = values.map(
    (v, i) =>
      `${(x0 + i * step).toFixed(1)},${(H - Math.min(1, v / max) * (H - 4) - 2).toFixed(1)}`,
  )
  const line = `M${pts.join(' L')}`
  return close ? `${line} L${W},${H} L${x0.toFixed(1)},${H} Z` : line
}

const charts = computed(() => {
  const g = last.value?.gpu
  const m = last.value?.memory
  return [
    {
      key: 'cpu',
      label: 'Processeur',
      icon: 'i-ri-cpu-line',
      values: cpu.value,
      max: 100,
      now: last.value ? `${last.value.cpu.toFixed(0)} %` : '—',
      sub:
        last.value?.cpu_temp != null
          ? `${last.value.cpu_temp} °C`
          : 'Température non exposée par Windows',
      color: 'text-primary',
    },
    {
      key: 'ram',
      label: 'Mémoire',
      icon: 'i-ri-ram-line',
      values: ram.value,
      max: 100,
      now: m ? `${m.percent} %` : '—',
      sub: m ? `${fmtBytes(m.used)} / ${fmtBytes(m.total)}` : '',
      color: 'text-info',
    },
    ...(g
      ? [
          {
            key: 'gpu',
            label: 'GPU',
            icon: 'i-ri-gamepad-line',
            values: gpuLoad.value,
            max: 100,
            now: g.load != null ? `${g.load.toFixed(0)} %` : '—',
            sub: `${g.name}${g.temp != null ? ` · ${g.temp.toFixed(0)} °C` : ''}${g.mem_used != null && g.mem_total ? ` · ${(g.mem_used / 1024).toFixed(1)} / ${(g.mem_total / 1024).toFixed(0)} Go` : ''}${g.power != null ? ` · ${g.power.toFixed(0)} W` : ''}`,
            color: 'text-success',
          },
        ]
      : []),
  ]
})
</script>

<template>
  <UCard :ui="{ body: 'p-4 sm:p-4' }">
    <template #header>
      <h2 class="text-base font-semibold text-highlighted">Moniteur système</h2>
      <p class="text-sm text-muted">Charge de la machine pendant les conversions, une minute d’historique. Omni travaille sur le processeur ; le GPU sert à l’affichage 3D.</p>
    </template>
    <UAlert v-if="failed" color="warning" variant="subtle" icon="i-ri-error-warning-line" title="Mesures indisponibles" />
    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <div v-for="c in charts" :key="c.key" class="min-w-0 rounded-md border border-default p-3">
        <div class="flex items-center justify-between gap-2">
          <span class="flex items-center gap-1.5 text-sm text-muted"><UIcon :name="c.icon" class="size-4" />{{ c.label }}</span>
          <span class="text-lg font-semibold tabular-nums text-highlighted">{{ c.now }}</span>
        </div>
        <svg :viewBox="`0 0 ${W} ${H}`" class="mt-2 h-14 w-full" :class="c.color" preserveAspectRatio="none" role="img" :aria-label="`${c.label} : ${c.now}`">
          <line :x1="0" :x2="W" :y1="H / 2" :y2="H / 2" stroke="currentColor" stroke-opacity="0.12" stroke-dasharray="3 4" />
          <path :d="path(c.values, c.max, true)" fill="currentColor" fill-opacity="0.15" />
          <path :d="path(c.values, c.max)" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
        </svg>
        <p class="mt-1 truncate text-xs text-muted" :title="c.sub">{{ c.sub }}</p>
      </div>
    </div>
  </UCard>
</template>
