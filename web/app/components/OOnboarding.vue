<script setup lang="ts">
import type { Job } from '~/utils/types'

/**
 * The welcome tour of a fresh install: what omni does, the game to use, Garry's Mod and where files go. It can be
 * skipped at any step and is shown again from Réglages. Nothing is configured behind the user's back: every step only
 * calls the same routes as the pages it points to.
 */
interface Game {
  id: string
  title: string
  engine: 'glacier' | 'unreal'
  root: string
  version?: string
  supported: boolean
  reason?: string
  ready?: boolean
}

const open = defineModel<boolean>('open', { default: false })

const toast = useToast()
const jobs = useJobs()
const { save } = useSettings()
const { load: loadSources } = useSources()

const STEPS = ['Bienvenue', 'Ce qu’omni fait', 'Ton jeu', 'Garry’s Mod', 'Prêt'] as const
const step = ref(0)
const direction = ref(1)
const last = STEPS.length - 1

function go(to: number) {
  direction.value = to >= step.value ? 1 : -1
  step.value = Math.max(0, Math.min(last, to))
}

const FEATURES = [
  {
    icon: 'i-ri-box-3-line',
    title: 'Props et personnages',
    text: 'Chaque modèle devient un .mdl complet pour Garry’s Mod : matériaux, collision, LOD. Les personnages deviennent des playermodels.',
  },
  {
    icon: 'i-ri-image-2-line',
    title: 'Textures',
    text: 'Les textures du jeu, converties en VTF pour GMod ou exportées en PNG, avec leurs normales.',
  },
  {
    icon: 'i-ri-music-2-line',
    title: 'Sons',
    text: 'Toute la bande son rangée par langue et par événement, avec écoute directe et recherche.',
  },
  {
    icon: 'i-ri-eye-line',
    title: 'Visionneuse 3D',
    text: 'Vois chaque modèle comme GMod le chargera : squelette, hitboxes, animations, et une capture prête à partager.',
  },
] as const

// ---- game
const games = ref<Game[]>([])
const candidates = ref<Game[]>([])
const loading = ref(false)
const busy = ref('')

async function loadGames() {
  loading.value = true
  try {
    const r = await api<{ games: Game[]; candidates: Game[] }>('/games')
    games.value = r.games
    candidates.value = r.candidates
  } catch {
    /* the library stays empty: the user can still pick a folder */
  } finally {
    loading.value = false
  }
}

const preparing = (g: Game): Job | undefined =>
  jobs.jobs.value.find(
    (j) =>
      j.kind === 'setup' &&
      ['queued', 'running'].includes(j.phase) &&
      (j.source === g.id || (g.id === '007fl' && !j.source)),
  )

async function prepare(g: Game) {
  busy.value = g.id
  try {
    const { job } = await api<{ job: string }>(
      g.id === '007fl' ? '/setup/auto' : `/games/${g.id}/prepare`,
      { method: 'POST' },
    )
    await jobs.track(job)
  } catch (e) {
    toast.add({ title: 'Préparation impossible', description: apiError(e), color: 'error' })
  } finally {
    busy.value = ''
  }
}

async function add(path?: string) {
  busy.value = path ?? 'pick'
  try {
    const r = path
      ? await api<Game & { job?: string }>('/games', { method: 'POST', body: { path } })
      : await api<(Game & { job?: string }) | { cancelled: true }>('/games/pick', {
          method: 'POST',
        })
    if ('cancelled' in r) return
    await loadGames()
    await loadSources(true).catch(() => [])
    if (!r.job) {
      const g = games.value.find((x) => x.id === r.id)
      if (g && !g.ready) await prepare(g)
    }
  } catch (e) {
    toast.add({ title: 'Jeu non reconnu', description: apiError(e), color: 'error' })
  } finally {
    busy.value = ''
  }
}

watch(
  () => games.value.map((g) => !!preparing(g)).join(),
  async (now, before) => {
    if (before?.includes('true') && !now.includes('true')) {
      await loadGames()
      await loadSources(true).catch(() => [])
    }
  },
)

// ---- Garry's Mod and folders
const gmod = ref<{ installed: boolean } | null>(null)
const home = ref<{ home: string } | null>(null)

watch(
  open,
  async (v) => {
    if (!v) return
    step.value = 0
    loadGames()
    gmod.value = await api<{ installed: boolean }>('/gmod/status').catch(() => null)
    home.value = await api<{ home: string }>('/home').catch(() => null)
  },
  { immediate: true },
)

const openHome = () => api('/reveal', { method: 'POST', body: { target: 'home' } }).catch(() => {})

const readyGame = computed(() => games.value.find((g) => g.ready))

async function finish(target?: string) {
  open.value = false
  await save({ general: { onboarded: true } }).catch(() => {})
  await loadSources(true).catch(() => [])
  await navigateTo(target ?? (readyGame.value ? `/${readyGame.value.id}` : '/games'))
}
const route = useRoute()
const skip = () => finish(route.fullPath)

onMounted(() => {
  const onKey = (e: KeyboardEvent) => {
    if (!open.value) return
    if (e.key === 'ArrowRight' && step.value < last) go(step.value + 1)
    if (e.key === 'ArrowLeft' && step.value > 0) go(step.value - 1)
  }
  window.addEventListener('keydown', onKey)
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
})
</script>

<template>
  <Transition
    enter-active-class="transition-opacity duration-500"
    leave-active-class="transition-opacity duration-300"
    enter-from-class="opacity-0"
    leave-to-class="opacity-0"
  >
    <div
      v-if="open"
      class="fixed inset-0 z-[100] overflow-y-auto bg-default"
      role="dialog"
      aria-modal="true"
      aria-label="Bienvenue dans omni"
    >
      <div class="onb-blob onb-blob-a" />
      <div class="onb-blob onb-blob-b" />

      <div class="relative mx-auto flex min-h-full w-full max-w-3xl flex-col px-5 py-8">
        <header class="flex items-center justify-between">
          <span class="text-lg font-extrabold tracking-tight text-highlighted">omni</span>
          <UButton v-if="step < last" label="Passer la présentation" color="neutral" variant="ghost" size="sm" @click="skip" />
        </header>

        <main class="flex flex-1 items-center py-8">
          <div class="w-full" :class="direction > 0 ? 'onb-in-next' : 'onb-in-prev'" :key="step">
            <!-- 0 -->
            <section v-if="step === 0" key="0" class="w-full space-y-6 text-center">
              <h1 class="onb-title text-5xl font-extrabold tracking-tight sm:text-7xl">Bienvenue dans omni</h1>
              <p class="mx-auto max-w-xl text-lg text-toned">
                Les ressources de tes jeux, prêtes pour Garry’s Mod et pour Blender. Modèles, textures, sons : tout se lit sur ton PC, rien n’est envoyé nulle part.
              </p>
              <UButton label="Commencer" trailing-icon="i-ri-arrow-right-line" size="xl" @click="go(1)" />
            </section>

            <!-- 1 -->
            <section v-else-if="step === 1" key="1" class="w-full space-y-6">
              <div class="space-y-2">
                <h2 class="text-3xl font-bold tracking-tight text-highlighted">Ce qu’omni fait pour toi</h2>
                <p class="text-toned">Quatre ateliers, dans le même outil.</p>
              </div>
              <div class="grid gap-3 sm:grid-cols-2">
                <div
                  v-for="(f, i) in FEATURES"
                  :key="f.title"
                  class="onb-card rounded-xl border border-default bg-elevated/60 p-4 backdrop-blur"
                  :style="{ animationDelay: `${i * 90}ms` }"
                >
                  <UIcon :name="f.icon" class="size-7 text-primary" />
                  <h3 class="mt-3 font-semibold text-highlighted">{{ f.title }}</h3>
                  <p class="mt-1 text-sm text-muted">{{ f.text }}</p>
                </div>
              </div>
            </section>

            <!-- 2 -->
            <section v-else-if="step === 2" key="2" class="w-full space-y-5">
              <div class="space-y-2">
                <h2 class="text-3xl font-bold tracking-tight text-highlighted">Ton jeu</h2>
                <p class="text-toned">
                  Donne le dossier d’un jeu : omni reconnaît le moteur et prépare tout le reste tout seul. 007 First Light, HITMAN et les jeux Unreal Engine 5 sont pris en charge.
                </p>
              </div>

              <USkeleton v-if="loading && !games.length && !candidates.length" class="h-20 w-full" />

              <div v-for="g in games" :key="g.id" class="rounded-xl border border-default bg-elevated/60 p-4">
                <div class="flex items-center gap-3">
                  <UIcon name="i-ri-gamepad-line" class="size-6 shrink-0 text-muted" />
                  <div class="min-w-0 flex-1">
                    <p class="truncate font-semibold text-highlighted">{{ g.title }}</p>
                    <p class="truncate text-xs text-muted">{{ g.root }}</p>
                  </div>
                  <UBadge v-if="g.ready" label="Prêt" color="success" variant="subtle" />
                  <UBadge v-else-if="preparing(g)" label="Préparation…" color="primary" variant="subtle" />
                  <UButton v-else-if="g.supported" label="Préparer" icon="i-ri-magic-line" size="sm" :loading="busy === g.id" @click="prepare(g)" />
                  <UBadge v-else label="Non pris en charge" color="warning" variant="subtle" />
                </div>
                <div v-if="preparing(g)" class="mt-3 space-y-1">
                  <UProgress :model-value="preparing(g)!.total ? (100 * preparing(g)!.done) / preparing(g)!.total : null" size="sm" />
                  <p class="truncate text-xs text-muted">{{ preparing(g)!.last || 'En attente…' }}</p>
                </div>
              </div>

              <template v-if="candidates.length">
                <p class="text-xs font-semibold uppercase tracking-wide text-muted">Trouvés dans Steam</p>
                <div v-for="g in candidates" :key="g.id" class="flex items-center gap-3 rounded-xl border border-default p-3">
                  <div class="min-w-0 flex-1">
                    <p class="truncate font-medium text-highlighted">{{ g.title }}</p>
                    <p class="truncate text-xs" :class="!g.supported && g.reason ? 'text-warning' : 'text-muted'">{{ !g.supported && g.reason ? g.reason : g.root }}</p>
                  </div>
                  <UButton label="Ajouter" icon="i-ri-add-line" size="sm" color="neutral" variant="soft" :disabled="!g.supported" :loading="busy === g.root" @click="add(g.root)" />
                </div>
              </template>

              <UButton label="Choisir un dossier de jeu" icon="i-ri-folder-add-line" color="neutral" variant="outline" :loading="busy === 'pick'" @click="add()" />
            </section>

            <!-- 3 -->
            <section v-else-if="step === 3" key="3" class="w-full space-y-5">
              <div class="space-y-2">
                <h2 class="text-3xl font-bold tracking-tight text-highlighted">Garry’s Mod et tes fichiers</h2>
              </div>
              <div class="flex items-start gap-3 rounded-xl border border-default bg-elevated/60 p-4">
                <UIcon :name="gmod?.installed ? 'i-ri-checkbox-circle-fill' : 'i-ri-information-line'" class="mt-0.5 size-6 shrink-0" :class="gmod?.installed ? 'text-success' : 'text-warning'" />
                <div>
                  <p class="font-semibold text-highlighted">{{ gmod?.installed ? 'Garry’s Mod est installé' : 'Garry’s Mod est introuvable' }}</p>
                  <p class="mt-1 text-sm text-muted">
                    {{ gmod?.installed ? 'Les addons générés peuvent être liés au jeu d’un clic (le bouton en forme de lien, en haut).' : 'Tu peux tout explorer et exporter sans lui. Indique son dossier dans Réglages pour lier les addons.' }}
                  </p>
                </div>
              </div>
              <div class="flex items-start gap-3 rounded-xl border border-default bg-elevated/60 p-4">
                <UIcon name="i-ri-folder-3-line" class="mt-0.5 size-6 shrink-0 text-primary" />
                <div class="min-w-0 flex-1">
                  <p class="font-semibold text-highlighted">Tout tient dans un seul dossier</p>
                  <p class="mt-1 break-all font-mono text-xs text-muted">{{ home?.home }}</p>
                  <p class="mt-1 text-sm text-muted">Tes exports d’un côté, ce que l’application gère de l’autre. Tu peux le déplacer plus tard dans Réglages, par exemple vers un disque plus grand : les exports de jeux se comptent en dizaines de Go.</p>
                </div>
                <UButton icon="i-ri-folder-open-line" color="neutral" variant="soft" size="sm" aria-label="Ouvrir le dossier" @click="openHome" />
              </div>
            </section>

            <!-- 4 -->
            <section v-else key="4" class="w-full space-y-6 text-center">
              <UIcon name="i-ri-rocket-2-line" class="onb-float mx-auto size-14 text-primary" />
              <h2 class="text-4xl font-extrabold tracking-tight text-highlighted">C’est prêt</h2>
              <p class="mx-auto max-w-lg text-toned">
                {{ readyGame ? `${readyGame.title} est prêt : ouvre les modèles, les textures ou les sons.` : 'Tu pourras ajouter ou préparer un jeu à tout moment depuis la page Jeux.' }}
                La présentation se revoit depuis Réglages.
              </p>
              <UButton label="Ouvrir omni" trailing-icon="i-ri-arrow-right-line" size="xl" @click="finish()" />
            </section>
          </div>
        </main>

        <footer class="flex items-center justify-between">
          <UButton v-if="step > 0" label="Retour" icon="i-ri-arrow-left-line" color="neutral" variant="ghost" @click="go(step - 1)" />
          <span v-else />
          <div class="flex items-center gap-2" role="tablist" aria-label="Étapes">
            <button
              v-for="(s, i) in STEPS"
              :key="s"
              type="button"
              class="h-2 rounded-full transition-all"
              :class="i === step ? 'w-6 bg-primary' : 'w-2 bg-accented hover:bg-muted'"
              :aria-label="s"
              :aria-selected="i === step"
              role="tab"
              @click="go(i)"
            />
          </div>
          <UButton v-if="step > 0 && step < last" label="Suivant" trailing-icon="i-ri-arrow-right-line" @click="go(step + 1)" />
          <span v-else />
        </footer>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.onb-title {
  background: linear-gradient(120deg, var(--ui-text-highlighted), var(--ui-primary) 55%, var(--ui-text-highlighted));
  background-size: 200% auto;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: onb-shine 6s linear infinite;
}
.onb-blob {
  position: fixed;
  border-radius: 9999px;
  filter: blur(90px);
  opacity: 0.28;
  pointer-events: none;
  background: var(--ui-primary);
}
.onb-blob-a {
  width: 34rem;
  height: 34rem;
  left: -10rem;
  top: -8rem;
  animation: onb-drift 18s ease-in-out infinite alternate;
}
.onb-blob-b {
  width: 28rem;
  height: 28rem;
  right: -8rem;
  bottom: -6rem;
  opacity: 0.18;
  animation: onb-drift 22s ease-in-out infinite alternate-reverse;
}
.onb-card {
  animation: onb-rise 0.5s both cubic-bezier(0.2, 0.8, 0.2, 1);
}
.onb-float {
  animation: onb-bob 3.2s ease-in-out infinite;
}
.onb-in-next {
  animation: onb-slide-next 0.35s both ease;
}
.onb-in-prev {
  animation: onb-slide-prev 0.35s both ease;
}
@keyframes onb-slide-next {
  from {
    opacity: 0;
    transform: translateX(28px);
  }
}
@keyframes onb-slide-prev {
  from {
    opacity: 0;
    transform: translateX(-28px);
  }
}
@keyframes onb-shine {
  to {
    background-position: 200% center;
  }
}
@keyframes onb-drift {
  to {
    transform: translate(6rem, 4rem) scale(1.15);
  }
}
@keyframes onb-rise {
  from {
    opacity: 0;
    transform: translateY(14px);
  }
}
@keyframes onb-bob {
  50% {
    transform: translateY(-8px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .onb-title,
  .onb-blob,
  .onb-card,
  .onb-in-next,
  .onb-in-prev,
  .onb-float {
    animation: none;
  }
}
</style>
