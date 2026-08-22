import { useState } from 'react'
import type { FieldDefinition, GraphNode, NodeType, StatusDefinition } from '../types'
import { FilesPanel } from './FilesPanel'

export interface RelationRow {
  edgeTypeName: string
  otherNodeTitle: string
}

export interface ReferenceableNode {
  id: string
  title: string
}

interface InspectorProps {
  archiveArmed?: boolean
  node: GraphNode | null
  nodeType: NodeType | undefined
  relations: RelationRow[]
  referenceableNodes: ReferenceableNode[]
  onChangeStatus: (statusId: string) => void
  onChangeBody: (body: string) => Promise<boolean>
  onChangeField: (fieldDefinitionId: string, value: unknown) => Promise<boolean>
  onArchive: () => void
  goToLabel?: string | null
  onGoTo?: () => void
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
  referenceableNodes: ReferenceableNode[]
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
function FieldControl({ field, value, referenceableNodes, onCommit }: FieldControlProps) {
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

  if (field.field_type === 'object_reference') {
    const currentValue = isEmpty(value) ? '' : String(value)
    const isStale = currentValue !== '' && !referenceableNodes.some((n) => n.id === currentValue)
    return (
      <>
        <select
          id={fieldControlId(field.id)}
          className="field-control-select"
          value={currentValue}
          onChange={(event) => commitOrFlagEmpty(event.target.value, event.target.value === '')}
        >
          {!field.is_required && <option value="">—</option>}
          {isStale && (
            <option value={currentValue} disabled>
              (no longer available)
            </option>
          )}
          {referenceableNodes.map((referenceable) => (
            <option key={referenceable.id} value={referenceable.id}>
              {referenceable.title}
            </option>
          ))}
        </select>
        {isStale && (
          <span className="field-error" role="alert">
            This reference no longer exists — choose another
          </span>
        )}
      </>
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
        onChange={(event) => setDraft(event.target.value)}
        onBlur={handleBlur}
      />
      {error && (
        <span className="field-error" role="alert">
          {error}
        </span>
      )}
    </>
  )
}

interface DescriptionFieldProps {
  nodeId: string
  body: string
  onCommit: (value: string) => Promise<boolean>
}

/** Every node has a free-text `body` (the domain's built-in description field, independent
 * of any schema-driven `FieldDefinition`) — this makes it editable rather than only shown
 * when already non-empty, since there was previously no way to set it from the UI at all. */
function DescriptionField({ nodeId, body, onCommit }: DescriptionFieldProps) {
  const [draft, setDraft] = useState(body)

  async function handleBlur() {
    if (draft === body) return
    if (!(await onCommit(draft))) setDraft(body)
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={`insp-description-${nodeId}`}>
        Description
      </label>
      <textarea
        id={`insp-description-${nodeId}`}
        className="field-control-textarea"
        rows={4}
        placeholder="Add a description…"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={handleBlur}
      />
    </div>
  )
}

export function Inspector({ archiveArmed = false,
  node,
  nodeType,
  relations,
  referenceableNodes,
  onChangeStatus,
  onChangeBody,
  onChangeField,
  onArchive,
  goToLabel,
  onGoTo,
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

      <DescriptionField nodeId={node.id} body={node.body} onCommit={onChangeBody} />

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
            referenceableNodes={referenceableNodes}
            onCommit={(value) => onChangeField(field.id, value)}
          />
        </div>
      ))}

      <div className="divider" />

      <FilesPanel nodeId={node.id} />

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

      <button
        type="button"
        className={archiveArmed ? 'archive-button archive-button-armed' : 'archive-button'}
        onClick={onArchive}
      >
        {archiveArmed ? 'Confirm archive' : 'Archive'}
      </button>
      {goToLabel && onGoTo && (
        <button type="button" className="go-to-button" onClick={onGoTo}>
          Go to {goToLabel}
        </button>
      )}
    </aside>
  )
}
