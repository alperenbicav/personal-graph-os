import type { NodeType, ProjectionItem, StatusDefinition } from '../types'
import { NodeRow } from './NodeRow'

interface TableViewProps {
  rows: ProjectionItem[]
  nodeTypeById: Map<string, NodeType>
  statusById: Map<string, StatusDefinition>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

export function TableView({
  rows,
  nodeTypeById,
  statusById,
  selectedNodeId,
  onSelectNode,
}: TableViewProps) {
  if (rows.length === 0) {
    return <p className="view-empty">Nothing here yet.</p>
  }

  return (
    <div className="list-view" role="table" aria-label="Table view">
      {rows.map((row) => (
        <NodeRow
          key={row.node.id}
          node={row.node}
          resource={row.resource}
          nodeType={nodeTypeById.get(row.node.node_type_id)}
          status={row.node.status_id ? statusById.get(row.node.status_id) : undefined}
          isSelected={row.node.id === selectedNodeId}
          onSelect={onSelectNode}
        />
      ))}
    </div>
  )
}
