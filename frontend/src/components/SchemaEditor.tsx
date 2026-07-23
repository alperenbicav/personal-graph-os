import { useState } from 'react'
import * as api from '../api/client'
import { messageFor } from '../lib/errors'
import { useModalDialog } from '../lib/useModalDialog'
import type { EdgeType, FieldDefinition, FieldType, NodeType, StatusDefinition, Workspace } from '../types'

interface SchemaEditorProps {
  workspace: Workspace
  onWorkspaceChange: (workspace: Workspace) => void
  onError: (message: string) => void
  onClose: () => void
}

const FIELD_TYPES: FieldType[] = [
  'text',
  'number',
  'boolean',
  'date',
  'select',
  'object_reference',
  'url',
  'file_path',
]

function parseSelectOptions(raw: string): string[] {
  return raw
    .split(',')
    .map((option) => option.trim())
    .filter(Boolean)
}

interface NodeTypeIdentityFormProps {
  nodeType: NodeType
  onSave: (patch: { name: string; icon: string; color_hex: string }) => Promise<boolean>
}

function NodeTypeIdentityForm({ nodeType, onSave }: NodeTypeIdentityFormProps) {
  const [name, setName] = useState(nodeType.name)
  const [icon, setIcon] = useState(nodeType.icon)
  const [colorHex, setColorHex] = useState(nodeType.color_hex)
  const isDirty = name !== nodeType.name || icon !== nodeType.icon || colorHex !== nodeType.color_hex

  return (
    <div className="schema-create-row schema-create-row-wrap">
      <input
        type="text"
        aria-label={`${nodeType.name} name`}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        type="text"
        aria-label={`${nodeType.name} icon`}
        placeholder="icon"
        value={icon}
        onChange={(event) => setIcon(event.target.value)}
      />
      <input
        type="text"
        aria-label={`${nodeType.name} color`}
        placeholder="#rrggbb"
        value={colorHex}
        onChange={(event) => setColorHex(event.target.value)}
      />
      <button
        type="button"
        disabled={!isDirty || !name.trim()}
        onClick={() => onSave({ name: name.trim(), icon: icon.trim(), color_hex: colorHex.trim() })}
      >
        Save
      </button>
    </div>
  )
}

interface FieldRowProps {
  field: FieldDefinition
  onUpdate: (patch: api.UpdateFieldDefinitionPatch) => Promise<boolean>
  onRemove: () => void
}

function FieldRow({ field, onUpdate, onRemove }: FieldRowProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [name, setName] = useState(field.name)
  const [fieldType, setFieldType] = useState<FieldType>(field.field_type)
  const [isRequired, setIsRequired] = useState(field.is_required)
  const [selectOptions, setSelectOptions] = useState(field.select_options.join(', '))
  const [description, setDescription] = useState(field.description ?? '')

  if (!isEditing) {
    return (
      <div className="schema-row">
        <span>
          {field.name} <em>({field.field_type})</em>
          {field.is_required && (
            <strong title="A node can omit this field forever, but can't clear it back to empty once it has a value">
              {' '}
              required once set
            </strong>
          )}
        </span>
        <span className="schema-row-actions">
          <button type="button" onClick={() => setIsEditing(true)}>
            Edit
          </button>
          <button type="button" onClick={onRemove}>
            Remove
          </button>
        </span>
      </div>
    )
  }

  async function handleSave() {
    const ok = await onUpdate({
      name: name.trim(),
      field_type: fieldType,
      is_required: isRequired,
      select_options: fieldType === 'select' ? parseSelectOptions(selectOptions) : [],
      description: description.trim() || null,
      clear_description: description.trim() === '',
    })
    if (ok) setIsEditing(false)
  }

  return (
    <div className="schema-create-row schema-create-row-wrap">
      <input
        type="text"
        aria-label={`${field.name} name`}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <select
        aria-label={`${field.name} type`}
        value={fieldType}
        onChange={(event) => setFieldType(event.target.value as FieldType)}
      >
        {FIELD_TYPES.map((type) => (
          <option key={type} value={type}>
            {type}
          </option>
        ))}
      </select>
      <label
        className="schema-inline-checkbox"
        title="A node can omit this field forever, but can't clear it back to empty once it has a value"
      >
        <input
          type="checkbox"
          checked={isRequired}
          onChange={(event) => setIsRequired(event.target.checked)}
        />
        required once set
      </label>
      {fieldType === 'select' && (
        <input
          type="text"
          aria-label={`${field.name} options`}
          placeholder="Options, comma-separated"
          value={selectOptions}
          onChange={(event) => setSelectOptions(event.target.value)}
        />
      )}
      <input
        type="text"
        aria-label={`${field.name} description`}
        placeholder="Description (optional)"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />
      <button type="button" onClick={handleSave} disabled={!name.trim()}>
        Save
      </button>
      <button type="button" onClick={() => setIsEditing(false)}>
        Cancel
      </button>
    </div>
  )
}

interface StatusRowProps {
  status: StatusDefinition
  onUpdate: (patch: api.UpdateStatusDefinitionPatch) => Promise<boolean>
  onRemove: () => void
}

function StatusRow({ status, onUpdate, onRemove }: StatusRowProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [name, setName] = useState(status.name)
  const [colorHex, setColorHex] = useState(status.color_hex)
  const [isTerminal, setIsTerminal] = useState(status.is_terminal)
  const [sortOrder, setSortOrder] = useState(String(status.sort_order))

  if (!isEditing) {
    return (
      <div className="schema-row">
        <span>
          {status.name}
          {status.is_terminal && <em> (terminal)</em>}
        </span>
        <span className="schema-row-actions">
          <button type="button" onClick={() => setIsEditing(true)}>
            Edit
          </button>
          <button type="button" onClick={onRemove}>
            Remove
          </button>
        </span>
      </div>
    )
  }

  async function handleSave() {
    const parsedSortOrder = Number(sortOrder)
    const ok = await onUpdate({
      name: name.trim(),
      color_hex: colorHex.trim(),
      is_terminal: isTerminal,
      sort_order: Number.isNaN(parsedSortOrder) ? status.sort_order : parsedSortOrder,
    })
    if (ok) setIsEditing(false)
  }

  return (
    <div className="schema-create-row schema-create-row-wrap">
      <input
        type="text"
        aria-label={`${status.name} name`}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        type="text"
        aria-label={`${status.name} color`}
        placeholder="#rrggbb"
        value={colorHex}
        onChange={(event) => setColorHex(event.target.value)}
      />
      <label className="schema-inline-checkbox">
        <input
          type="checkbox"
          checked={isTerminal}
          onChange={(event) => setIsTerminal(event.target.checked)}
        />
        terminal
      </label>
      <input
        type="text"
        inputMode="numeric"
        aria-label={`${status.name} sort order`}
        value={sortOrder}
        onChange={(event) => setSortOrder(event.target.value)}
      />
      <button type="button" onClick={handleSave} disabled={!name.trim()}>
        Save
      </button>
      <button type="button" onClick={() => setIsEditing(false)}>
        Cancel
      </button>
    </div>
  )
}

interface EdgeTypeRowProps {
  edgeType: EdgeType
  onUpdate: (patch: api.UpdateEdgeTypePatch) => Promise<boolean>
  onRemove: () => void
}

function EdgeTypeRow({ edgeType, onUpdate, onRemove }: EdgeTypeRowProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [name, setName] = useState(edgeType.name)
  const [inverseName, setInverseName] = useState(edgeType.inverse_name ?? '')
  const [colorHex, setColorHex] = useState(edgeType.color_hex)

  if (!isEditing) {
    return (
      <div className="schema-row">
        <span>
          {edgeType.name}
          {edgeType.inverse_name && <em> / {edgeType.inverse_name}</em>}
        </span>
        <span className="schema-row-actions">
          <button type="button" onClick={() => setIsEditing(true)}>
            Edit
          </button>
          <button type="button" onClick={onRemove}>
            Remove
          </button>
        </span>
      </div>
    )
  }

  async function handleSave() {
    const trimmedInverse = inverseName.trim()
    const ok = await onUpdate({
      name: name.trim(),
      color_hex: colorHex.trim(),
      inverse_name: trimmedInverse || null,
      clear_inverse_name: trimmedInverse === '',
    })
    if (ok) setIsEditing(false)
  }

  return (
    <div className="schema-create-row schema-create-row-wrap">
      <input
        type="text"
        aria-label={`${edgeType.name} name`}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        type="text"
        aria-label={`${edgeType.name} inverse name`}
        placeholder="Inverse name (optional)"
        value={inverseName}
        onChange={(event) => setInverseName(event.target.value)}
      />
      <input
        type="text"
        aria-label={`${edgeType.name} color`}
        placeholder="#rrggbb"
        value={colorHex}
        onChange={(event) => setColorHex(event.target.value)}
      />
      <button type="button" onClick={handleSave} disabled={!name.trim()}>
        Save
      </button>
      <button type="button" onClick={() => setIsEditing(false)}>
        Cancel
      </button>
    </div>
  )
}

export function SchemaEditor({ workspace, onWorkspaceChange, onError, onClose }: SchemaEditorProps) {
  const [tab, setTab] = useState<'node-types' | 'edge-types'>('node-types')
  const [expandedNodeTypeId, setExpandedNodeTypeId] = useState<string | null>(null)
  const [newNodeTypeName, setNewNodeTypeName] = useState('')
  const [newEdgeTypeName, setNewEdgeTypeName] = useState('')
  const [newEdgeTypeInverseName, setNewEdgeTypeInverseName] = useState('')
  const [newFieldName, setNewFieldName] = useState('')
  const [newFieldType, setNewFieldType] = useState<FieldType>('text')
  const [newFieldRequired, setNewFieldRequired] = useState(false)
  const [newFieldSelectOptions, setNewFieldSelectOptions] = useState('')
  const [newStatusName, setNewStatusName] = useState('')
  const dialogRef = useModalDialog<HTMLDivElement>(onClose)

  async function refresh() {
    onWorkspaceChange(await api.getWorkspace())
  }

  /** Returns whether `action` actually succeeded. Callers must only reset/close their own UI
   * state (clearing a create form, exiting a row's edit mode) when this resolves `true` — a
   * rejected save must leave the user's draft visible and the row still in edit mode. */
  async function guarded(action: () => Promise<unknown>): Promise<boolean> {
    try {
      await action()
      await refresh()
      return true
    } catch (error) {
      onError(messageFor(error))
      return false
    }
  }

  function handleCreateNodeType() {
    const name = newNodeTypeName.trim()
    if (!name) return
    guarded(() => api.createNodeType(workspace.id, name)).then((ok) => {
      if (ok) setNewNodeTypeName('')
    })
  }

  function handleUpdateNodeType(nodeType: NodeType, patch: api.UpdateNodeTypePatch) {
    return guarded(() => api.updateNodeType(workspace.id, nodeType.id, patch))
  }

  function handleAddField(nodeType: NodeType) {
    const name = newFieldName.trim()
    if (!name) return
    guarded(() =>
      api.addFieldDefinition(workspace.id, nodeType.id, {
        name,
        field_type: newFieldType,
        is_required: newFieldRequired,
        select_options: newFieldType === 'select' ? parseSelectOptions(newFieldSelectOptions) : [],
      }),
    ).then((ok) => {
      if (!ok) return
      setNewFieldName('')
      setNewFieldRequired(false)
      setNewFieldSelectOptions('')
    })
  }

  function handleUpdateField(
    nodeType: NodeType,
    fieldDefinitionId: string,
    patch: api.UpdateFieldDefinitionPatch,
  ) {
    return guarded(() => api.updateFieldDefinition(workspace.id, nodeType.id, fieldDefinitionId, patch))
  }

  function handleRemoveField(nodeType: NodeType, fieldDefinitionId: string) {
    guarded(() => api.removeFieldDefinition(workspace.id, nodeType.id, fieldDefinitionId))
  }

  function handleAddStatus(nodeType: NodeType) {
    const name = newStatusName.trim()
    if (!name) return
    guarded(() => api.addStatusDefinition(workspace.id, nodeType.id, { name })).then((ok) => {
      if (ok) setNewStatusName('')
    })
  }

  function handleUpdateStatus(
    nodeType: NodeType,
    statusDefinitionId: string,
    patch: api.UpdateStatusDefinitionPatch,
  ) {
    return guarded(() => api.updateStatusDefinition(workspace.id, nodeType.id, statusDefinitionId, patch))
  }

  function handleRemoveStatus(nodeType: NodeType, statusDefinitionId: string) {
    guarded(() => api.removeStatusDefinition(workspace.id, nodeType.id, statusDefinitionId))
  }

  function handleCreateEdgeType() {
    const name = newEdgeTypeName.trim()
    if (!name) return
    guarded(() =>
      api.createEdgeType(workspace.id, name, newEdgeTypeInverseName.trim() || undefined),
    ).then((ok) => {
      if (!ok) return
      setNewEdgeTypeName('')
      setNewEdgeTypeInverseName('')
    })
  }

  function handleUpdateEdgeType(edgeType: EdgeType, patch: api.UpdateEdgeTypePatch) {
    return guarded(() => api.updateEdgeType(workspace.id, edgeType.id, patch))
  }

  function handleRemoveEdgeType(edgeTypeId: string) {
    guarded(() => api.removeEdgeType(workspace.id, edgeTypeId))
  }

  return (
    <div
      className="schema-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Edit schema"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="schema-modal">
        <div className="schema-modal-header">
          <h2>Schema</h2>
          <button type="button" className="schema-modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="schema-modal-tabs">
          <button
            type="button"
            className={tab === 'node-types' ? 'schema-tab schema-tab-active' : 'schema-tab'}
            onClick={() => setTab('node-types')}
          >
            Node types
          </button>
          <button
            type="button"
            className={tab === 'edge-types' ? 'schema-tab schema-tab-active' : 'schema-tab'}
            onClick={() => setTab('edge-types')}
          >
            Edge types
          </button>
        </div>

        {tab === 'node-types' && (
          <div className="schema-modal-body">
            <div className="schema-create-row">
              <input
                type="text"
                placeholder="New node type name"
                value={newNodeTypeName}
                onChange={(event) => setNewNodeTypeName(event.target.value)}
              />
              <button type="button" onClick={handleCreateNodeType} disabled={!newNodeTypeName.trim()}>
                Create
              </button>
            </div>

            {workspace.node_types.map((nodeType) => {
              const isExpanded = expandedNodeTypeId === nodeType.id
              return (
                <div className="schema-item" key={nodeType.id}>
                  <button
                    type="button"
                    className="schema-item-header"
                    onClick={() => setExpandedNodeTypeId(isExpanded ? null : nodeType.id)}
                  >
                    <span
                      className="schema-swatch"
                      style={{ background: nodeType.color_hex }}
                      aria-hidden="true"
                    />
                    <span className="schema-item-name">{nodeType.name}</span>
                    <span className="schema-item-meta">
                      {nodeType.field_definitions.length} fields ·{' '}
                      {nodeType.status_definitions.length} statuses
                    </span>
                  </button>

                  {isExpanded && (
                    <div className="schema-item-detail">
                      <h4>Node type</h4>
                      <NodeTypeIdentityForm
                        nodeType={nodeType}
                        onSave={(patch) => handleUpdateNodeType(nodeType, patch)}
                      />

                      <h4>Fields</h4>
                      {nodeType.field_definitions.map((field) => (
                        <FieldRow
                          key={field.id}
                          field={field}
                          onUpdate={(patch) => handleUpdateField(nodeType, field.id, patch)}
                          onRemove={() => handleRemoveField(nodeType, field.id)}
                        />
                      ))}
                      <div className="schema-create-row schema-create-row-wrap">
                        <input
                          type="text"
                          placeholder="Field name"
                          value={newFieldName}
                          onChange={(event) => setNewFieldName(event.target.value)}
                        />
                        <select
                          value={newFieldType}
                          onChange={(event) => setNewFieldType(event.target.value as FieldType)}
                        >
                          {FIELD_TYPES.map((fieldType) => (
                            <option key={fieldType} value={fieldType}>
                              {fieldType}
                            </option>
                          ))}
                        </select>
                        <label
                          className="schema-inline-checkbox"
                          title="A node can omit this field forever, but can't clear it back to empty once it has a value"
                        >
                          <input
                            type="checkbox"
                            checked={newFieldRequired}
                            onChange={(event) => setNewFieldRequired(event.target.checked)}
                          />
                          required once set
                        </label>
                        {newFieldType === 'select' && (
                          <input
                            type="text"
                            placeholder="Options, comma-separated"
                            value={newFieldSelectOptions}
                            onChange={(event) => setNewFieldSelectOptions(event.target.value)}
                          />
                        )}
                        <button
                          type="button"
                          onClick={() => handleAddField(nodeType)}
                          disabled={!newFieldName.trim()}
                        >
                          Add field
                        </button>
                      </div>

                      <h4>Statuses</h4>
                      {nodeType.status_definitions.map((status) => (
                        <StatusRow
                          key={status.id}
                          status={status}
                          onUpdate={(patch) => handleUpdateStatus(nodeType, status.id, patch)}
                          onRemove={() => handleRemoveStatus(nodeType, status.id)}
                        />
                      ))}
                      <div className="schema-create-row">
                        <input
                          type="text"
                          placeholder="Status name"
                          value={newStatusName}
                          onChange={(event) => setNewStatusName(event.target.value)}
                        />
                        <button
                          type="button"
                          onClick={() => handleAddStatus(nodeType)}
                          disabled={!newStatusName.trim()}
                        >
                          Add status
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {tab === 'edge-types' && (
          <div className="schema-modal-body">
            <div className="schema-create-row schema-create-row-wrap">
              <input
                type="text"
                placeholder="New edge type name"
                value={newEdgeTypeName}
                onChange={(event) => setNewEdgeTypeName(event.target.value)}
              />
              <input
                type="text"
                placeholder="Inverse name (optional)"
                value={newEdgeTypeInverseName}
                onChange={(event) => setNewEdgeTypeInverseName(event.target.value)}
              />
              <button type="button" onClick={handleCreateEdgeType} disabled={!newEdgeTypeName.trim()}>
                Create
              </button>
            </div>

            {workspace.edge_types.map((edgeType) => (
              <EdgeTypeRow
                key={edgeType.id}
                edgeType={edgeType}
                onUpdate={(patch) => handleUpdateEdgeType(edgeType, patch)}
                onRemove={() => handleRemoveEdgeType(edgeType.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
