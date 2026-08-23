import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentDock } from './AgentDock'
import type { Agent, AgentRun } from '../../types'

const mockAgents: Agent[] = [
  {
    id: 'agent-research-default',
    name: 'Research-Agent',
    emoji: '🤖',
    system_prompt: 'Research assistant',
    tool_allowlist: ['search', 'list_nodes'],
    model: null,
    write_mode: 'proposal',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'agent-ingest-default',
    name: 'Ingest-Agent',
    emoji: '📥',
    system_prompt: 'Ingest assistant',
    tool_allowlist: [],
    model: 'gpt-4o-mini',
    write_mode: 'direct',
    created_at: '2026-01-01T00:00:00Z',
  },
]

const mockRuns: AgentRun[] = [
  {
    id: 'run-1',
    agent_id: 'agent-research-default',
    action: 'summarize_papers',
    entity_type: 'resource',
    entity_id: 'res-1',
    status: 'applied',
    summary: 'added section to wiki/self-attention',
    diff_json: JSON.stringify({ additions: 12, deletions: 3 }),
    created_at: new Date().toISOString(),
  },
]

describe('AgentDock', () => {
  it('renders stream events and agent actions', () => {
    const onAskAgent = vi.fn().mockResolvedValue(undefined)
    render(
      <AgentDock
        agents={mockAgents}
        runs={mockRuns}
        onAskAgent={onAskAgent}
      />
    )

    expect(screen.getByText(/agent stream/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Research-Agent/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/added section to wiki\/self-attention/i)).toBeInTheDocument()
  })

  it('submits a prompt through AskAgentComposer', async () => {
    const onAskAgent = vi.fn().mockResolvedValue(undefined)
    render(
      <AgentDock
        agents={mockAgents}
        runs={mockRuns}
        onAskAgent={onAskAgent}
      />
    )

    const textarea = screen.getByLabelText(/ask agent prompt/i)
    fireEvent.change(textarea, { target: { value: 'Summarize graph nodes' } })
    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true })

    expect(onAskAgent).toHaveBeenCalledWith('agent-research-default', 'Summarize graph nodes')
  })

  it('displays running indicator when agent is actively processing', () => {
    render(
      <AgentDock
        agents={mockAgents}
        runs={[]}
        onAskAgent={vi.fn()}
        isAgentRunning={true}
      />
    )

    expect(screen.getByText(/analyzing and executing/i)).toBeInTheDocument()
  })

  it('renders empty state when there are no runs', () => {
    render(
      <AgentDock
        agents={mockAgents}
        runs={[]}
        onAskAgent={vi.fn()}
      />
    )

    expect(screen.getByText(/agent stream idle/i)).toBeInTheDocument()
    expect(screen.getByText(/no recent agent events/i)).toBeInTheDocument()
  })

  it('renders LLM provider not configured guidance card when error occurs', () => {
    const onClearError = vi.fn()
    render(
      <AgentDock
        agents={mockAgents}
        runs={[]}
        onAskAgent={vi.fn()}
        lastError="Agent execution failed: 503 LLM provider not configured"
        onClearError={onClearError}
      />
    )

    expect(screen.getByText(/LLM Provider Not Configured/i)).toBeInTheDocument()
    expect(screen.getByText(/PGOS_LLM_PROVIDER=openai/i)).toBeInTheDocument()
    expect(screen.getByText(/PGOS_LLM_API_KEY=your-api-key/i)).toBeInTheDocument()

    const dismissBtn = screen.getByRole('button', { name: /dismiss error/i })
    fireEvent.click(dismissBtn)
    expect(onClearError).toHaveBeenCalledTimes(1)
  })
})
