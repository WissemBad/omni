const STORAGE = 'omni:prop-selection'
let persisting = false

/** Props ticked for conversion, remembered per source (and across restarts: hundreds of ticks are not lost). */
export function useSelection() {
  const sid = useSourceId()
  const store = useState<Record<string, Record<string, string>>>('prop-selection', () => {
    if (!import.meta.client) return {}
    try {
      return JSON.parse(localStorage.getItem(STORAGE) ?? '{}')
    } catch {
      return {}
    }
  })
  if (import.meta.client && !persisting) {
    persisting = true
    watch(
      store,
      (v) => {
        try {
          localStorage.setItem(STORAGE, JSON.stringify(v))
        } catch {
          /* storage full or blocked */
        }
      },
      { deep: true },
    )
  }
  const current = computed(() => store.value[sid.value] ?? {})
  const keys = computed(() => Object.keys(current.value))
  const count = computed(() => keys.value.length)

  const set = (next: Record<string, string>) => {
    store.value = { ...store.value, [sid.value]: next }
  }
  const has = (key: string) => key in current.value
  function toggle(key: string, label: string) {
    const next = { ...current.value }
    if (key in next) delete next[key]
    else next[key] = label
    set(next)
  }
  function add(items: Record<string, string>) {
    set({ ...current.value, ...items })
  }
  const clear = () => set({})
  return { keys, count, has, toggle, add, clear, current }
}
