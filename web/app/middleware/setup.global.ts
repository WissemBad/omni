/**
 * Each game must be ready before its pages open: 007 First Light through its first-run installation, the games of
 * the library once omni has prepared them (Jeux page).
 */
export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === '/setup' || to.path === '/games' || to.path === '/') return
  const sid = String(to.params.source ?? '')
  if (!sid) return
  if (sid === '007fl') {
    const { status, load } = useSetup()
    if (!status.value) await load()
    if (status.value && !status.value.ready) return navigateTo('/setup')
    return
  }
  const { sources, load } = useSources()
  await load().catch(() => [])
  const s = sources.value.find((x) => x.id === sid)
  if (!s || s.ready === false) {
    await load(true).catch(() => [])
    const again = sources.value.find((x) => x.id === sid)
    if (!again || again.ready === false) return navigateTo(`/games?game=${encodeURIComponent(sid)}`)
  }
})
