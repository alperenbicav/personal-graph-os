import { useState } from 'react'
import { NEXT_WORKFLOW_STEP } from '../lib/workflowChain'

interface WorkflowChainPanelProps {
  nodeSystemKey: string | null | undefined
  onAdvance: (title: string) => Promise<void>
}

/** Guided Resource -> Takeaway -> Decision -> Task -> Implementation action: shown next to
 * whichever chain node is selected, offering the single next step for its role. */
export function WorkflowChainPanel({ nodeSystemKey, onAdvance }: WorkflowChainPanelProps) {
  const [title, setTitle] = useState('')
  const [isAdvancing, setIsAdvancing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const next = nodeSystemKey ? NEXT_WORKFLOW_STEP[nodeSystemKey] : undefined
  if (!next) return null

  async function submit() {
    const trimmed = title.trim()
    if (!trimmed) return
    setIsAdvancing(true)
    setError(null)
    try {
      await onAdvance(trimmed)
      setTitle('')
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
            if (event.key === 'Enter') submit()
          }}
        />
        <button type="button" onClick={submit} disabled={isAdvancing || !title.trim()}>
          Add
        </button>
      </div>
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}
