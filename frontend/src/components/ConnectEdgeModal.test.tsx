import { useState } from 'react'
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

  it('sets modal semantics and moves focus inside on open', () => {
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={vi.fn()} onCancel={vi.fn()} />)

    const dialog = screen.getByRole('dialog', { name: /connect two objects/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    expect(document.activeElement).not.toBe(document.body)
  })

  it('closes on Escape', () => {
    const onCancel = vi.fn()
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={vi.fn()} onCancel={onCancel} />)

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('traps Tab focus within the dialog and wraps at both ends', () => {
    render(<ConnectEdgeModal pending={pending} edgeTypes={edgeTypes} onConfirm={vi.fn()} onCancel={vi.fn()} />)

    const select = screen.getByLabelText(/relationship type/i)
    const connectButton = screen.getByRole('button', { name: /connect/i })

    // First focusable element (the select) is focused on open.
    expect(document.activeElement).toBe(select)

    // Shift+Tab from the first element wraps to the last (Connect) — the boundary the
    // trap must own; ordinary mid-dialog Tab advancement is native browser behavior that
    // jsdom does not simulate, so it is not asserted here.
    fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(connectButton)

    // Tab from the last element wraps back to the first (the select).
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
    expect(document.activeElement).toBe(select)
  })

  function HostWithInvoker({ open }: { open: boolean }) {
    const [isOpen, setIsOpen] = useState(open)
    return (
      <div>
        <button type="button" onClick={() => setIsOpen(true)}>
          Open connect modal
        </button>
        {isOpen && (
          <ConnectEdgeModal
            pending={pending}
            edgeTypes={edgeTypes}
            onConfirm={vi.fn()}
            onCancel={() => setIsOpen(false)}
          />
        )}
      </div>
    )
  }

  it('restores focus to the invoking element after closing', () => {
    render(<HostWithInvoker open={false} />)
    const invoker = screen.getByRole('button', { name: /open connect modal/i })
    invoker.focus()
    fireEvent.click(invoker)

    expect(screen.getByRole('dialog', { name: /connect two objects/i })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: /connect two objects/i })).not.toBeInTheDocument()
    expect(document.activeElement).toBe(invoker)
  })
})
