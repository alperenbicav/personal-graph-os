import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentsView } from './AgentsView'
import type { Agent } from '../../types'

const mockAgents: Agent[] = [
  {
    id: 'agent-custom-1',
    name: 'Custom-Synthesizer',
    emoji: '🔬',
    system_prompt: 'Custom synthesizer instructions',
    tool_allowlist: ['search', 'read_node'],
    model: 'claude-3-5-sonnet',
    write_mode: 'direct',
    created_at: '2026-01-01T00:00:00Z',
  },
]

describe('AgentsView', () => {
  it('renders agent cards and opens create modal', () => {
    const onCreate = vi.fn().mockResolvedValue(undefined)
    render(
      <AgentsView
        agents={mockAgents}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onCreateAgent={onCreate}
        onUpdateAgent={vi.fn().mockResolvedValue(undefined)}
        onDeleteAgent={vi.fn().mockResolvedValue(undefined)}
      />
    )

    expect(screen.getByText('Autonomous Agents Fleet')).toBeInTheDocument()
    expect(screen.getByText('Custom-Synthesizer')).toBeInTheDocument()
    expect(screen.getByText('claude-3-5-sonnet')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /\+ create agent/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Create New Agent')).toBeInTheDocument()
  })

  it('validates required fields before submitting create agent', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined)
    render(
      <AgentsView
        agents={mockAgents}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onCreateAgent={onCreate}
        onUpdateAgent={vi.fn().mockResolvedValue(undefined)}
        onDeleteAgent={vi.fn().mockResolvedValue(undefined)}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /\+ create agent/i }))

    // Submit with empty name
    const dialog = screen.getByRole('dialog')
    const submitBtn = within(dialog).getByRole('button', { name: /^create agent$/i })
    fireEvent.click(submitBtn)

    expect(screen.getByText(/please provide an agent name/i)).toBeInTheDocument()
    expect(onCreate).not.toHaveBeenCalled()

    // Fill valid name and submit
    const nameInput = screen.getByLabelText(/agent name/i)
    fireEvent.change(nameInput, { target: { value: 'Planner-Pro' } })
    fireEvent.click(submitBtn)

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Planner-Pro',
        emoji: '🤖',
        write_mode: 'proposal',
      })
    )
  })

  it('selects agent for chat playground', () => {
    const onSelectChat = vi.fn()
    render(
      <AgentsView
        agents={mockAgents}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onCreateAgent={vi.fn().mockResolvedValue(undefined)}
        onUpdateAgent={vi.fn().mockResolvedValue(undefined)}
        onDeleteAgent={vi.fn().mockResolvedValue(undefined)}
        onSelectChatAgent={onSelectChat}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /chat/i }))
    expect(onSelectChat).toHaveBeenCalledWith('agent-custom-1')
  })
})
