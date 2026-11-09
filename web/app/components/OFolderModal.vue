<script setup lang="ts">
import type { ViewerRoot } from '~/utils/types'

/**
 * Open models in the viewer: a .mdl (its addon opens, with that model selected), a .gma archive, a folder, or one of
 * the addons Garry's Mod has installed. Everything opened here is only read, never modified.
 */
const props = defineProps<{ roots: ViewerRoot[] }>()
const emit = defineEmits<{ opened: [root: string, model: string]; removed: [id: string] }>()
const open = defineModel<boolean>('open', { default: false })
const sid = useSourceId()

interface Installed {
  label: string
  path: string
  kind: 'folder' | 'gma' | 'workshop'
}

const path = ref('')
const busy = ref('')
const error = ref('')
const installed = ref<Installed[]>([])
const filter = ref('')

const external = computed(() => props.roots.filter((r) => r.external))
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return (
    f ? installed.value.filter((i) => i.label.toLowerCase().includes(f)) : installed.value
  ).slice(0, 200)
})

watch(open, async (o) => {
  if (!o) return
  error.value = ''
  installed.value = await api<Installed[]>(`/${sid.value}/output/installed`).catch(() => [])
})

async function openPath(p = path.value, key = 'path') {
  if (!p.trim()) return
  busy.value = key
  error.value = ''
  try {
    const r = await api<{ root: string; model: string }>(
      `/${sid.value}/output/open?path=${encodeURIComponent(p.trim())}`,
      { method: 'POST' },
    )
    path.value = ''
    emit('opened', r.root, r.model)
    open.value = false
  } catch (e) {
    error.value = apiError(e)
  } finally {
    busy.value = ''
  }
}

async function pick(kind: 'file' | 'folder') {
  busy.value = kind
  error.value = ''
  try {
    const r = await api<{ path: string }>(
      `/${sid.value}/output/${kind === 'file' ? 'pick-file' : 'pick'}`,
      { method: 'POST' },
    )
    if (r.path) await openPath(r.path, kind)
  } catch (e) {
    error.value = apiError(e)
  } finally {
    if (busy.value === kind) busy.value = ''
  }
}

async function remove(r: ViewerRoot) {
  await api(`/${sid.value}/output/roots/${r.id}`, { method: 'DELETE' })
  emit('removed', r.id)
}

const KIND_ICON = {
  folder: 'i-ri-folder-3-line',
  gma: 'i-ri-archive-line',
  workshop: 'i-ri-steam-line',
}
</script>

<template>
  <UModal v-model:open="open" title="Ouvrir des modèles" description="Un modèle .mdl, une archive .gma du Workshop ou le dossier d’un addon. Ils sont seulement lus, jamais modifiés.">
    <template #body>
      <div class="space-y-5">
        <div class="grid gap-2 sm:grid-cols-2">
          <UButton icon="i-ri-file-3-line" label="Un fichier .mdl ou .gma…" color="primary" variant="soft" size="lg" block :loading="busy === 'file'" @click="pick('file')" />
          <UButton icon="i-ri-folder-open-line" label="Un dossier…" color="neutral" variant="soft" size="lg" block :loading="busy === 'folder'" @click="pick('folder')" />
        </div>

        <UFormField label="Ou un chemin" :error="error || undefined">
          <UFieldGroup class="w-full">
            <UInput v-model="path" class="flex-1" icon="i-ri-route-line" placeholder="C:\…\addons\mon_addon, …\modele.mdl ou …\addon.gma" @keydown.enter="openPath()" />
            <UButton label="Ouvrir" color="neutral" variant="solid" :loading="busy === 'path'" :disabled="!path.trim()" @click="openPath()" />
          </UFieldGroup>
        </UFormField>

        <section v-if="installed.length" class="space-y-2">
          <div class="flex items-center justify-between gap-2">
            <h3 class="text-sm font-semibold text-highlighted">Addons de Garry’s Mod</h3>
            <UInput v-if="installed.length > 8" v-model="filter" size="xs" icon="i-ri-search-line" placeholder="Filtrer" class="w-40" />
          </div>
          <ul class="max-h-56 divide-y divide-default overflow-y-auto rounded-lg border border-default">
            <li v-for="a in shown" :key="a.path">
              <button type="button" class="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-elevated disabled:opacity-60" :disabled="!!busy" @click="openPath(a.path, a.path)">
                <UIcon :name="busy === a.path ? 'i-ri-loader-4-line' : KIND_ICON[a.kind]" class="size-5 shrink-0 text-muted" :class="busy === a.path && 'animate-spin'" />
                <span class="min-w-0 flex-1 truncate text-sm text-highlighted" :title="a.path">{{ a.label }}</span>
                <span class="shrink-0 text-xs text-muted">{{ a.kind === 'folder' ? 'dossier' : '.gma' }}</span>
              </button>
            </li>
          </ul>
        </section>

        <section v-if="external.length" class="space-y-2">
          <h3 class="text-sm font-semibold text-highlighted">Déjà ouverts</h3>
          <ul class="divide-y divide-default rounded-lg border border-default">
            <li v-for="r in external" :key="r.id" class="flex items-center gap-3 px-3 py-2">
              <UIcon :name="r.exists ? 'i-ri-folder-3-line' : 'i-ri-folder-warning-line'" class="size-5 shrink-0" :class="r.exists ? 'text-muted' : 'text-error'" />
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-highlighted">{{ r.label }}</p>
                <p class="truncate text-xs text-muted" :title="r.path">{{ r.exists ? r.path : 'Dossier introuvable · ' + r.path }}</p>
              </div>
              <UButton icon="i-ri-delete-bin-line" size="xs" color="neutral" variant="ghost" aria-label="Oublier ce dossier" @click="remove(r)" />
            </li>
          </ul>
        </section>
      </div>
    </template>
  </UModal>
</template>
