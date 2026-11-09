<script setup lang="ts">
/** The viewer without a game: the models, folders and .gma archives the user opens (`omni view <path>`). */
definePageMeta({ key: 'view' })
</script>

<template>
  <OModelViewer />
</template>
