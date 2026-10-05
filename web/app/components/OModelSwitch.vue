<script setup lang="ts">
/** Props / Characters switch at the top of the Models workbench (only the kinds the source offers). */
const model = defineModel<'props' | 'characters'>({ required: true })
defineProps<{ props?: number; characters?: number; has: { props: boolean; characters: boolean } }>()
</script>

<template>
  <UFieldGroup v-if="has.props && has.characters" class="flex w-full">
    <UButton
      icon="i-ri-box-3-line"
      label="Props"
      size="sm"
      color="neutral"
      :variant="model === 'props' ? 'solid' : 'outline'"
      class="flex-1 justify-center"
      @click="model = 'props'"
    />
    <UButton
      icon="i-ri-user-3-line"
      label="Personnages"
      size="sm"
      color="neutral"
      :variant="model === 'characters' ? 'solid' : 'outline'"
      class="flex-1 justify-center"
      @click="model = 'characters'"
    />
  </UFieldGroup>
</template>
