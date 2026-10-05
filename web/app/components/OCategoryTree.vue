<script setup lang="ts">
import type { Category } from '~/utils/types'

/**
 * Folder tree of the props catalog. Clicking a folder filters on it (and everything below);
 * the chevron only opens/closes. Internal folders (`_test`, `_glacier`...) are hidden unless asked for.
 */
const props = withDefaults(
  defineProps<{ categories: Category[]; modelValue: string; rootLabel?: string }>(),
  {
    rootLabel: 'Tout le catalogue',
  },
)
const emit = defineEmits<{ 'update:modelValue': [path: string] }>()

interface Node {
  label: string
  value: string
  count: number
  children?: Node[]
  defaultExpanded?: boolean
}

const showInternal = ref(false)
const total = computed(() => props.categories.filter(visible).reduce((a, c) => a + c.n, 0))

function visible(c: Category) {
  return showInternal.value || !c.cat.startsWith('_')
}

const tree = computed<Node[]>(() => {
  const top = new Map<string, Node>()
  for (const c of props.categories.filter(visible)) {
    const parts = c.cat.split('/')
    let level = top
    let path = ''
    for (const [i, part] of parts.entries()) {
      path = path ? `${path}/${part}` : part
      let node = level.get(path)
      if (!node) {
        node = { label: titleCase(part), value: path, count: 0 }
        level.set(path, node)
      }
      node.count += c.n
      if (i < parts.length - 1) {
        const holder = node as Node & { map?: Map<string, Node> }
        holder.map ??= new Map()
        level = holder.map
      }
    }
  }
  const finish = (m: Map<string, Node>): Node[] =>
    [...m.values()]
      .sort((a, b) => b.count - a.count)
      .map((n) => {
        const map = (n as Node & { map?: Map<string, Node> }).map
        const { label, value, count } = n
        return map ? { label, value, count, children: finish(map) } : { label, value, count }
      })
  return finish(top)
})

const selected = computed({
  get: () => findNode(tree.value, props.modelValue),
  set: (node: Node | undefined) => emit('update:modelValue', node?.value ?? ''),
})

function findNode(nodes: Node[], value: string): Node | undefined {
  for (const n of nodes) {
    if (n.value === value) return n
    const sub = n.children && findNode(n.children, value)
    if (sub) return sub
  }
}

// open the path of the selected folder
const expanded = ref<string[]>([])
watch(
  () => props.modelValue,
  (v) => {
    const parts = v.split('/')
    const open = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
    expanded.value = [...new Set([...expanded.value, ...open])]
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <UButton
      icon="i-ri-stack-line"
      :label="rootLabel"
      :color="modelValue === '' ? 'primary' : 'neutral'"
      :variant="modelValue === '' ? 'soft' : 'ghost'"
      block
      class="mb-1 justify-start"
      :ui="{ label: 'flex-1 text-left' }"
      @click="emit('update:modelValue', '')"
    >
      <template #trailing>
        <span class="text-xs tabular-nums text-muted">{{ total.toLocaleString('fr-FR') }}</span>
      </template>
    </UButton>

    <UTree
      v-model="selected"
      v-model:expanded="expanded"
      :items="tree"
      :get-key="(i: Node) => i.value"
      size="sm"
      class="min-h-0 flex-1 overflow-y-auto"
    >
      <template #item-trailing="{ item, expanded: isOpen, handleToggle }">
        <span class="text-xs tabular-nums text-muted">{{ (item as Node).count.toLocaleString('fr-FR') }}</span>
        <UButton
          v-if="(item as Node).children?.length"
          icon="i-ri-arrow-down-s-line"
          size="xs"
          color="neutral"
          variant="ghost"
          :class="isOpen && '[&_svg]:rotate-180'"
          :aria-label="isOpen ? 'Replier' : 'Déplier'"
          @click.stop.prevent="handleToggle()"
        />
      </template>
    </UTree>

    <USwitch v-model="showInternal" size="sm" label="Dossiers internes" description="_test, _glacier…" class="mt-2 shrink-0 px-1" />
  </div>
</template>
