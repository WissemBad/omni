export interface ConfirmOptions {
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  /** Red button: the action deletes or interrupts something. */
  destructive?: boolean
}

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

/**
 * Branded replacement of `window.confirm` (which WebView2 shows as "127.0.0.1:8770 says…", unstyled and in English).
 * `if (!(await confirm({ title: '…', destructive: true }))) return`. The dialog itself is `<OConfirm />` in app.vue.
 */
export function useConfirm() {
  const pending = useState<Pending | null>('confirm', () => null)
  return (opts: ConfirmOptions) =>
    new Promise<boolean>((resolve) => {
      pending.value?.resolve(false)
      pending.value = { ...opts, resolve }
    })
}
