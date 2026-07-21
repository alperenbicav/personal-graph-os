import { useState } from 'react'
import type { FieldDefinition, GraphNode, NodeType, StatusDefinition } from '../types'

export interface RelationRow {
  edgeTypeName: string
  otherNodeTitle: string
}

interface InspectorProps {
  node: GraphNode | null
  nodeType: NodeType | undefined
  relations: RelationRow[]
  onChangeStatus: (statusId: string) => void
  onChangeField: (fieldDefinitionId: string, value: unknown) => Promise<boolean>
  onArchive: () => void
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === ''
}

function isAbsoluteHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

interface FieldControlProps {
  field: FieldDefinition
  value: unknown
  onCommit: (value: unknown) => Promise<boolean>
}

function fieldControlId(fieldDefinitionId: string): string {
  return `field-control-${fieldDefinitionId}`
}

/** One schema-driven control per `FieldDefinition.field_type`, with client-side feedback
 * that mirrors `FieldDefinition.validate_value()` so a bad value is flagged before the
 * request round-trips; the server's own rejection still applies as the source of truth.
 * `onCommit` resolves to whether the save actually succeeded — a draft-backed control
 * (text/number/date/url) reverts to the last canonical `value` on a rejected save, so it
 * never keeps displaying a value that was never actually persisted. */
function FieldControl({ field, value, onCommit }: FieldControlProps) {
  const canonicalText = () => (isEmpty(value) ? '' : String(value))
  const [draft, setDraft] = useState(canonicalText)
  const [error, setError] = useState<string | null>(null)

  async function commitOrFlagEmpty(next: unknown, empty: boolean) {
    if (empty) {
      if (field.is_required) {
        setError("Can't be cleared to empty")
        return
      }
      setError(null)
      if (!(await onCommit(null))) setDraft(canonicalText())
      return
    }
    setError(null)
    if (!(await onCommit(next))) setDraft(canonicalText())
  }

  if (field.field_type === 'boolean') {
    return (
      <label className="field-control-checkbox">
        <input
          id={fieldControlId(field.id)}
          type="checkbox"
          checked={value === true}
          onChange={(event) => onCommit(event.target.checked)}
        />
        <span>{value === true ? 'Yes' : 'No'}</span>
      </label>
    )
  }

  if (field.field_type === 'select') {
    return (
      <select
        id={fieldControlId(field.id)}
        className="field-control-select"
        value={isEmpty(value) ? '' : String(value)}
        onChange={(event) => commitOrFlagEmpty(event.target.value, event.target.value === '')}
      >
        {!field.is_required && <option value="">—</option>}
        {field.select_options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }

  // `type="text"` even for a numeric field: a native `type="number"` input silently
  // replaces invalid keystrokes with an empty value instead of letting this component
  // show its own "must be a number" message, so validation stays in our own control.
  const inputType = field.field_type === 'date' ? 'date' : field.field_type === 'url' ? 'url' : 'text'
  const inputMode = field.field_type === 'number' ? 'decimal' : undefined

  async function handleBlur() {
    if (draft.trim() === '') {
      await commitOrFlagEmpty(null, true)
      return
    }
    if (field.field_type === 'number') {
      const parsed = Number(draft)
      if (Number.isNaN(parsed)) {
        setError('Must be a number')
        return
      }
      await commitOrFlagEmpty(parsed, false)
      return
    }
    if (field.field_type === 'date' && Number.isNaN(Date.parse(draft))) {
      setError('Must be a valid date')
      return
    }
    if (field.field_type === 'url' && !isAbsoluteHttpUrl(draft)) {
      setError('Must be an absolute http(s) URL')
      return
    }
    await commitOrFlagEmpty(draft, false)
  }

  return (
    <>
      <input
        id={fieldControlId(field.id)}
        type={inputType}
        inputMode={inputMode}
        className="field-control-input"
        value={draft}
        placeholder={field.field_type === 'object_reference' ? 'node id' : undefined}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={handleBlur}
      />
      {error && <span className="field-error">{error}</span>}
    </>
  )
}

export function Inspector({
  node,
  nodeType,
  relations,
  onChangeStatus,
  onChangeField,
  onArchive,
}: InspectorProps) {
  if (!node || !nodeType) {
    return (
      <aside className="inspector" aria-label="Selected object">
        <p className="inspector-empty">Select a node on the canvas to see its details here.</p>
      </aside>
    )
  }

  const statusOptions: StatusDefinition[] = nodeType.status_definitions

  return (
    <aside className="inspector" aria-label="Selected object" key={node.id}>
      <div className="inspector-eyebrow">{nodeType.name}</div>
      <h2 className="inspector-title">{node.title}</h2>

      {statusOptions.length > 0 && (
        <>
          <label className="field-label" htmlFor="insp-status">
            Status
          </label>
          <select
            id="insp-status"
            className="status-select"
            value={node.status_id ?? ''}
            onChange={(event) => onChangeStatus(event.target.value)}
          >
            {!node.status_id && <option value="">No status</option>}
            {statusOptions.map((status) => (
              <option key={status.id} value={status.id}>
                {status.name}
              </option>
            ))}
          </select>
        </>
      )}

      {node.body && (
        <div className="field">
          <span className="field-label">Description</span>
          <div className="field-value">{node.body}</div>
        </div>
      )}

      {nodeType.field_definitions.map((field) => (
        <div className="field" key={field.id}>
          <label className="field-label" htmlFor={fieldControlId(field.id)}>
            {field.name.replace(/_/g, ' ')}
            {field.is_required && (
              <span
                className="field-required-mark"
                title="Required once set — you can leave it empty, but you can't clear it back to empty once it has a value"
              >
                {' '}
                *
              </span>
            )}
          </label>
          <FieldControl
            field={field}
            value={node.field_values[field.id]}
            onCommit={(value) => onChangeField(field.id, value)}
          />
        </div>
      ))}

      <div className="divider" />

      <div className="field" style={{ marginTop: 0 }}>
        <span className="field-label">Relations</span>
        <div className="rel-list">
          {relations.length === 0 && <span className="field-value">No relations yet.</span>}
          {relations.map((relation, index) => (
            <div className="rel-row" key={index}>
              <span className="rel-badge">{relation.edgeTypeName}</span>
              <span className="rel-target">{relation.otherNodeTitle}</span>
            </div>
          ))}
        </div>
      </div>

      <button type="button" className="archive-button" onClick={onArchive}>
        Archive (Delete)
      </button>
    </aside>
  )
}
