import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowChainPanel } from './WorkflowChainPanel'

describe('WorkflowChainPanel', () => {
  it('renders nothing for a node role with no next workflow step', () => {
    const { container } = render(
      <WorkflowChainPanel nodeSystemKey="implementation" onAdvance={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for a node with no semantic role at all', () => {
    const { container } = render(<WorkflowChainPanel nodeSystemKey={null} onAdvance={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('offers the next step label for a resource node and submits the typed title', async () => {
    const onAdvance = vi.fn().mockResolvedValue(undefined)
    render(<WorkflowChainPanel nodeSystemKey="resource" onAdvance={onAdvance} />)

    expect(screen.getByText('Add Takeaway')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/takeaway title/i), {
      target: { value: 'Key insight' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    expect(onAdvance).toHaveBeenCalledWith('Key insight')
  })

  it('shows an error message when onAdvance rejects', async () => {
    const onAdvance = vi.fn().mockRejectedValue(new Error('nope'))
    render(<WorkflowChainPanel nodeSystemKey="takeaway" onAdvance={onAdvance} />)

    fireEvent.change(screen.getByPlaceholderText(/decision title/i), {
      target: { value: 'Ship it' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    expect(await screen.findByText('nope')).toBeInTheDocument()
  })
})
