<script setup lang="ts">
/** `/` → the home page of the first source. */
const { sources } = useSources()
const first = sources.value[0]
if (first) await navigateTo(`/${first.id}`, { replace: true })
</script>

<template>
  <div class="flex h-full items-center justify-center p-6">
    <UEmpty icon="i-ri-gamepad-line" title="Aucune source disponible" description="L’API ne déclare aucun jeu." />
  </div>
</template>
