import type { WorkflowChainStep } from '../types'

/** Mirrors `WORKFLOW_CHAIN_STEP_DEFINITIONS` (backend `application/workflow_chain.py`): the
 * step to advance from a node carrying a given semantic `system_key`, and the role/label of
 * the node it produces. `null` for a role with no next step (the chain's end). */
export const NEXT_WORKFLOW_STEP: Record<
  string,
  { step: WorkflowChainStep; targetLabel: string } | undefined
> = {
  resource: { step: 'resource_to_takeaway', targetLabel: 'Takeaway' },
  takeaway: { step: 'takeaway_to_decision', targetLabel: 'Decision' },
  decision: { step: 'decision_to_task', targetLabel: 'Task' },
  task: { step: 'task_to_implementation', targetLabel: 'Implementation' },
}
