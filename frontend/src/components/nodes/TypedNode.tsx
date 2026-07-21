import { Handle, Position, type NodeProps } from '@xyflow/react'

export interface TypedNodeData extends Record<string, unknown> {
  title: string
  kindLabel: string
  kindSlug: string
  statusLabel: string | null
}

export function TypedNode({ data, selected }: NodeProps) {
  const { title, kindLabel, kindSlug, statusLabel } = data as TypedNodeData

  return (
    <div className="pg-node" data-kind={kindSlug} data-selected={selected}>
      <Handle type="target" position={Position.Top} className="pg-handle" />
      <div className="node-kind">{kindLabel}</div>
      <span className="node-title">{title}</span>
      {statusLabel && (
        <span className="status-chip">
          <span className="status-dot" />
          {statusLabel}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} className="pg-handle" />
    </div>
  )
}
