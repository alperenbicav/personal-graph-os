import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowChainPanel } from './WorkflowChainPanel'

describe('WorkflowChainPanel', () => {
  it('renders nothing for a node role with no next workflow step', () => {
    const { container } = render(
      <WorkflowChainPanel
        nodeSystemKey="implementation"
        existingTargetCandidates={[]}
        onAdvance={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for a node with no semantic role at all', () => {
    const { container } = render(
      <WorkflowChainPanel nodeSystemKey={null} existingTargetCandidates={[]} onAdvance={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('offers the next step label for a resource node and submits the typed title', async () => {
    const onAdvance = vi.fn().mockResolvedValue(undefined)
    render(
      <WorkflowChainPanel
        nodeSystemKey="resource"
        existingTargetCandidates={[]}
        onAdvance={onAdvance}
      />,
    )

    expect(screen.getByText('Add Takeaway')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/takeaway title/i), {
      target: { value: 'Key insight' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    expect(onAdvance).toHaveBeenCalledWith({ title: 'Key insight' })
  })

  it('shows an error message when onAdvance rejects', async () => {
    const onAdvance = vi.fn().mockRejectedValue(new Error('nope'))
    render(
      <WorkflowChainPanel
        nodeSystemKey="takeaway"
        existingTargetCandidates={[]}
        onAdvance={onAdvance}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText(/decision title/i), {
      target: { value: 'Ship it' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    expect(await screen.findByText('nope')).toBeInTheDocument()
  })

  it('does not show the existing-target selector when there are no candidates', () => {
    render(
      <WorkflowChainPanel nodeSystemKey="resource" existingTargetCandidates={[]} onAdvance={vi.fn()} />,
    )
    expect(screen.queryByLabelText(/connect to an existing/i)).not.toBeInTheDocument()
  })

  it('connects to a selected existing target instead of creating a new node', async () => {
    const onAdvance = vi.fn().mockResolvedValue(undefined)
    render(
      <WorkflowChainPanel
        nodeSystemKey="resource"
        existingTargetCandidates={[
          { id: 'takeaway-1', title: 'Existing takeaway A' },
          { id: 'takeaway-2', title: 'Existing takeaway B' },
        ]}
        onAdvance={onAdvance}
      />,
    )

    fireEvent.change(screen.getByLabelText(/connect to an existing/i), {
      target: { value: 'takeaway-2' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^connect$/i }))

    expect(onAdvance).toHaveBeenCalledWith({ existingTargetNodeId: 'takeaway-2' })
  })

  it('keeps the connect button disabled until an existing target is selected', () => {
    render(
      <WorkflowChainPanel
        nodeSystemKey="resource"
        existingTargetCandidates={[{ id: 'takeaway-1', title: 'Existing takeaway' }]}
        onAdvance={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /^connect$/i })).toBeDisabled()
  })
})
