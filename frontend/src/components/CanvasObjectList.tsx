import { useRef, useState, type KeyboardEvent } from 'react'
import type { GraphNode, NodeType } from '../types'

interface CanvasObjectListProps {
  nodes: GraphNode[]
  nodeTypeById: Map<string, NodeType>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string | null) => void
}

/** The keyboard-operable equivalent of clicking a node card on the pointer-driven canvas
 * (ST-09 09.2): a synchronized listbox, not a hidden duplicate — it shares `selectedNodeId`/
 * `onSelectNode` with `GraphCanvas` so selecting here or on the canvas stays in lockstep.
 * Uses roving tabindex (WAI-ARIA APG listbox pattern) rather than native `<select>` since
 * each option needs to carry a node-kind label alongside the title. */
export function CanvasObjectList({
  nodes,
  nodeTypeById,
  selectedNodeId,
  onSelectNode,
}: CanvasObjectListProps) {
  const itemRefs = useRef(new Map<string, HTMLDivElement>())
  const [activeNodeId, setActiveNodeId] = useState<string | null>(selectedNodeId ?? nodes[0]?.id ?? null)

  if (nodes.length === 0) return null

  const rovingNodeId = activeNodeId && nodes.some((node) => node.id === activeNodeId)
    ? activeNodeId
    : nodes[0].id

  function focusNodeAt(index: number) {
    const wrapped = (index + nodes.length) % nodes.length
    const node = nodes[wrapped]
    setActiveNodeId(node.id)
    itemRefs.current.get(node.id)?.focus()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusNodeAt(index + 1)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      focusNodeAt(index - 1)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelectNode(nodes[index].id)
    }
  }

  return (
    <div className="canvas-object-list" role="listbox" aria-label="Canvas objects (keyboard)">
      {nodes.map((node, index) => {
        const nodeType = nodeTypeById.get(node.node_type_id)
        const isSelected = node.id === selectedNodeId
        return (
          <div
            key={node.id}
            ref={(element) => {
              if (element) itemRefs.current.set(node.id, element)
              else itemRefs.current.delete(node.id)
            }}
            role="option"
            aria-selected={isSelected}
            tabIndex={node.id === rovingNodeId ? 0 : -1}
            className={
              isSelected ? 'canvas-object-list-item canvas-object-list-item-selected' : 'canvas-object-list-item'
            }
            onClick={() => onSelectNode(node.id)}
            onFocus={() => setActiveNodeId(node.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {`${nodeType ? `${nodeType.name}: ` : ''}${node.title}`}
          </div>
        )
      })}
    </div>
  )
}
