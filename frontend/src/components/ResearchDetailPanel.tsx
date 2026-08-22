import { useState } from 'react'
import type { UpdateResourcePatch } from '../api/client'
import type { Resource, ResourceLifecycleStatus } from '../types'

const LIFECYCLE_OPTIONS: ResourceLifecycleStatus[] = [
  'inbox',
  'to_review',
  'reading',
  'paused',
  'reviewed',
  'applied',
  'archived',
]

interface StringListEditorProps {
  label: string
  values: string[]
  onCommit: (values: string[]) => Promise<boolean>
}

/** Add/remove editor for `takeaways`/`open_questions`: the backend always replaces the
 * whole array, so every mutation here sends the full next array. */
function StringListEditor({ label, values, onCommit }: StringListEditorProps) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function add() {
    const trimmed = draft.trim()
    if (!trimmed) return
    if (await onCommit([...values, trimmed])) {
      setDraft('')
      setError(null)
    } else {
      setError('Could not save')
    }
  }

  async function removeAt(index: number) {
    const next = values.filter((_, i) => i !== index)
    if (!(await onCommit(next))) setError('Could not save')
  }

  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <ul className="string-list">
        {values.map((value, index) => (
          <li key={index}>
            <span>{value}</span>
            <button type="button" onClick={() => removeAt(index)} aria-label={`Remove ${value}`}>
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="workflow-chain-row">
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') add()
          }}
        />
        <button type="button" onClick={add} disabled={!draft.trim()}>
          Add
        </button>
      </div>
      {error && (
        <span className="field-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

interface ResearchDetailPanelProps {
  resource: Resource
  onUpdate: (patch: UpdateResourcePatch) => Promise<boolean>
}

function progressDraftFrom(resource: Resource): string {
  return resource.progress_percent !== null ? String(resource.progress_percent) : ''
}

export function ResearchDetailPanel({ resource, onUpdate }: ResearchDetailPanelProps) {
  const [nextActionDraft, setNextActionDraft] = useState(resource.next_action ?? '')
  const [reviewAtDraft, setReviewAtDraft] = useState(resource.review_at?.slice(0, 10) ?? '')
  const [progressDraft, setProgressDraft] = useState(progressDraftFrom(resource))
  const [error, setError] = useState<string | null>(null)

  async function commit(patch: UpdateResourcePatch, revert?: () => void) {
    setError(null)
    if (!(await onUpdate(patch))) {
      setError('Could not save that change')
      revert?.()
    }
  }

  return (
    <div className="research-detail" key={resource.id}>
      <div className="field">
        <label className="field-label" htmlFor="research-lifecycle">
          Lifecycle status
        </label>
        <select
          id="research-lifecycle"
          value={resource.lifecycle_status}
          onChange={(event) =>
            commit({ lifecycle_status: event.target.value as ResourceLifecycleStatus })
          }
        >
          {LIFECYCLE_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field-label" htmlFor="research-next-action">
          Next action
        </label>
        <input
          id="research-next-action"
          type="text"
          value={nextActionDraft}
          onChange={(event) => setNextActionDraft(event.target.value)}
          onBlur={() => {
            const trimmed = nextActionDraft.trim()
            if (trimmed) {
              commit({ next_action: trimmed }, () => setNextActionDraft(resource.next_action ?? ''))
            } else {
              commit({ clear_next_action: true })
            }
          }}
        />
        <label className="schema-inline-checkbox">
          <input
            type="checkbox"
            checked={resource.next_action_dismissed}
            onChange={(event) => commit({ next_action_dismissed: event.target.checked })}
          />
          <span>Dismissed (paused on purpose, no next action)</span>
        </label>
      </div>

      <div className="field">
        <label className="field-label" htmlFor="research-progress">
          Progress (%)
        </label>
        <input
          id="research-progress"
          type="number"
          min={0}
          max={100}
          placeholder="0"
          value={progressDraft}
          onChange={(event) => setProgressDraft(event.target.value)}
          onBlur={() => {
            const trimmed = progressDraft.trim()
            if (!trimmed) {
              commit({ clear_progress_percent: true })
              return
            }
            const parsed = Number(trimmed)
            if (!Number.isInteger(parsed) || parsed < 0 || parsed > 100) {
              setError('Progress must be a whole number between 0 and 100')
              setProgressDraft(progressDraftFrom(resource))
              return
            }
            commit({ progress_percent: parsed }, () => setProgressDraft(progressDraftFrom(resource)))
          }}
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="research-review-at">
          Review date
        </label>
        <input
          id="research-review-at"
          type="date"
          value={reviewAtDraft}
          onChange={(event) => setReviewAtDraft(event.target.value)}
          onBlur={() => {
            if (reviewAtDraft) {
              commit(
                { review_at: new Date(reviewAtDraft).toISOString() },
                () => setReviewAtDraft(resource.review_at?.slice(0, 10) ?? ''),
              )
            } else {
              commit({ clear_review_at: true })
            }
          }}
        />
      </div>

      <StringListEditor
        label="Takeaways"
        values={resource.takeaways}
        onCommit={(values) => onUpdate({ takeaways: values })}
      />
      <StringListEditor
        label="Open questions"
        values={resource.open_questions}
        onCommit={(values) => onUpdate({ open_questions: values })}
      />

      {error && (
        <span className="field-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
