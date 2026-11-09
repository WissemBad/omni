<script setup lang="ts">
/**
 * `/` → the overview of the first game ready to use. Without any game, the two ways to use omni: convert a game,
 * or only look at Garry's Mod models (the viewer needs no game).
 */
const { sources, load } = useSources()
await load(true).catch(() => [])
const first = sources.value.find((s) => s.ready !== false)
if (first) await navigateTo(`/${first.id}`, { replace: true })
else if (sources.value.length) await navigateTo('/games', { replace: true })
</script>

<template>
  <div class="flex h-full items-center justify-center overflow-y-auto px-4 pb-8 pt-[4.75rem] sm:pt-20">
    <div class="w-full max-w-3xl space-y-6">
      <div class="space-y-2 text-center">
        <h1 class="text-3xl font-bold tracking-tight text-highlighted">Que veux-tu faire ?</h1>
        <p class="text-toned">Tu pourras passer de l’un à l’autre à tout moment.</p>
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <NuxtLink to="/games" class="group">
          <UCard class="h-full transition group-hover:ring-2 group-hover:ring-primary/40" :ui="{ body: 'space-y-3 p-5 sm:p-5' }">
            <UIcon name="i-ri-gamepad-line" class="size-7 text-primary" />
            <h2 class="text-lg font-semibold text-highlighted">Convertir un jeu</h2>
            <p class="text-sm text-muted">Les modèles, textures et sons de ton jeu (007 First Light, HITMAN, Unreal Engine 5) deviennent un addon Garry’s Mod, des modèles glTF et des sons.</p>
          </UCard>
        </NuxtLink>
        <NuxtLink to="/view" class="group">
          <UCard class="h-full transition group-hover:ring-2 group-hover:ring-primary/40" :ui="{ body: 'space-y-3 p-5 sm:p-5' }">
            <UIcon name="i-ri-eye-line" class="size-7 text-primary" />
            <h2 class="text-lg font-semibold text-highlighted">Ouvrir des modèles Garry’s Mod</h2>
            <p class="text-sm text-muted">Un .mdl, une archive .gma du Workshop ou un addon installé : modèle, squelette, hitboxes, collision, animations et textures, sans rien convertir.</p>
          </UCard>
        </NuxtLink>
      </div>
    </div>
  </div>
</template>
