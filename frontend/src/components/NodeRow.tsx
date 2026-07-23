import type { GraphNode, NodeType, Resource, StatusDefinition } from '../types'

interface NodeRowProps {
  node: GraphNode
  resource: Resource | null
  nodeType: NodeType | undefined
  status: StatusDefinition | undefined
  isSelected: boolean
  onSelect: (nodeId: string) => void
}

export function NodeRow({ node, resource, nodeType, status, isSelected, onSelect }: NodeRowProps) {
  return (
    <button
      type="button"
      className="node-row"
      aria-current={isSelected}
      onClick={() => onSelect(node.id)}
    >
      <span className="node-row-title">{node.title}</span>
      <span className="node-row-meta">
        {nodeType && <span className="node-row-type">{nodeType.name}</span>}
        {status && (
          <span className="node-row-status" style={{ color: status.color_hex }}>
            {status.name}
          </span>
        )}
        {resource && <span className="node-row-lifecycle">{resource.lifecycle_status}</span>}
      </span>
    </button>
  )
}
