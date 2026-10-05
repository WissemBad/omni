<script setup lang="ts">
import type { ViewerRoot } from '~/utils/types'

/** Add a folder of models to the viewer (a decompiled addon...) or forget one. Folders are only ever read. */
const props = defineProps<{ roots: ViewerRoot[] }>()
const emit = defineEmits<{ added: [id: string]; removed: [id: string] }>()
const open = defineModel<boolean>('open', { default: false })
const sid = useSourceId()

const path = ref('')
const busy = ref(false)
const picking = ref(false)
const error = ref('')

const external = computed(() => props.roots.filter((r) => r.external))

async function add(p = path.value) {
  if (!p.trim()) return
  busy.value = true
  error.value = ''
  try {
    const r = await api<{ id: string }>(`/${sid.value}/output/roots?path=${encodeURIComponent(p.trim())}`, { method: 'POST' })
    path.value = ''
    emit('added', r.id)
    open.value = false
  } catch (e) {
    error.value = apiError(e)
  } finally {
    busy.value = false
  }
}

async function pick() {
  picking.value = true
  error.value = ''
  try {
    const r = await api<{ path: string }>(`/${sid.value}/output/pick`, { method: 'POST' })
    if (r.path) await add(r.path)
  } catch (e) {
    error.value = apiError(e)
  } finally {
    picking.value = false
  }
}

async function remove(r: ViewerRoot) {
  await api(`/${sid.value}/output/roots/${r.id}`, { method: 'DELETE' })
  emit('removed', r.id)
}
</script>

<template>
  <UModal v-model:open="open" title="Ouvrir un dossier de modèles" description="Un addon décompilé, ou n’importe quel dossier qui contient des .mdl. Il est seulement lu, jamais modifié.">
    <template #body>
      <div class="space-y-5">
        <UButton
          icon="i-ri-folder-open-line"
          label="Parcourir mon ordinateur…"
          color="primary"
          variant="soft"
          size="lg"
          block
          :loading="picking"
          @click="pick"
        />

        <USeparator label="ou" />

        <UFormField label="Chemin du dossier" :error="error || undefined" description="Le dossier qui contient models/ et materials/ (ou plusieurs addons).">
          <UFieldGroup class="w-full">
            <UInput v-model="path" class="flex-1" icon="i-ri-folder-3-line" placeholder="C:\Users\…\addons\mon_addon" @keydown.enter="add()" />
            <UButton label="Ouvrir" color="neutral" variant="solid" :loading="busy" :disabled="!path.trim()" @click="add()" />
          </UFieldGroup>
        </UFormField>

        <section v-if="external.length" class="space-y-2">
          <h3 class="text-sm font-semibold text-highlighted">Dossiers ouverts</h3>
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
