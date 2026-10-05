<script setup lang="ts">
import type { OutputModel } from '~/utils/types'

/**
 * Everything known about one converted model, in tabs: quality checks and key figures, variants (skins,
 * bodygroups, LODs), materials and textures, skeleton, and the raw data (sequences, hitboxes, files...).
 */
const props = defineProps<{ model: OutputModel; gmodPath: string }>()
const emit = defineEmits<{ texture: [material: number, texture: number]; reconvert: [] }>()

const tab = defineModel<string>('tab', { default: 'summary' })
const skin = defineModel<number>('skin', { default: 0 })
const groups = defineModel<number[]>('groups', { default: () => [] })
const lod = defineModel<number>('lod', { default: 0 })
const bone = defineModel<number>('bone', { default: -1 })
const sid = useSourceId()

const m = computed(() => props.model)
const skinCount = computed(() => m.value.skin_materials.length)
const variantGroups = computed(() =>
  m.value.model.bodyparts.map((bp, b) => ({ bp, b })).filter(({ bp }) => bp.models.length > 1),
)
const usedMaterials = computed(() => [...new Set(m.value.skin_materials[skin.value] ?? [])])

const ALL_TABS = [
  { label: 'Résumé', value: 'summary', icon: 'i-ri-dashboard-line' },
  { label: 'Variantes', value: 'variants', icon: 'i-ri-shapes-line' },
  { label: 'Matériaux', value: 'materials', icon: 'i-ri-palette-line' },
  { label: 'Os', value: 'bones', icon: 'i-ri-node-tree' },
  { label: 'Données', value: 'data', icon: 'i-ri-database-2-line' },
]
const TABS = computed(() =>
  ALL_TABS.map((t) =>
    t.value === tab.value ? t : { ...t, label: undefined, 'aria-label': t.label },
  ),
)

const verdict = computed(() => VERDICT[m.value.verdict])
const problems = computed(
  () => m.value.checks.filter((c) => c.level === 'warn' || c.level === 'error').length,
)

/** Triangles of what is currently shown (the chosen option of every bodygroup, in the chosen LOD). */
const shownTriangles = computed(() => {
  const base = m.value.lods[lod.value]?.triangles ?? m.value.triangles
  if (lod.value || !variantGroups.value.length) return base
  let all = 0
  let shown = 0
  m.value.model.bodyparts.forEach((bp, b) => {
    for (const [i, mod] of bp.models.entries()) {
      all += mod.triangles
      if (i === (groups.value[b] ?? 0)) shown += mod.triangles
    }
  })
  return all ? shown : base
})

const stats = computed(() => {
  const cm = m.value.extent?.map((v) => (v / 39.37) * 100)
  return [
    {
      label: 'Triangles',
      value: shownTriangles.value.toLocaleString('fr-FR'),
      icon: 'i-ri-shape-line',
    },
    { label: 'Matériaux', value: String(m.value.materials.length), icon: 'i-ri-palette-line' },
    {
      label: 'Textures',
      value: `${m.value.texture_count} · ${fmtBytes(m.value.texture_bytes)}`,
      icon: 'i-ri-image-line',
    },
    { label: 'Os', value: String(m.value.model.bones.length), icon: 'i-ri-node-tree' },
    {
      label: 'Dimensions',
      value: cm ? cm.map((v) => Math.round(v)).join(' × ') + ' cm' : '—',
      icon: 'i-ri-ruler-line',
    },
    {
      label: 'Masse',
      value: m.value.model.mass ? `${m.value.model.mass.toLocaleString('fr-FR')} kg` : '—',
      icon: 'i-ri-scales-3-line',
    },
    { label: 'Surface', value: m.value.model.surfaceprop || '—', icon: 'i-ri-stack-line' },
    { label: 'Sur le disque', value: fmtBytes(m.value.bytes), icon: 'i-ri-hard-drive-2-line' },
  ]
})

function setGroup(b: number, v: number) {
  const next = [...groups.value]
  next[b] = v
  groups.value = next
}

function resetVariants() {
  skin.value = 0
  groups.value = m.value.model.bodyparts.map(() => 0)
  lod.value = 0
}

const sequenceColumns = [
  { accessorKey: 'name', header: 'Nom' },
  { accessorKey: 'frames', header: 'Images' },
  { accessorKey: 'fps', header: 'i/s' },
  {
    accessorKey: 'loop',
    header: 'Boucle',
    cell: ({ row }: { row: { original: { loop: boolean } } }) => (row.original.loop ? 'oui' : '—'),
  },
]

const sourceLink = computed(() => {
  const s = m.value.source
  if (s)
    return {
      to: `/${sid.value}/models?tab=props&k=${s.key}`,
      label: 'Voir dans Modèles',
      detail: `${s.cat} · ${s.key}`,
    }
  if (m.value.kind === 'character' && !m.value.external) {
    return {
      to: `/${sid.value}/models?tab=characters&id=outfit_${m.value.stem}`,
      label: 'Voir dans Modèles',
      detail: m.value.model.name,
    }
  }
  return null
})

const spawn = computed(() =>
  m.value.kind === 'character' ? modelCommand(props.gmodPath) : spawnCommand(props.gmodPath),
)
const spawnLabel = computed(() =>
  m.value.kind === 'character'
    ? 'Copier la commande (devenir ce personnage)'
    : 'Copier la commande de spawn',
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <UTabs v-model="tab" :items="TABS" :content="false" size="sm" class="shrink-0 px-3 pt-3" :ui="{ list: 'overflow-x-auto', trigger: 'px-2' }" />

    <div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-3">
      <!-- ------------------------------------------------------------------ summary -->
      <div v-if="tab === 'summary'" class="space-y-4">
        <UAlert
          :color="verdict.color"
          variant="subtle"
          :icon="verdict.icon"
          :title="verdict.label"
          :description="problems ? `${problems} point${problems > 1 ? 's' : ''} à regarder ci-dessous.` : 'Tous les contrôles automatiques sont passés.'"
        />

        <ul class="space-y-2">
          <li v-for="c in m.checks" :key="c.title" class="flex gap-2.5">
            <UIcon :name="CHECK_STYLE[c.level].icon" class="mt-0.5 size-4 shrink-0" :class="CHECK_STYLE[c.level].color" />
            <div class="min-w-0">
              <p class="text-sm text-highlighted">{{ c.title }}</p>
              <p v-if="c.detail" class="text-xs text-muted">{{ c.detail }}</p>
            </div>
          </li>
        </ul>

        <USeparator />

        <div class="grid grid-cols-2 gap-2">
          <UCard v-for="s in stats" :key="s.label" :ui="{ body: 'p-2.5 sm:p-2.5' }">
            <p class="flex items-center gap-1.5 text-xs text-muted"><UIcon :name="s.icon" class="size-3.5" />{{ s.label }}</p>
            <p class="mt-0.5 truncate text-sm font-semibold tabular-nums text-highlighted" :title="s.value">{{ s.value }}</p>
          </UCard>
        </div>

        <UCard v-if="sourceLink" :ui="{ body: 'flex items-center gap-3 p-3 sm:p-3' }">
          <div class="min-w-0 flex-1">
            <p class="text-xs text-muted">Origine</p>
            <p class="truncate text-sm text-toned" :title="sourceLink.detail">{{ sourceLink.detail }}</p>
          </div>
          <UButton :to="sourceLink.to" :label="sourceLink.label" icon="i-ri-arrow-right-up-line" trailing size="xs" color="neutral" variant="outline" />
        </UCard>

        <div class="flex flex-col gap-1.5">
          <UButton v-if="m.source" icon="i-ri-hammer-line" label="Reconvertir avec d’autres réglages" color="primary" variant="soft" class="justify-start" @click="emit('reconvert')" />
          <UButton icon="i-ri-file-copy-line" label="Copier le chemin GMod" color="neutral" variant="ghost" class="justify-start" @click="copy(gmodPath, 'Chemin du modèle copié')" />
          <UButton icon="i-ri-terminal-box-line" :label="spawnLabel" color="neutral" variant="ghost" class="justify-start" @click="copy(spawn, 'Commande copiée (console GMod)')" />
          <UButton icon="i-ri-folder-open-line" label="Afficher dans l’Explorateur" color="neutral" variant="ghost" class="justify-start" @click="reveal(sid, m.path)" />
        </div>
      </div>

      <!-- ------------------------------------------------------------------ variants -->
      <div v-else-if="tab === 'variants'" class="space-y-5">
        <section v-if="m.lods.length > 1" class="space-y-2">
          <h3 class="text-sm font-semibold text-highlighted">Niveaux de détail</h3>
          <UFieldGroup class="flex w-full">
            <UButton
              v-for="l in m.lods"
              :key="l.lod"
              :label="`LOD ${l.lod}`"
              size="xs"
              color="neutral"
              :variant="lod === l.lod ? 'solid' : 'outline'"
              class="flex-1 justify-center"
              @click="lod = l.lod"
            />
          </UFieldGroup>
          <dl class="grid grid-cols-[auto_1fr_1fr] gap-x-3 gap-y-1 text-xs">
            <template v-for="l in m.lods" :key="l.lod">
              <dt class="text-muted">LOD {{ l.lod }}</dt>
              <dd class="text-right tabular-nums text-toned">{{ l.triangles.toLocaleString('fr-FR') }} tri</dd>
              <dd class="text-right tabular-nums text-muted">{{ l.lod ? `dès ${l.switch}` : 'au plus près' }}</dd>
            </template>
          </dl>
        </section>

        <section v-if="skinCount > 1" class="space-y-2">
          <h3 class="text-sm font-semibold text-highlighted">Skin <span class="font-normal text-muted">({{ skinCount }} jeux de matériaux)</span></h3>
          <div class="flex flex-wrap gap-1.5">
            <UButton
              v-for="s in skinCount"
              :key="s"
              :label="String(s - 1)"
              size="xs"
              color="neutral"
              :variant="skin === s - 1 ? 'solid' : 'outline'"
              class="min-w-8 justify-center"
              @click="skin = s - 1"
            />
          </div>
        </section>

        <section v-if="variantGroups.length" class="space-y-3">
          <h3 class="text-sm font-semibold text-highlighted">Bodygroups <span class="font-normal text-muted">({{ variantGroups.length }})</span></h3>
          <UFormField v-for="{ bp, b } in variantGroups" :key="b" :label="titleCase(bp.name)" size="sm">
            <USelect
              :model-value="groups[b] ?? 0"
              :items="bp.models.map((mod, i) => ({ label: mod.empty ? 'Aucun' : `${optionLabel(mod.name)} · ${mod.triangles.toLocaleString('fr-FR')} tri`, value: i }))"
              class="w-full"
              @update:model-value="(v: number) => setGroup(b, v)"
            />
          </UFormField>
        </section>

        <UEmpty
          v-if="m.lods.length <= 1 && skinCount <= 1 && !variantGroups.length"
          icon="i-ri-shapes-line"
          title="Un seul aspect"
          description="Ce modèle n’a ni skin, ni bodygroup, ni niveau de détail alternatif."
        />
        <UButton
          v-else
          icon="i-ri-restart-line"
          label="Tout réinitialiser"
          size="sm"
          color="neutral"
          variant="outline"
          @click="resetVariants"
        />
        <p v-if="m.lods.length > 1" class="text-xs text-muted">Triangles affichés : <span class="tabular-nums text-toned">{{ shownTriangles.toLocaleString('fr-FR') }}</span></p>
      </div>

      <!-- ------------------------------------------------------------------ materials -->
      <OMaterialList
        v-else-if="tab === 'materials'"
        :materials="m.materials"
        :used="usedMaterials"
        :skin="skin"
        :skins="skinCount"
        @texture="(mi: number, ti: number) => emit('texture', mi, ti)"
      />

      <!-- ------------------------------------------------------------------ bones -->
      <template v-else-if="tab === 'bones'">
        <UEmpty
          v-if="m.model.bones.length <= 1"
          icon="i-ri-node-tree"
          title="Prop statique"
          description="Un seul os : le modèle n’est pas animé."
        />
        <OBoneTree v-else v-model="bone" :bones="m.model.bones" :hitboxes="m.model.hitboxes" />
      </template>

      <!-- ------------------------------------------------------------------ data -->
      <UAccordion
        v-else
        type="multiple"
        :default-value="['properties']"
        :items="[
          { label: 'Propriétés', value: 'properties', icon: 'i-ri-information-line', slot: 'properties' },
          { label: `Séquences (${m.model.sequences.length})`, value: 'sequences', icon: 'i-ri-film-line', slot: 'sequences' },
          { label: `Hitboxes (${m.model.hitboxes.length})`, value: 'hitboxes', icon: 'i-ri-shield-line', slot: 'hitboxes', disabled: !m.model.hitboxes.length },
          { label: `Attachements (${m.model.attachments.length})`, value: 'attachments', icon: 'i-ri-crosshair-2-line', slot: 'attachments', disabled: !m.model.attachments.length },
          { label: `Animations incluses (${m.model.includes.length + m.model.ikchains.length})`, value: 'rig', icon: 'i-ri-links-line', slot: 'rig', disabled: !m.model.includes.length && !m.model.ikchains.length },
          { label: `Fichiers (${m.files.filter((f) => f.exists).length})`, value: 'files', icon: 'i-ri-file-list-3-line', slot: 'files' },
        ]"
      >
        <template #properties>
          <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 pb-2 text-xs">
            <dt class="text-muted">Nom interne</dt>
            <dd class="truncate text-right text-toned" :title="m.model.name">{{ m.model.name }}</dd>
            <dt class="text-muted">Version Studio</dt>
            <dd class="text-right tabular-nums text-toned">{{ m.model.version }}</dd>
            <dt class="text-muted">Somme de contrôle</dt>
            <dd class="text-right font-mono text-toned">{{ m.model.checksum }}</dd>
            <dt class="text-muted">Surface physique</dt>
            <dd class="text-right text-toned">{{ m.model.surfaceprop || '—' }}</dd>
            <dt class="text-muted">Masse</dt>
            <dd class="text-right tabular-nums text-toned">{{ m.model.mass }} kg</dd>
            <dt class="text-muted">Sommets</dt>
            <dd class="text-right tabular-nums text-toned">{{ m.vertices.toLocaleString('fr-FR') }}</dd>
            <dt class="text-muted">Boîte de collision</dt>
            <dd class="text-right tabular-nums text-toned">{{ m.model.hull[0].map((v) => Math.round(v)).join(', ') }} → {{ m.model.hull[1].map((v) => Math.round(v)).join(', ') }}</dd>
            <dt class="text-muted">Collision</dt>
            <dd class="text-right tabular-nums text-toned">{{ m.collision ? `${m.collision.pieces} pièce(s) · ${m.collision.triangles} tri` : 'aucune' }}</dd>
          </dl>
          <div v-if="m.model.flags.length" class="flex flex-wrap gap-1 pb-2">
            <UBadge v-for="f in m.model.flags" :key="f" :label="f" size="sm" color="neutral" variant="subtle" />
          </div>
        </template>
        <template #sequences>
          <UTable :data="m.model.sequences" :columns="sequenceColumns" class="pb-2 text-xs" :ui="{ th: 'py-1.5 text-xs', td: 'py-1.5 text-xs' }" />
          <p v-if="m.model.poseparams.length" class="pb-2 text-xs text-muted">
            Paramètres de pose : {{ m.model.poseparams.map((p) => p.name).join(', ') }}
          </p>
        </template>
        <template #hitboxes>
          <ul class="space-y-1 pb-2 text-xs">
            <li v-for="(h, i) in m.model.hitboxes" :key="i" class="flex items-center gap-2">
              <span class="min-w-0 flex-1 truncate text-toned">{{ m.model.bones[h.bone]?.name ?? h.bone }}</span>
              <UBadge :label="HITGROUP[h.group] ?? String(h.group)" size="sm" color="neutral" variant="subtle" />
              <span class="w-24 text-right tabular-nums text-muted">{{ h.size.join(' × ') }} cm</span>
            </li>
          </ul>
        </template>
        <template #attachments>
          <ul class="space-y-1 pb-2 text-xs">
            <li v-for="a in m.model.attachments" :key="a.name" class="flex items-center gap-2">
              <span class="min-w-0 flex-1 truncate font-mono text-toned">{{ a.name }}</span>
              <span class="truncate text-muted">{{ m.model.bones[a.bone]?.name }}</span>
            </li>
          </ul>
        </template>
        <template #rig>
          <div class="space-y-2 pb-2 text-xs">
            <p v-for="i in m.model.includes" :key="i" class="flex items-center gap-2 font-mono text-toned">
              <UIcon name="i-ri-links-line" class="size-3.5 text-muted" />{{ i }}
            </p>
            <p v-for="c in m.model.ikchains" :key="c.name" class="text-muted">
              <span class="font-medium text-toned">{{ c.name }}</span> : {{ c.bones.join(' → ') }}
            </p>
          </div>
        </template>
        <template #files>
          <ul class="space-y-1 pb-2">
            <li v-for="f in m.files" :key="f.name" class="flex items-center gap-2 text-xs">
              <UIcon :name="f.exists ? 'i-ri-file-line' : 'i-ri-file-warning-line'" class="size-4 shrink-0" :class="f.exists ? 'text-muted' : f.required ? 'text-error' : 'text-muted'" />
              <span class="min-w-0 flex-1 truncate font-mono" :class="f.exists ? 'text-toned' : 'text-muted line-through'" :title="f.abs">{{ f.name }}</span>
              <span class="tabular-nums text-muted">{{ f.exists ? fmtBytes(f.bytes) : '—' }}</span>
              <UButton v-if="f.path" icon="i-ri-file-copy-line" size="xs" color="neutral" variant="ghost" aria-label="Copier le chemin complet" @click="copy(f.abs, 'Chemin copié')" />
            </li>
          </ul>
        </template>
      </UAccordion>
    </div>
  </div>
</template>
