/** A ref remembered in localStorage (conversion options, panel layout). Safe when storage is unavailable. */
export function usePersisted<T>(key: string, fallback: T) {
  const state = ref<T>(structuredClone(fallback)) as Ref<T>
  if (import.meta.client) {
    try {
      const raw = localStorage.getItem(`omni:${key}`)
      if (raw) state.value = { ...(typeof fallback === 'object' ? fallback : {}), ...JSON.parse(raw) } as T
    } catch {
      /* private mode, corrupted value: keep the default */
    }
    watch(
      state,
      (v) => {
        try {
          localStorage.setItem(`omni:${key}`, JSON.stringify(v))
        } catch {
          /* storage full or blocked */
        }
      },
      { deep: true },
    )
  }
  return state
}
