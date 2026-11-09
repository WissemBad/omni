<script setup lang="ts">
/** Everything about omni itself rather than a game: settings, updates, the welcome tour. */
const tour = useState('tour', () => false)
const update = useUpdate()
const checking = ref(false)

async function checkNow() {
  checking.value = true
  const info = await update.check(true)
  checking.value = false
  useToast().add(
    info?.error
      ? { title: 'Vérification impossible', description: info.error, color: 'error' }
      : info?.available
        ? { title: `omni ${info.latest} est disponible`, icon: 'i-ri-download-cloud-2-line' }
        : { title: `omni ${info?.current ?? ''} est à jour`, icon: 'i-ri-check-line' },
  )
}

const menu = computed(() => [
  [{ label: 'Réglages', icon: 'i-ri-settings-3-line', to: '/settings' }],
  [
    update.info.value?.available
      ? {
          label: `Installer omni ${update.info.value.latest}`,
          icon: 'i-ri-download-cloud-2-line',
          color: 'primary' as const,
          onSelect: () => update.install(),
        }
      : {
          label: 'Chercher une mise à jour',
          icon: 'i-ri-refresh-line',
          onSelect: checkNow,
        },
    {
      label: 'Revoir la présentation',
      icon: 'i-ri-slideshow-line',
      onSelect: () => (tour.value = true),
    },
  ],
])
</script>

<template>
  <UDropdownMenu :items="menu" :content="{ align: 'end', sideOffset: 10 }">
    <UChip :show="!!update.info.value?.available" color="primary" inset>
      <UButton
        icon="i-ri-settings-3-line"
        color="neutral"
        variant="ghost"
        class="size-11 justify-center rounded-full sm:size-9"
        aria-label="omni : réglages et mises à jour"
        :loading="checking"
      />
    </UChip>
  </UDropdownMenu>
</template>
