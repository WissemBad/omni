<script setup lang="ts">
/** Conversion options for a set of props, shared with the Réglages page (server settings). */
const props = defineProps<{ keys: string[] }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ started: [job: string] }>()

const sid = useSourceId()
const { load, save } = useSettings()
const { info: system, load: loadSystem } = useSystem()
const opts = reactive({
  physics: true,
  collision: 'game',
  tex_quality: 'max',
  lossless_normals: false,
  blend: false,
  workers: 8,
})
const busy = ref(false)

watch(open, async (v) => {
  if (!v) return
  const s = await load(true).catch(() => null)
  if (s)
    Object.assign(opts, {
      physics: s.props.physics,
      collision: s.props.collision,
      tex_quality: s.textures.quality,
      lossless_normals: s.textures.lossless_normals,
      blend: s.props.blend,
      workers: s.props.workers,
    })
  if (!system.value) loadSystem()
})

const collisions = [
  {
    label: 'Celle du jeu (recommandé)',
    value: 'game',
    description:
      'Reprend les formes physiques du jeu quand elles existent, sinon une enveloppe par pièce.',
  },
  {
    label: 'Par pièces',
    value: 'parts',
    description: 'Une enveloppe convexe par morceau de l’objet. Rapide et fidèle.',
  },
  {
    label: 'Enveloppe unique',
    value: 'hull',
    description: 'Un seul volume convexe. Très léger, mais comble les creux (portes, tables).',
  },
  {
    label: 'Décomposition précise',
    value: 'coacd',
    description: 'CoACD : épouse les formes concaves. Très lent (≈ 2 min par objet).',
  },
]
const qualities = [
  {
    label: 'Maximum',
    value: 'max',
    description: 'Résolution du jeu, normales 2K. Le plus fidèle.',
  },
  { label: 'Élevée', value: 'high', description: 'Couleur jusqu’à 4K, normales 2K.' },
  { label: 'Équilibrée', value: 'balanced', description: 'Couleur 2K, normales 1K.' },
  { label: 'Légère', value: 'light', description: 'Couleur 1K, normales 512.' },
]

async function start() {
  busy.value = true
  try {
    // the choices made here become the defaults (same as the Réglages page)
    await save({
      props: {
        physics: opts.physics,
        collision: opts.collision,
        blend: opts.blend,
        workers: opts.workers,
      },
      textures: { quality: opts.tex_quality, lossless_normals: opts.lossless_normals },
    }).catch(() => {})
    const { job } = await api<{ job: string }>(`/${sid.value}/props/convert`, {
      method: 'POST',
      body: { keys: props.keys, ...opts },
    })
    open.value = false
    emit('started', job)
  } catch (e) {
    useToast().add({ title: 'Conversion impossible', description: apiError(e), color: 'error' })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UModal v-model:open="open" :title="`Convertir ${keys.length.toLocaleString('fr-FR')} prop${keys.length > 1 ? 's' : ''}`" description="Les modèles sont écrits dans l’addon, prêts pour GMod. Ces choix deviennent les réglages par défaut.">
    <template #body>
      <div class="space-y-6">
        <UFormField label="Qualité des textures" description="Les textures BC1/BC3 du jeu sont toujours copiées sans perte.">
          <URadioGroup v-model="opts.tex_quality" :items="qualities" variant="card" />
        </UFormField>

        <div class="space-y-3">
          <USwitch v-model="opts.physics" label="Collision" description="Sans collision, le prop traverse le décor et les joueurs." />
          <UFormField v-if="opts.physics" label="Forme de collision" :description="collisions.find((c) => c.value === opts.collision)?.description">
            <USelect v-model="opts.collision" :items="collisions" value-key="value" class="w-full" />
          </UFormField>
        </div>

        <div class="space-y-3">
          <USwitch v-model="opts.lossless_normals" label="Normales sans compression" description="Évite les artefacts de bloc DXT, au prix de textures 4× plus lourdes." />
          <USwitch v-model="opts.blend" label="Produire aussi un .blend" description="Un fichier Blender par modèle, matériaux déjà liés aux textures (facultatif)." />
        </div>

        <UFormField :label="`Traitements en parallèle : ${opts.workers}`" :description="`Autant que de cœurs libres (${system?.cpus ?? '?'} sur ce PC).`">
          <USlider v-model="opts.workers" :min="1" :max="Math.max(4, system?.cpus ?? 24)" :step="1" />
        </UFormField>
      </div>
    </template>
    <template #footer>
      <div class="flex w-full items-center justify-between gap-3">
        <p class="text-xs text-muted">Ferme GMod avant : il verrouille les fichiers de l’addon.</p>
        <div class="flex gap-2">
          <UButton label="Annuler" color="neutral" variant="ghost" @click="open = false" />
          <UButton label="Lancer la conversion" icon="i-ri-play-large-line" color="primary" variant="solid" :loading="busy" @click="start" />
        </div>
      </div>
    </template>
  </UModal>
</template>
