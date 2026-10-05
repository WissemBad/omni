<script setup lang="ts">
import type { OutputBone, OutputHitbox } from '~/utils/types'

/** Skeleton of a converted model as a tree; choosing a bone highlights it in the 3D view. */
const props = defineProps<{ bones: OutputBone[]; hitboxes: OutputHitbox[] }>()
const selected = defineModel<number>({ default: -1 })

interface Node {
  label: string
  value: string
  index: number
  children?: Node[]
}

const q = ref('')
const expanded = ref<string[]>([])

const roots = computed<Node[]>(() => {
  const nodes: Node[] = props.bones.map((b, i) => ({ label: b.name.replace(/^ValveBiped\./, ''), value: String(i), index: i }))
  const out: Node[] = []
  props.bones.forEach((b, i) => {
    const p = nodes[b.parent]
    if (p) (p.children ??= []).push(nodes[i]!)
    else out.push(nodes[i]!)
  })
  return out
})

watch(
  roots,
  () => (expanded.value = props.bones.length <= 90 ? props.bones.map((_, i) => String(i)) : roots.value.map((n) => n.value)),
  { immediate: true },
)

const boxesOf = computed(() => {
  const m = new Map<number, OutputHitbox[]>()
  for (const h of props.hitboxes) m.set(h.bone, [...(m.get(h.bone) ?? []), h])
  return m
})

const matches = computed(() => {
  const w = q.value.trim().toLowerCase()
  return w ? props.bones.map((b, i) => ({ b, i })).filter(({ b }) => b.name.toLowerCase().includes(w)) : []
})

const current = computed(() => props.bones[selected.value])
const chain = computed(() => {
  const out: string[] = []
  let i = selected.value
  while (props.bones[i]) {
    out.unshift(props.bones[i]!.name)
    i = props.bones[i]!.parent
  }
  return out
})

const model = computed({
  get: () => (selected.value >= 0 ? findNode(roots.value, String(selected.value)) : undefined),
  set: (n: Node | undefined) => (selected.value = n ? n.index : -1),
})

function findNode(nodes: Node[], value: string): Node | undefined {
  for (const n of nodes) {
    if (n.value === value) return n
    const sub = n.children && findNode(n.children, value)
    if (sub) return sub
  }
}
</script>

<template>
  <div class="flex min-h-0 flex-col gap-3">
    <UInput v-model="q" icon="i-ri-search-line" size="sm" placeholder="Chercher un os…" class="w-full">
      <template #trailing>
        <UButton v-if="q" icon="i-ri-close-line" color="neutral" variant="link" size="xs" aria-label="Effacer" @click="q = ''" />
      </template>
    </UInput>

    <div class="max-h-[22rem] min-h-40 overflow-auto rounded-lg border border-default p-1">
      <ul v-if="q" class="space-y-0.5">
        <li v-for="m in matches" :key="m.i">
          <UButton
            :label="m.b.name"
            size="sm"
            block
            class="justify-start"
            :color="selected === m.i ? 'primary' : 'neutral'"
            :variant="selected === m.i ? 'soft' : 'ghost'"
            @click="selected = m.i"
          />
        </li>
        <li v-if="!matches.length" class="p-3 text-center text-xs text-muted">Aucun os ne correspond.</li>
      </ul>
      <UTree v-else v-model="model" v-model:expanded="expanded" :items="roots" :get-key="(i: Node) => i.value" size="sm" :ui="{ listWithChildren: 'ms-2.5 ps-0.5', link: 'whitespace-nowrap' }" class="min-w-max">
        <template #item-trailing="{ item }">
          <UBadge
            v-if="boxesOf.get((item as Node).index)?.length"
            :label="String(boxesOf.get((item as Node).index)?.length)"
            color="neutral"
            variant="subtle"
            size="sm"
            icon="i-ri-shield-line"
          />
        </template>
      </UTree>
    </div>

    <UCard v-if="current" :ui="{ body: 'space-y-2 p-3 sm:p-3' }">
      <p class="break-all font-mono text-xs font-semibold text-highlighted">{{ current.name }}</p>
      <p class="text-xs text-muted">{{ chain.slice(0, -1).join(' › ') || 'Os racine' }}</p>
      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">Position</dt>
        <dd class="text-right tabular-nums text-toned">{{ current.pos.map((v) => (v * 100).toFixed(1)).join(' · ') }} cm</dd>
        <dt class="text-muted">Enfants</dt>
        <dd class="text-right tabular-nums text-toned">{{ bones.filter((b) => b.parent === selected).length }}</dd>
      </dl>
      <div v-if="boxesOf.get(selected)?.length" class="flex flex-wrap gap-1">
        <UBadge
          v-for="(h, i) in boxesOf.get(selected)"
          :key="i"
          :label="`${HITGROUP[h.group] ?? h.group} · ${h.size.join('×')} cm`"
          color="neutral"
          variant="subtle"
          size="sm"
        />
      </div>
    </UCard>
    <p v-else class="text-xs text-muted">Choisis un os pour le repérer dans la vue 3D (point jaune).</p>
  </div>
</template>
