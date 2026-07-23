import { useState } from 'react'
import { NEXT_WORKFLOW_STEP } from '../lib/workflowChain'

export interface WorkflowChainCandidate {
  id: string
  title: string
}

export type WorkflowChainAdvanceInput = { title: string } | { existingTargetNodeId: string }

interface WorkflowChainPanelProps {
  nodeSystemKey: string | null | undefined
  existingTargetCandidates: WorkflowChainCandidate[]
  onAdvance: (input: WorkflowChainAdvanceInput) => Promise<void>
}

/** Guided Resource -> Takeaway -> Decision -> Task -> Implementation action: shown next to
 * whichever chain node is selected, offering the single next step for its role — either
 * creating a new node or connecting to an existing one of the right semantic type. */
export function WorkflowChainPanel({
  nodeSystemKey,
  existingTargetCandidates,
  onAdvance,
}: WorkflowChainPanelProps) {
  const [title, setTitle] = useState('')
  const [selectedExistingId, setSelectedExistingId] = useState('')
  const [isAdvancing, setIsAdvancing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const next = nodeSystemKey ? NEXT_WORKFLOW_STEP[nodeSystemKey] : undefined
  if (!next) return null

  async function submit(input: WorkflowChainAdvanceInput) {
    setIsAdvancing(true)
    setError(null)
    try {
      await onAdvance(input)
      setTitle('')
      setSelectedExistingId('')
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError))
    } finally {
      setIsAdvancing(false)
    }
  }

  return (
    <div className="workflow-chain-panel">
      <span className="field-label">Add {next.targetLabel}</span>
      <div className="workflow-chain-row">
        <input
          type="text"
          placeholder={`${next.targetLabel} title…`}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && title.trim()) submit({ title: title.trim() })
          }}
        />
        <button
          type="button"
          onClick={() => submit({ title: title.trim() })}
          disabled={isAdvancing || !title.trim()}
        >
          Add
        </button>
      </div>

      {existingTargetCandidates.length > 0 && (
        <div className="workflow-chain-row">
          <label className="sr-only" htmlFor="workflow-chain-existing-target">
            Connect to an existing {next.targetLabel}
          </label>
          <select
            id="workflow-chain-existing-target"
            value={selectedExistingId}
            onChange={(event) => setSelectedExistingId(event.target.value)}
          >
            <option value="">Connect to an existing {next.targetLabel}…</option>
            {existingTargetCandidates.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => submit({ existingTargetNodeId: selectedExistingId })}
            disabled={isAdvancing || !selectedExistingId}
          >
            Connect
          </button>
        </div>
      )}

      {error && (
        <span className="field-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
