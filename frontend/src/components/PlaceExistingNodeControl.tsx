import { useState } from 'react'
import type { GraphNode } from '../types'

interface PlaceExistingNodeControlProps {
  /** Nodes that exist in the workspace but have no placement on the active canvas yet. */
  unplacedNodes: GraphNode[]
  onPlace: (nodeId: string) => void
}

export function PlaceExistingNodeControl({ unplacedNodes, onPlace }: PlaceExistingNodeControlProps) {
  const [nodeId, setNodeId] = useState('')

  if (unplacedNodes.length === 0) return null

  const activeNodeId = nodeId || unplacedNodes[0].id

  return (
    <div className="stage-pill place-existing">
      <label className="sr-only" htmlFor="place-existing-select">
        Existing object to place on this canvas
      </label>
      <select id="place-existing-select" value={activeNodeId} onChange={(event) => setNodeId(event.target.value)}>
        {unplacedNodes.map((node) => (
          <option key={node.id} value={node.id}>
            {node.title}
          </option>
        ))}
      </select>
      <button type="button" onClick={() => onPlace(activeNodeId)}>
        Place here
      </button>
    </div>
  )
}
