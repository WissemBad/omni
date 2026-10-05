<script setup lang="ts">
/** Former address of the props workbench: Models › Props (query kept, e.g. ?k=<key>). */
const route = useRoute()
await navigateTo(
  { path: `/${route.params.source}/models`, query: { ...route.query, tab: 'props' } },
  { replace: true },
)
</script>

<template><div /></template>
