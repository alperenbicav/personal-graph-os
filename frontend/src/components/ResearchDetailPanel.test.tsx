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
  review_at: null,
  last_activity_at: '2026-01-01T00:00:00Z',
  title: 'A paper',
  body: '',
}

describe('ResearchDetailPanel', () => {
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
