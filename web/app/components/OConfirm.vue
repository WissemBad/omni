<script setup lang="ts">
/** The dialog behind `useConfirm()`: mounted once in app.vue. */
const pending = useState<{
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  resolve: (ok: boolean) => void
} | null>('confirm', () => null)

const open = computed({
  get: () => !!pending.value,
  set: (v) => {
    if (!v) answer(false)
  },
})

function answer(ok: boolean) {
  const p = pending.value
  pending.value = null
  p?.resolve(ok)
}
</script>

<template>
  <UModal v-model:open="open" :title="pending?.title" :description="pending?.description" :ui="{ content: 'max-w-md' }">
    <template #footer>
      <div class="flex w-full justify-end gap-2">
        <UButton :label="pending?.cancelLabel ?? 'Annuler'" color="neutral" variant="ghost" @click="answer(false)" />
        <UButton :label="pending?.confirmLabel ?? 'Confirmer'" :color="pending?.destructive ? 'error' : 'primary'" autofocus @click="answer(true)" />
      </div>
    </template>
  </UModal>
</template>
