<script setup lang="ts">
/** `/` → the home page of the first game ready to use, else the game library (add a game folder there). */
const { sources, load } = useSources()
await load(true).catch(() => [])
const first = sources.value.find((s) => s.ready !== false)
await navigateTo(first ? `/${first.id}` : '/games', { replace: true })
</script>

<template>
  <div class="flex h-full items-center justify-center p-6">
    <UEmpty icon="i-ri-gamepad-line" title="Aucun jeu prêt" description="Ajoute le dossier d’un jeu dans la bibliothèque." />
  </div>
</template>
