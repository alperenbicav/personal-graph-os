import { type ReactNode } from 'react'
import { useModalDialog } from '../../lib/useModalDialog'

/** Right-hand slide-over panel for on-demand detail (versions, links, backlinks, ⋯ menus).
 * Replaces permanently-visible narrow side bars (EP-2026-013 ST-01). Escape and backdrop
 * click close it; focus is trapped by `useModalDialog`. */
export function SlideOver({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const dialogRef = useModalDialog<HTMLDivElement>(onClose)

  return (
    <div className="slideover-backdrop" onClick={onClose}>
      <div
        className="slideover"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={dialogRef}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="slideover-header">
          <h2 className="slideover-title">{title}</h2>
          <button type="button" className="slideover-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="slideover-body">{children}</div>
      </div>
    </div>
  )
}
