/** Until the game's resources and names are set up, every page leads to the installation. */
export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === '/setup' || to.path === '/games') return
  const { status, load } = useSetup()
  if (!status.value) await load()
  if (status.value && !status.value.ready) return navigateTo('/setup')
})
