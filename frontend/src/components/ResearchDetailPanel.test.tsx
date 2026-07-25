import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ResearchDetailPanel } from './ResearchDetailPanel'
import type { Resource } from '../types'

const resource: Resource = {
  id: 'r1',
  workspace_id: 'ws-1',
  node_id: 'n1',
  kind: 'paper',
  canonical_identifier: 'arxiv:1',
  source_url: null,
  lifecycle_status: 'inbox',
  next_action: null,
  next_action_dismissed: false,
  open_questions: [],
  takeaways: ['Existing takeaway'],
  progress_percent: null,
  review_at: null,
  last_activity_at: '2026-01-01T00:00:00Z',
  repository_label: null,
  title: 'A paper',
  body: '',
}

const otherResource: Resource = {
  ...resource,
  id: 'r2',
  node_id: 'n2',
  canonical_identifier: 'arxiv:2',
  progress_percent: 80,
  title: 'Another paper',
}

describe('ResearchDetailPanel', () => {
  it('resets its drafts when the parent keys by resource id on A -> B selection', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    const withResource = { ...resource, progress_percent: 10 }
    const { rerender } = render(
      <ResearchDetailPanel key={withResource.id} resource={withResource} onUpdate={onUpdate} />,
    )
    expect(screen.getByLabelText(/progress/i)).toHaveValue(10)

    // Selection moves directly A -> B (no deselect in between), same as App.tsx's
    // `key={selectedResource.id}` on this call site.
    rerender(
      <ResearchDetailPanel key={otherResource.id} resource={otherResource} onUpdate={onUpdate} />,
    )

    expect(screen.getByLabelText(/progress/i)).toHaveValue(80)

    // A blur here must submit B's own value (80), never A's stale value (10) leaking through.
    fireEvent.blur(screen.getByLabelText(/progress/i))
    expect(onUpdate).toHaveBeenCalledWith({ progress_percent: 80 })
  })

  it('commits a lifecycle status change immediately', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    fireEvent.change(screen.getByLabelText(/lifecycle status/i), { target: { value: 'reading' } })

    expect(onUpdate).toHaveBeenCalledWith({ lifecycle_status: 'reading' })
  })

  it('commits a next-action edit on blur', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    const input = screen.getByLabelText('Next action')
    fireEvent.change(input, { target: { value: 'Read section 3' } })
    fireEvent.blur(input)

    expect(onUpdate).toHaveBeenCalledWith({ next_action: 'Read section 3' })
  })

  it('shows an error and does not lose the draft when the save fails', async () => {
    const onUpdate = vi.fn().mockResolvedValue(false)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    const input = screen.getByLabelText('Next action')
    fireEvent.change(input, { target: { value: 'Read section 3' } })
    fireEvent.blur(input)

    expect(await screen.findByText(/could not save/i)).toBeInTheDocument()
    expect(input).toHaveValue('')
  })

  it('commits a valid progress value on blur', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    const input = screen.getByLabelText(/progress/i)
    fireEvent.change(input, { target: { value: '42' } })
    fireEvent.blur(input)

    expect(onUpdate).toHaveBeenCalledWith({ progress_percent: 42 })
  })

  it('clears progress when the field is emptied', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(
      <ResearchDetailPanel resource={{ ...resource, progress_percent: 50 }} onUpdate={onUpdate} />,
    )

    const input = screen.getByLabelText(/progress/i)
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)

    expect(onUpdate).toHaveBeenCalledWith({ clear_progress_percent: true })
  })

  it('rejects an out-of-bounds progress value without calling onUpdate', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    const input = screen.getByLabelText(/progress/i)
    fireEvent.change(input, { target: { value: '150' } })
    fireEvent.blur(input)

    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByText(/between 0 and 100/i)).toBeInTheDocument()
    expect(input).toHaveValue(null)
  })

  it('renders existing takeaways and adds a new one via the full replacement array', () => {
    const onUpdate = vi.fn().mockResolvedValue(true)
    render(<ResearchDetailPanel resource={resource} onUpdate={onUpdate} />)

    expect(screen.getByText('Existing takeaway')).toBeInTheDocument()

    const takeawaysField = screen.getByText('Takeaways').closest('.field') as HTMLElement
    fireEvent.change(within(takeawaysField).getByRole('textbox'), {
      target: { value: 'New takeaway' },
    })
    fireEvent.click(within(takeawaysField).getByRole('button', { name: /^add$/i }))

    expect(onUpdate).toHaveBeenCalledWith({ takeaways: ['Existing takeaway', 'New takeaway'] })
  })
})
