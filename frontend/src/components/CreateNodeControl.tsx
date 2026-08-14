import { useState } from 'react'
import type { NodeType } from '../types'

interface CreateNodeControlProps {
  nodeTypes: NodeType[]
  isDisabled: boolean
  onCreate: (nodeTypeId: string, title: string) => void
}

/** The Graph tab's typed create-node action (ST-08 S8-F03): every node type is instantiable
 * from the owning tab now that the TopBar global quick-capture is gone, including custom
 * Schema-Editor types and generic graph-only nodes. */
export function CreateNodeControl({ nodeTypes, isDisabled, onCreate }: CreateNodeControlProps) {
  const [nodeTypeId, setNodeTypeId] = useState(nodeTypes[0]?.id ?? '')
  const [title, setTitle] = useState('')

  const activeNodeTypeId = nodeTypeId || nodeTypes[0]?.id || ''

  const submit = () => {
    const trimmed = title.trim()
    if (!trimmed || !activeNodeTypeId || isDisabled) return
    onCreate(activeNodeTypeId, trimmed)
    setTitle('')
  }

  return (
    <div className="create-node">
      <label className="sr-only" htmlFor="create-node-kind">
        Node type
      </label>
      <select
        id="create-node-kind"
        value={activeNodeTypeId}
        onChange={(event) => setNodeTypeId(event.target.value)}
        aria-label="Node type"
      >
        {nodeTypes.map((nodeType) => (
          <option key={nodeType.id} value={nodeType.id}>
            {nodeType.name}
          </option>
        ))}
      </select>
      <label className="sr-only" htmlFor="create-node-input">
        New node title
      </label>
      <input
        id="create-node-input"
        type="text"
        placeholder="New node title…"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit()
        }}
      />
      <button type="button" onClick={submit} disabled={isDisabled || !title.trim()}>
        Add node
      </button>
    </div>
  )
}
