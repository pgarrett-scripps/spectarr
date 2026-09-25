import { useEffect } from 'react'

export function useDialogKeyboard() {
  useEffect(() => {
    let dialog: HTMLElement | null = null
    let previous: HTMLElement | null = document.activeElement as HTMLElement
    const controls = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]'))
      .filter(element => !element.matches(':disabled, [tabindex="-1"]') && element.getClientRects().length > 0)
    const focus = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement && !event.target.closest('[role="dialog"]')) previous = event.target
    }
    const observe = () => {
      const next = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')).at(-1) ?? null
      if (next === dialog) return
      const closed = dialog
      dialog = next
      if (next && !next.contains(document.activeElement)) (controls(next)[0] ?? next).focus()
      else if (closed && !next && previous?.isConnected) previous.focus()
    }
    const key = (event: KeyboardEvent) => {
      if (!dialog) return
      if (event.key === 'Escape') {
        const close = dialog.querySelector<HTMLButtonElement>('button[aria-label="Close"]')
        if (close && !close.disabled) {
          event.preventDefault()
          close.click()
        }
      }
      if (event.key !== 'Tab') return
      const items = controls(dialog)
      const first = items[0]
      const last = items.at(-1)
      if (!first) { event.preventDefault()
        return
      }
      if (!dialog.contains(document.activeElement) || event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const observer = new MutationObserver(observe)
    observer.observe(document.body, { childList: true, subtree: true })
    document.addEventListener('focusin', focus)
    document.addEventListener('keydown', key)
    observe()
    return () => {
      observer.disconnect()
      document.removeEventListener('focusin', focus)
      document.removeEventListener('keydown', key)
    }
  }, [])
}
