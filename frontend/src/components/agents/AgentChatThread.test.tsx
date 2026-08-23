import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentChatThread } from './AgentChatThread'
import * as api from '../../api/client'
import type { Agent } from '../../types'

vi.mock('../../api/client')
const mockedApi = vi.mocked(api)

const mockAgent: Agent = {
  id: 'agent-1',
  name: 'Test-Agent',
  emoji: '🧪',
  system_prompt: 'Test prompt',
  tool_allowlist: [],
  model: null,
  write_mode: 'proposal',
  created_at: '2026-01-01T00:00:00Z',
}

describe('AgentChatThread', () => {
  it('renders welcome message and handles user message flow', async () => {
    mockedApi.messageAgent.mockResolvedValue({
      agent_id: 'agent-1',
      run_id: 'run-1',
      reply: 'I found 3 relevant papers on attention mechanisms.',
      status: 'success',
    })

    const onMessageSent = vi.fn()
    render(<AgentChatThread agent={mockAgent} onMessageSent={onMessageSent} />)

    expect(screen.getByText(/Hello! I am Test-Agent/i)).toBeInTheDocument()

    const input = screen.getByLabelText(/message input/i)
    fireEvent.change(input, { target: { value: 'Find papers' } })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))

    expect(mockedApi.messageAgent).toHaveBeenCalledWith(
      'agent-1',
      'Find papers',
      undefined,
      expect.any(Function),
    )
    await waitFor(() => {
      expect(screen.getByText('I found 3 relevant papers on attention mechanisms.')).toBeInTheDocument()
    })
    expect(onMessageSent).toHaveBeenCalled()
  })
})
