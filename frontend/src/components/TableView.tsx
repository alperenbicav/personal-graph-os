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
    return (
      <p className="view-empty" role="status">
        Nothing here yet.
      </p>
    )
  }

  return (
    // Not `role="table"`: these are plain selectable rows (no exposed rows/cells/headers),
    // and ARIA's `table` role requires `row`/`cell` children — `group` matches what's
    // actually here without a false table-navigation promise to screen reader users.
    <div className="list-view" role="group" aria-label="Table view">
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
