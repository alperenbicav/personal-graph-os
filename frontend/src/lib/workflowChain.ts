import type { WorkflowChainStep } from '../types'

/** Mirrors `WORKFLOW_CHAIN_STEP_DEFINITIONS` (backend `application/workflow_chain.py`): the
 * step to advance from a node carrying a given semantic `system_key`, the role/label of the
 * node it produces, and that target's own `system_key` (used to find same-workspace existing
 * nodes eligible to connect to instead of creating a new one). `null` for a role with no next
 * step (the chain's end). */
export const NEXT_WORKFLOW_STEP: Record<
  string,
  { step: WorkflowChainStep; targetLabel: string; targetSystemKey: string } | undefined
> = {
  resource: { step: 'resource_to_takeaway', targetLabel: 'Takeaway', targetSystemKey: 'takeaway' },
  takeaway: {
    step: 'takeaway_to_decision',
    targetLabel: 'Decision',
    targetSystemKey: 'decision',
  },
  decision: { step: 'decision_to_task', targetLabel: 'Task', targetSystemKey: 'task' },
  task: {
    step: 'task_to_implementation',
    targetLabel: 'Implementation',
    targetSystemKey: 'implementation',
  },
}
