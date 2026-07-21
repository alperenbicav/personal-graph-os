import { useState } from 'react'
import type { EdgeType } from '../types'

export interface PendingConnection {
  sourceNodeId: string
  sourceTitle: string
  targetNodeId: string
  targetTitle: string
}

interface ConnectEdgeModalProps {
  pending: PendingConnection
  edgeTypes: EdgeType[]
  onConfirm: (edgeTypeId: string) => void
  onCancel: () => void
}

export function ConnectEdgeModal({ pending, edgeTypes, onConfirm, onCancel }: ConnectEdgeModalProps) {
  const [edgeTypeId, setEdgeTypeId] = useState(edgeTypes[0]?.id ?? '')
  const activeEdgeTypeId = edgeTypeId || edgeTypes[0]?.id || ''

  return (
    <div className="connect-modal-backdrop" role="dialog" aria-label="Connect two objects">
      <div className="connect-modal">
        <p className="connect-modal-summary">
          <strong>{pending.sourceTitle}</strong>
          {' → '}
          <strong>{pending.targetTitle}</strong>
        </p>

        <label className="field-label" htmlFor="connect-edge-type">
          Relationship type
        </label>
        <select
          id="connect-edge-type"
          className="status-select"
          value={activeEdgeTypeId}
          onChange={(event) => setEdgeTypeId(event.target.value)}
        >
          {edgeTypes.map((edgeType) => (
            <option key={edgeType.id} value={edgeType.id}>
              {edgeType.name}
            </option>
          ))}
        </select>

        <div className="connect-modal-actions">
          <button type="button" className="connect-modal-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="connect-modal-confirm"
            onClick={() => onConfirm(activeEdgeTypeId)}
            disabled={!activeEdgeTypeId}
          >
            Connect
          </button>
        </div>
      </div>
    </div>
  )
}
