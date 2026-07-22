import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActivityView } from './ActivityView'
import type { ActivityEvent } from '../types'

function makeEvent(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: 'evt-1',
    workspace_id: 'ws-1',
    actor_kind: 'human',
    actor_name: 'human/local-user/rest',
    source: 'rest',
    entity_type: 'node',
    entity_id: 'node-1',
    action: 'updated',
    session_id: null,
    reason: null,
    before_state: { title: 'before' },
    after_state: { title: 'after' },
    is_undoable: true,
    occurred_at: '2026-01-01T00:00:00Z',
    request_id: null,
    reverses_event_id: null,
    ...overrides,
  }
}

describe('ActivityView', () => {
  it('loads and renders the first page of events', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeEvent()], next_cursor: null })
    render(<ActivityView onLoadPage={onLoadPage} onUndo={vi.fn()} />)

    expect(await screen.findByText('node node-1')).toBeInTheDocument()
    expect(onLoadPage).toHaveBeenCalledWith(null)
  })

  it('shows an empty message when there is no activity', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [], next_cursor: null })
    render(<ActivityView onLoadPage={onLoadPage} onUndo={vi.fn()} />)

    expect(await screen.findByText(/no activity recorded/i)).toBeInTheDocument()
  })

  it('expands a row to show its before/after detail', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeEvent()], next_cursor: null })
    render(<ActivityView onLoadPage={onLoadPage} onUndo={vi.fn()} />)

    fireEvent.click(await screen.findByText('node node-1'))

    expect(screen.getByText(/"before"/)).toBeInTheDocument()
    expect(screen.getByText(/"after"/)).toBeInTheDocument()
  })

  it('confirms and submits an undo with a bounded reason', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeEvent()], next_cursor: null })
    const onUndo = vi.fn().mockResolvedValue(undefined)
    render(<ActivityView onLoadPage={onLoadPage} onUndo={onUndo} />)

    fireEvent.click(await screen.findByText('node node-1'))
    fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
    fireEvent.change(screen.getByLabelText(/reason for undo/i), {
      target: { value: 'accidental edit' },
    })
    fireEvent.click(screen.getByRole('button', { name: /confirm undo/i }))

    await waitFor(() => expect(onUndo).toHaveBeenCalledWith('evt-1', 'accidental edit'))
    expect(await screen.findByText(/undone/i)).toBeInTheDocument()
  })

  it('does not offer undo for a non-undoable event', async () => {
    const onLoadPage = vi
      .fn()
      .mockResolvedValue({ events: [makeEvent({ is_undoable: false })], next_cursor: null })
    render(<ActivityView onLoadPage={onLoadPage} onUndo={vi.fn()} />)

    fireEvent.click(await screen.findByText('node node-1'))

    expect(screen.queryByRole('button', { name: /^undo$/i })).not.toBeInTheDocument()
    expect(screen.getByText(/not undoable/i)).toBeInTheDocument()
  })

  it('shows an error with a retry option when undo fails', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeEvent()], next_cursor: null })
    const onUndo = vi.fn().mockRejectedValue(new Error('stale: entity changed'))
    render(<ActivityView onLoadPage={onLoadPage} onUndo={onUndo} />)

    fireEvent.click(await screen.findByText('node node-1'))
    fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
    fireEvent.change(screen.getByLabelText(/reason for undo/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /confirm undo/i }))

    expect(await screen.findByText(/stale: entity changed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('filters the visible rows by entity type', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({
      events: [
        makeEvent({ id: 'evt-1', entity_type: 'node', entity_id: 'node-1' }),
        makeEvent({ id: 'evt-2', entity_type: 'edge', entity_id: 'edge-1' }),
      ],
      next_cursor: null,
    })
    render(<ActivityView onLoadPage={onLoadPage} onUndo={vi.fn()} />)

    expect(await screen.findByText('node node-1')).toBeInTheDocument()
    expect(screen.getByText('edge edge-1')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/entity type/i), { target: { value: 'edge' } })

    expect(screen.queryByText('node node-1')).not.toBeInTheDocument()
    expect(screen.getByText('edge edge-1')).toBeInTheDocument()
  })
})
