import { useState } from 'react'
import { useModalDialog } from '../lib/useModalDialog'

interface NewCanvasModalProps {
  onConfirm: (name: string) => void
  onCancel: () => void
}

/** Styled replacement for the former `window.prompt` canvas naming (ST-08): same one-field
 * contract as every other modal, with focus trap and Escape handling from `useModalDialog`. */
export function NewCanvasModal({ onConfirm, onCancel }: NewCanvasModalProps) {
  const [name, setName] = useState('')
  const dialogRef = useModalDialog<HTMLDivElement>(onCancel)

  const submit = () => {
    const trimmed = name.trim()
    if (trimmed) onConfirm(trimmed)
  }

  return (
    <div
      className="connect-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Name the new canvas"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="connect-modal">
        <label className="field-label" htmlFor="new-canvas-name">
          Canvas name
        </label>
        <input
          id="new-canvas-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submit()
          }}
        />
        <div className="connect-modal-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={!name.trim()}>
            Create canvas
          </button>
        </div>
      </div>
    </div>
  )
}
