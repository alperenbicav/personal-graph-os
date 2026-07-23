import type { NodeType, ProjectionItem, StatusDefinition } from '../types'
import { NodeRow } from './NodeRow'

interface TimelineViewProps {
  rows: ProjectionItem[]
  nodeTypeById: Map<string, NodeType>
  statusById: Map<string, StatusDefinition>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

export function TimelineView({
  rows,
  nodeTypeById,
  statusById,
  selectedNodeId,
  onSelectNode,
}: TimelineViewProps) {
  if (rows.length === 0) {
    return (
      <p className="view-empty" role="status">
        Nothing here yet.
      </p>
    )
  }

  return (
    <div className="list-view" aria-label="Timeline view">
      {rows.map((row) => (
        <div className="timeline-row" key={row.node.id}>
          <span className="timeline-date">
            {new Date(row.resource?.review_at ?? row.node.created_at).toLocaleDateString()}
          </span>
          <NodeRow
            node={row.node}
            resource={row.resource}
            nodeType={nodeTypeById.get(row.node.node_type_id)}
            status={row.node.status_id ? statusById.get(row.node.status_id) : undefined}
            isSelected={row.node.id === selectedNodeId}
            onSelect={onSelectNode}
          />
        </div>
      ))}
    </div>
  )
}
