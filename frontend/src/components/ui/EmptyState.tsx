import type { ReactNode } from 'react'

/** Friendly empty state with an optional call-to-action. Replaces bare "Nothing here."
 * sentences across views (EP-2026-013 ST-01). */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <p className="empty-state-title">{title}</p>
      {hint && <p className="empty-state-hint">{hint}</p>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  )
}
