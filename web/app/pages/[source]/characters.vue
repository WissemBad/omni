<script setup lang="ts">
/** Former address of the characters workbench: Models › Characters (query kept, e.g. ?id=<family>). */
const route = useRoute()
await navigateTo({ path: `/${route.params.source}/models`, query: { ...route.query, tab: 'characters' } }, { replace: true })
</script>

<template><div /></template>
