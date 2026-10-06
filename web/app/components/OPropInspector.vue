<script setup lang="ts">
import type { Prop, PropDetails } from '~/utils/types'

/** Inspector of a game prop (before conversion): figures, conversion state, game materials and raw data. */
const props = defineProps<{
  info: PropDetails
  prop: Prop
  stats: { label: string; value: string; icon: string }[]
  gmodPath: string
  selected: boolean
}>()
const emit = defineEmits<{
  texture: [material: number, texture: number]
  convert: []
  select: []
}>()
const tab = defineModel<string>('tab', { default: 'summary' })
const sid = useSourceId()
const toast = useToast()

/** The prop as a glTF .glb (Blender and other tools), written in the game's exports folder by a job. */
async function exportGltf() {
  try {
    await api(`/${sid.value}/models/gltf`, { method: 'POST', body: { keys: [props.info.key] } })
    toast.add({
      title: 'Export glTF lancé',
      description: 'Le .glb sera dans exports/gltf du jeu.',
      icon: 'i-ri-shape-2-line',
    })
  } catch (e) {
    toast.add({ title: 'Export glTF impossible', description: apiError(e), color: 'error' })
  }
}

const ALL = [
  { label: 'Résumé', value: 'summary', icon: 'i-ri-dashboard-line' },
  { label: 'Matériaux', value: 'materials', icon: 'i-ri-palette-line' },
  { label: 'Données', value: 'data', icon: 'i-ri-database-2-line' },
]
const TABS = computed(() =>
  ALL.map((t) => (t.value === tab.value ? t : { ...t, label: undefined, 'aria-label': t.label })),
)
const out = computed(() => props.info.output)
const textures = computed(() => {
  const seen = new Map<string, { key: string; role: string; name: string }>()
  for (const m of props.info.materials)
    for (const t of m.textures)
      if (t.found !== false && !seen.has(t.key))
        seen.set(t.key, { key: t.key, role: t.role, name: t.name || t.slot })
  return [...seen.values()]
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <UTabs v-model="tab" :items="TABS" :content="false" size="sm" class="shrink-0 px-3 pt-3" :ui="{ list: 'overflow-x-auto', trigger: 'px-2' }" />

    <div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-3">
      <!-- summary -->
      <div v-if="tab === 'summary'" class="space-y-4">
        <UAlert
          v-if="out.converted"
          color="success"
          variant="subtle"
          icon="i-ri-checkbox-circle-line"
          title="Converti pour GMod"
          :description="`Mis à jour ${timeAgo(out.mtime)}.`"
          :actions="[{ label: 'Visionneuse', icon: 'i-ri-eye-line', color: 'success', variant: 'outline', to: { path: `/${sid}/viewer`, query: { m: out.path } } }, { label: 'Reconvertir', icon: 'i-ri-hammer-line', color: 'neutral', variant: 'ghost', onClick: () => emit('convert') }]"
        />
        <UAlert
          v-else
          color="neutral"
          variant="subtle"
          icon="i-ri-hammer-line"
          title="Pas encore converti"
          description="La conversion garde les textures du jeu à leur qualité d’origine (sauf normales) et la collision du jeu."
          :actions="[{ label: 'Convertir', icon: 'i-ri-hammer-line', color: 'primary', variant: 'solid', onClick: () => emit('convert') }]"
        />

        <OStatGrid :stats="stats" />

        <section v-if="textures.length" class="space-y-2">
          <div class="flex items-baseline justify-between">
            <h3 class="text-sm font-semibold text-highlighted">Textures du jeu</h3>
            <UButton label="Tout voir" size="xs" color="neutral" variant="link" @click="tab = 'materials'" />
          </div>
          <div class="grid grid-cols-5 gap-1.5">
            <UTooltip v-for="t in textures.slice(0, 15)" :key="t.key" :text="`${texRoleLabel(t.role)} · ${t.name}`">
              <NuxtLink :to="{ path: `/${sid}/textures`, query: { k: t.key } }" class="block aspect-square overflow-hidden rounded-md border border-default bg-elevated transition hover:border-primary">
                <img :src="gameTextureUrl(sid, t.key, { size: 64, role: t.role })" alt="" loading="lazy" class="size-full object-cover" />
              </NuxtLink>
            </UTooltip>
          </div>
        </section>

        <UAlert v-if="info.warnings.length" color="warning" variant="subtle" icon="i-ri-alert-line" :title="`${info.warnings.length} avertissement(s) de lecture`" :description="info.warnings.slice(0, 3).join(' · ')" />

        <div class="flex flex-col gap-1.5">
          <UButton
            :icon="selected ? 'i-ri-checkbox-circle-fill' : 'i-ri-add-circle-line'"
            :label="selected ? 'Retirer de la sélection' : 'Ajouter à la sélection'"
            color="neutral"
            variant="ghost"
            class="justify-start"
            @click="emit('select')"
          />
          <UButton icon="i-ri-file-copy-line" label="Copier le chemin GMod" color="neutral" variant="ghost" class="justify-start" @click="copy(gmodPath, 'Chemin du modèle copié')" />
          <UButton icon="i-ri-terminal-box-line" label="Copier la commande de spawn" color="neutral" variant="ghost" class="justify-start" @click="copy(spawnCommand(gmodPath), 'Commande copiée (console GMod)')" />
          <UButton icon="i-ri-shape-2-line" label="Exporter en .glb (Blender)" color="neutral" variant="ghost" class="justify-start" @click="exportGltf" />
          <UButton v-if="out.converted" icon="i-ri-folder-open-line" label="Afficher dans l’Explorateur" color="neutral" variant="ghost" class="justify-start" @click="reveal(sid, out.path)" />
        </div>
      </div>

      <!-- materials -->
      <OGameMaterials v-else-if="tab === 'materials'" :materials="info.materials" hint="Textures telles que le jeu les stocke, avant conversion. Clique pour les voir en grand." @texture="(mi: number, ti: number) => emit('texture', mi, ti)" />

      <!-- data -->
      <div v-else class="space-y-4 text-xs">
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          <dt class="text-muted">Clé</dt>
          <dd class="flex items-center justify-end gap-1 font-mono text-toned">{{ info.key }}<UButton icon="i-ri-file-copy-line" size="xs" color="neutral" variant="ghost" aria-label="Copier" @click="copy(info.key, 'Clé copiée')" /></dd>
          <dt class="text-muted">Catégorie</dt>
          <dd class="truncate text-right text-toned">{{ info.cat }}</dd>
          <dt class="text-muted">Taille (jeu)</dt>
          <dd class="text-right tabular-nums text-toned">{{ fmtBytes(info.size) }}</dd>
          <dt class="text-muted">Sous-maillages</dt>
          <dd class="text-right tabular-nums text-toned">{{ info.submeshes }}</dd>
          <dt class="text-muted">LOD lu</dt>
          <dd class="text-right tabular-nums text-toned">{{ info.lod }} (disponibles : {{ info.lods.join(', ') || '—' }})</dd>
          <dt class="text-muted">Skinné</dt>
          <dd class="text-right text-toned">{{ info.skinned ? 'oui (exporté en statue)' : 'non' }}</dd>
          <dt class="text-muted">Collision du jeu</dt>
          <dd class="truncate text-right text-toned">{{ info.collision ? `${info.collision.dynamic ? 'dynamique' : 'statique'} · ${info.collision.resource}` : 'aucune' }}</dd>
          <dt class="text-muted">Chemin GMod</dt>
          <dd class="truncate text-right font-mono text-toned" :title="gmodPath">{{ gmodPath }}</dd>
        </dl>
        <section>
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Chemin dans le jeu</h3>
          <p class="break-all rounded-lg bg-elevated p-2 font-mono text-[11px] text-toned">{{ info.name || '—' }}</p>
        </section>
        <section v-if="info.variants.length">
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Variantes des templates ({{ info.variants.length }})</h3>
          <ul class="space-y-0.5">
            <li v-for="v in info.variants" :key="v.index" class="flex justify-between text-toned">
              <span>Skin {{ v.index + 1 }}</span><span class="text-muted">{{ v.slots }} emplacement(s) · {{ v.params }} paramètre(s)</span>
            </li>
          </ul>
        </section>
        <section v-if="info.warnings.length">
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Avertissements</h3>
          <ul class="space-y-0.5 font-mono text-[11px] text-muted">
            <li v-for="(w, i) in info.warnings" :key="i">{{ w }}</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>
