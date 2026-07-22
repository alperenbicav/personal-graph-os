import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ResearchView } from './ResearchView'
import type { ResearchDashboard, Resource } from '../types'

function makeResource(id: string, nodeId: string, title: string): Resource {
  return {
    id,
    workspace_id: 'ws-1',
    node_id: nodeId,
    kind: 'paper',
    canonical_identifier: `id:${id}`,
    source_url: null,
    lifecycle_status: 'inbox',
    next_action: null,
    next_action_dismissed: false,
    open_questions: [],
    takeaways: [],
    progress_percent: null,
    review_at: null,
    last_activity_at: '2026-01-01T00:00:00Z',
    title,
    body: '',
  }
}

const emptyDashboard: ResearchDashboard = {
  inbox: [],
  continue_reading: [],
  stale: [],
  needs_takeaway: [],
  unlinked: [],
  applied: [],
}

describe('ResearchView', () => {
  it('shows a loading message when the dashboard has not loaded yet', () => {
    render(<ResearchView dashboard={null} selectedNodeId={null} onSelectNode={vi.fn()} />)
    expect(screen.getByText(/loading research dashboard/i)).toBeInTheDocument()
  })

  it('renders every built-in section with its count', () => {
    const dashboard: ResearchDashboard = {
      ...emptyDashboard,
      inbox: [makeResource('r1', 'n1', 'A paper')],
    }
    render(<ResearchView dashboard={dashboard} selectedNodeId={null} onSelectNode={vi.fn()} />)

    expect(screen.getByText('Research Inbox')).toBeInTheDocument()
    expect(screen.getByText('Continue Reading')).toBeInTheDocument()
    expect(screen.getByText('Stale Resources')).toBeInTheDocument()
    expect(screen.getByText('Needs Takeaway')).toBeInTheDocument()
    expect(screen.getByText('Unlinked Research')).toBeInTheDocument()
    expect(screen.getByText('Applied Sources')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /a paper/i })).toBeInTheDocument()
  })

  it('reports the backing node id when a resource row is clicked', () => {
    const onSelectNode = vi.fn()
    const dashboard: ResearchDashboard = {
      ...emptyDashboard,
      inbox: [makeResource('r1', 'n1', 'A paper')],
    }
    render(<ResearchView dashboard={dashboard} selectedNodeId={null} onSelectNode={onSelectNode} />)

    fireEvent.click(screen.getByRole('button', { name: /a paper/i }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })
})
