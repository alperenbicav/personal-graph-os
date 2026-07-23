import type { NodeType, ProjectionItem, StatusDefinition } from '../types'
import { NodeRow } from './NodeRow'

interface KanbanViewProps {
  columns: Record<string, ProjectionItem[]>
  nodeTypeById: Map<string, NodeType>
  statusById: Map<string, StatusDefinition>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

export function KanbanView({
  columns,
  nodeTypeById,
  statusById,
  selectedNodeId,
  onSelectNode,
}: KanbanViewProps) {
  const columnKeys = Object.keys(columns).sort()

  if (columnKeys.length === 0) {
    return (
      <p className="view-empty" role="status">
        Nothing here yet.
      </p>
    )
  }

  return (
    <div className="kanban-view" aria-label="Kanban view">
      {columnKeys.map((key) => (
        <div className="kanban-column" key={key}>
          <div className="kanban-column-header">{key || 'Unassigned'}</div>
          <div className="kanban-column-body">
            {columns[key].map((row) => (
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
        </div>
      ))}
    </div>
  )
}
