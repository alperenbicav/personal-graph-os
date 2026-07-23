import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActivityView } from './ActivityView'
import type { ActivityEvent, ActivityEventSummary } from '../types'

function makeSummary(overrides: Partial<ActivityEventSummary> = {}): ActivityEventSummary {
  return {
    id: 'evt-1',
    workspace_id: 'ws-1',
    actor_kind: 'human',
    actor_name: 'human/local-user/rest',
    source: 'rest',
    entity_type: 'node',
    entity_id: 'node-1',
    action: 'updated',
    reason: null,
    is_undoable: true,
    occurred_at: '2026-01-01T00:00:00Z',
    reverses_event_id: null,
    disabled_reason: null,
    ...overrides,
  }
}

function makeDetail(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    ...makeSummary(),
    session_id: null,
    before_state: { title: 'before' },
    after_state: { title: 'after' },
    request_id: null,
    ...overrides,
  }
}

describe('ActivityView', () => {
  it('loads and renders the first page of events', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeSummary()], next_cursor: null })
    render(
      <ActivityView
        onLoadPage={onLoadPage}
        onLoadDetail={vi.fn().mockResolvedValue(makeDetail())}
        onUndo={vi.fn()}
      />,
    )

    expect(await screen.findByText('node node-1')).toBeInTheDocument()
    expect(onLoadPage).toHaveBeenCalledWith(null)
  })

  it('shows an empty message when there is no activity', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [], next_cursor: null })
    render(
      <ActivityView
        onLoadPage={onLoadPage}
        onLoadDetail={vi.fn()}
        onUndo={vi.fn()}
      />,
    )

    expect(await screen.findByText(/no activity recorded/i)).toBeInTheDocument()
  })

  it('lazily fetches and shows a row detail on expand', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeSummary()], next_cursor: null })
    const onLoadDetail = vi.fn().mockResolvedValue(makeDetail())
    render(<ActivityView onLoadPage={onLoadPage} onLoadDetail={onLoadDetail} onUndo={vi.fn()} />)

    fireEvent.click(await screen.findByText('node node-1'))

    await waitFor(() => expect(onLoadDetail).toHaveBeenCalledWith('evt-1'))
    expect(await screen.findByText(/"before"/)).toBeInTheDocument()
    expect(screen.getByText(/"after"/)).toBeInTheDocument()
  })

  it('shows an error when the detail fetch fails', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeSummary()], next_cursor: null })
    const onLoadDetail = vi.fn().mockRejectedValue(new Error('detail fetch failed'))
    render(<ActivityView onLoadPage={onLoadPage} onLoadDetail={onLoadDetail} onUndo={vi.fn()} />)

    fireEvent.click(await screen.findByText('node node-1'))

    expect(await screen.findByText(/detail fetch failed/i)).toBeInTheDocument()
  })

  it('confirms and submits an undo with a bounded reason', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeSummary()], next_cursor: null })
    const onLoadDetail = vi.fn().mockResolvedValue(makeDetail())
    const onUndo = vi.fn().mockResolvedValue(undefined)
    render(<ActivityView onLoadPage={onLoadPage} onLoadDetail={onLoadDetail} onUndo={onUndo} />)

    fireEvent.click(await screen.findByText('node node-1'))
    await screen.findByText(/"before"/)
    fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
    fireEvent.change(screen.getByLabelText(/reason for undo/i), {
      target: { value: 'accidental edit' },
    })
    fireEvent.click(screen.getByRole('button', { name: /confirm undo/i }))

    await waitFor(() => expect(onUndo).toHaveBeenCalledWith('evt-1', 'accidental edit'))
    expect(await screen.findByText(/undone/i)).toBeInTheDocument()
  })

  it('does not offer undo for a non-undoable event', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({
      events: [makeSummary({ is_undoable: false, disabled_reason: 'unsupported_action' })],
      next_cursor: null,
    })
    render(
      <ActivityView
        onLoadPage={onLoadPage}
        onLoadDetail={vi.fn().mockResolvedValue(makeDetail({ is_undoable: false }))}
        onUndo={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByText('node node-1'))
    await waitFor(() => expect(screen.queryByText(/loading detail/i)).not.toBeInTheDocument())

    expect(screen.queryByRole('button', { name: /^undo$/i })).not.toBeInTheDocument()
    expect(screen.getByText(/not undoable/i)).toBeInTheDocument()
  })

  it('shows a distinct reason for a non-undoable event with an oversized snapshot', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({
      events: [
        makeSummary({ is_undoable: false, disabled_reason: 'snapshot_omitted_oversized' }),
      ],
      next_cursor: null,
    })
    render(
      <ActivityView
        onLoadPage={onLoadPage}
        onLoadDetail={vi.fn().mockResolvedValue(makeDetail({ is_undoable: false }))}
        onUndo={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByText('node node-1'))
    await waitFor(() => expect(screen.queryByText(/loading detail/i)).not.toBeInTheDocument())

    expect(screen.getByText(/too large to store safely/i)).toBeInTheDocument()
  })

  it('shows an error with a retry option when undo fails', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({ events: [makeSummary()], next_cursor: null })
    const onLoadDetail = vi.fn().mockResolvedValue(makeDetail())
    const onUndo = vi.fn().mockRejectedValue(new Error('stale: entity changed'))
    render(<ActivityView onLoadPage={onLoadPage} onLoadDetail={onLoadDetail} onUndo={onUndo} />)

    fireEvent.click(await screen.findByText('node node-1'))
    await screen.findByText(/"before"/)
    fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
    fireEvent.change(screen.getByLabelText(/reason for undo/i), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /confirm undo/i }))

    expect(await screen.findByText(/stale: entity changed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('filters the visible rows by entity type', async () => {
    const onLoadPage = vi.fn().mockResolvedValue({
      events: [
        makeSummary({ id: 'evt-1', entity_type: 'node', entity_id: 'node-1' }),
        makeSummary({ id: 'evt-2', entity_type: 'edge', entity_id: 'edge-1' }),
      ],
      next_cursor: null,
    })
    render(
      <ActivityView
        onLoadPage={onLoadPage}
        onLoadDetail={vi.fn().mockResolvedValue(makeDetail())}
        onUndo={vi.fn()}
      />,
    )

    expect(await screen.findByText('node node-1')).toBeInTheDocument()
    expect(screen.getByText('edge edge-1')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/entity type/i), { target: { value: 'edge' } })

    expect(screen.queryByText('node node-1')).not.toBeInTheDocument()
    expect(screen.getByText('edge edge-1')).toBeInTheDocument()
  })
})
