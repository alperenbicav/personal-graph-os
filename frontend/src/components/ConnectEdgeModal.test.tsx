import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConnectEdgeModal, type PendingConnection } from './ConnectEdgeModal'
import type { EdgeType } from '../types'

const pending: PendingConnection = {
  sourceNodeId: 'n1',
  sourceTitle: 'Provider refactor',
  targetNodeId: 'n2',
  targetTitle: 'Use typed providers',
}

const edgeTypes: EdgeType[] = [
  { id: 'et-implements', name: 'implements', inverse_name: 'is_implemented_by', color_hex: '#2563eb' },
  { id: 'et-cites', name: 'cites', inverse_name: 'is_cited_by', color_hex: '#8b5cf6' },
]

describe('ConnectEdgeModal', () => {
  it('shows the source and target node titles', () => {
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText('Provider refactor')).toBeInTheDocument()
    expect(screen.getByText('Use typed providers')).toBeInTheDocument()
  })

  it('confirms with the selected edge type id', () => {
    const onConfirm = vi.fn()
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={onConfirm} onCancel={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/relationship type/i), { target: { value: 'et-cites' } })
    fireEvent.click(screen.getByRole('button', { name: /connect/i }))

    expect(onConfirm).toHaveBeenCalledWith('et-cites')
  })

  it('cancels without confirming', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={onConfirm} onCancel={onCancel} />)

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })
})
