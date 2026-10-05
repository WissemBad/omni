/** Props ticked for conversion, remembered per source while the app is open. */
export function useSelection() {
  const sid = useSourceId()
  const store = useState<Record<string, Record<string, string>>>('prop-selection', () => ({}))
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
