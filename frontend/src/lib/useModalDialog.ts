import { useEffect, useRef } from 'react'

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Wires WAI-ARIA modal-dialog behavior onto the element the returned ref is attached to:
 * moves focus inside on open, traps Tab/Shift+Tab within it, closes on Escape, and restores
 * focus to whatever was focused before the dialog opened. The element itself needs
 * `role="dialog"`, `aria-modal="true"`, and `tabIndex={-1}` (as a focus target of last
 * resort when the dialog has no focusable content) — this hook only owns behavior, not
 * markup, so callers stay free to shape their own dialog content. */
export function useModalDialog<T extends HTMLElement>(onClose: () => void) {
  const containerRef = useRef<T>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const previouslyFocused = document.activeElement as HTMLElement | null
    const focusable = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
    ;(focusable[0] ?? container).focus()

    function handleKeyDown(event: KeyboardEvent) {
      if (!container) return
      if (event.key === 'Escape') {
        event.preventDefault()
        // Stops this Escape from also reaching App's own window-level Escape handler
        // (deselect-node), which would otherwise fire right behind the dialog closing.
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusableElements = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (focusableElements.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusableElements[0]
      const last = focusableElements[focusableElements.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    // Capture phase: must run before App's window-level (bubble-phase) key handling.
    document.addEventListener('keydown', handleKeyDown, true)
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      previouslyFocused?.focus()
    }
  }, [])

  return containerRef
}
