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
    repository_label: null,
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
    render(
      <ResearchView dashboard={null} resources={[]} selectedNodeId={null} onSelectNode={vi.fn()} onCreateResource={vi.fn()} onArchiveResource={vi.fn()} onDeleteResource={vi.fn()} />,
    )
    expect(screen.getByText(/loading research dashboard/i)).toBeInTheDocument()
  })

  it('renders every workflow bucket as a filter chip with its count', () => {
    const paper = makeResource('r1', 'n1', 'A paper')
    const dashboard: ResearchDashboard = { ...emptyDashboard, inbox: [paper] }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /research inbox\s*1/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue reading\s*0/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stale resources\s*0/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /needs takeaway\s*0/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /unlinked research\s*0/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /applied sources\s*0/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'A paper' })).toBeInTheDocument()
  })

  it('reports the backing node id when a resource row is clicked', () => {
    const onSelectNode = vi.fn()
    const paper = makeResource('r1', 'n1', 'A paper')
    const dashboard: ResearchDashboard = { ...emptyDashboard, inbox: [paper] }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={onSelectNode}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'A paper' }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })

  it('renders a resource exactly once even when it belongs to multiple guided-workflow buckets', () => {
    // Regression for S6-F03: the same resource can legitimately be both "Inbox" and "Needs
    // Takeaway" server-side, but the canonical master list must still show one selectable row.
    const paper = makeResource('r1', 'n1', 'A paper')
    const dashboard: ResearchDashboard = {
      ...emptyDashboard,
      inbox: [paper],
      needs_takeaway: [paper],
      unlinked: [paper],
    }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    expect(screen.getAllByRole('button', { name: 'A paper' })).toHaveLength(1)
  })

  it('filters the master list by workflow bucket, type, read state, and date', () => {
    const inboxPaper = { ...makeResource('r1', 'n1', 'Inbox paper'), lifecycle_status: 'inbox' as const }
    const readingArticle = {
      ...makeResource('r2', 'n2', 'Reading article'),
      kind: 'article' as const,
      lifecycle_status: 'reading' as const,
    }
    const dashboard: ResearchDashboard = { ...emptyDashboard, inbox: [inboxPaper] }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[inboxPaper, readingArticle]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Inbox paper' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reading article' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /research inbox/i }))
    expect(screen.getByRole('button', { name: 'Inbox paper' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reading article' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^all$/i }))
    fireEvent.change(screen.getByLabelText('Filter by type'), { target: { value: 'article' } })
    expect(screen.queryByRole('button', { name: 'Inbox paper' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reading article' })).toBeInTheDocument()
  })

  it('admits only paper/article kinds, never repositories or other resource kinds (S6-F04 regression)', () => {
    const paper = makeResource('r1', 'n1', 'A paper')
    const repository = { ...makeResource('r2', 'n2', 'A repository'), kind: 'github_repository' as const }
    const documentation = { ...makeResource('r3', 'n3', 'Some docs'), kind: 'documentation' as const }
    const dashboard: ResearchDashboard = {
      ...emptyDashboard,
      inbox: [paper, repository, documentation],
    }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[paper, repository, documentation]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'A paper' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'A repository' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Some docs' })).not.toBeInTheDocument()
    // The bucket chip's count must match what it actually admits, not the raw dashboard array.
    expect(screen.getByRole('button', { name: /research inbox\s*1/i })).toBeInTheDocument()
  })

  it('never nests the canonical-source link inside the row-selecting button (S6-F02 regression)', () => {
    const paper = { ...makeResource('r1', 'n1', 'A paper'), source_url: 'https://example.com/paper' }
    const dashboard: ResearchDashboard = { ...emptyDashboard, inbox: [paper] }
    render(
      <ResearchView
        dashboard={dashboard}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    const selectButton = screen.getByRole('button', { name: 'A paper' })
    const sourceLink = screen.getByRole('link', { name: /source/i })
    expect(selectButton.contains(sourceLink)).toBe(false)
    expect(sourceLink.closest('button')).toBeNull()
  })

  it('creates a paper or article through its own typed create form', async () => {
    const onCreateResource = vi.fn().mockResolvedValue(makeResource('r2', 'n2', 'New paper'))
    render(
      <ResearchView
        dashboard={emptyDashboard}
        resources={[]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={onCreateResource}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '+ New paper/article' }))
    fireEvent.change(screen.getByPlaceholderText('Title'), { target: { value: 'New paper' } })
    fireEvent.change(screen.getByPlaceholderText(/source url/i), {
      target: { value: 'https://arxiv.org/abs/2401.00001' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    await Promise.resolve()
    expect(onCreateResource).toHaveBeenCalledWith(
      'New paper',
      'https://arxiv.org/abs/2401.00001',
      'paper',
    )
  })

  it('disables add until title and source are both entered', () => {
    render(
      <ResearchView
        dashboard={emptyDashboard}
        resources={[]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '+ New paper/article' }))
    expect(screen.getByRole('button', { name: /^add$/i })).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText('Title'), { target: { value: 'New paper' } })
    expect(screen.getByRole('button', { name: /^add$/i })).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText(/source url/i), {
      target: { value: 'https://arxiv.org/abs/2401.00001' },
    })
    expect(screen.getByRole('button', { name: /^add$/i })).not.toBeDisabled()
  })

  it('archives a non-archived row without a confirmation step', async () => {
    const paper = makeResource('r1', 'n1', 'A paper')
    const onArchiveResource = vi.fn().mockResolvedValue(undefined)
    render(
      <ResearchView
        dashboard={{ ...emptyDashboard, inbox: [paper] }}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={onArchiveResource}
        onDeleteResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Archive' }))
    await Promise.resolve()

    expect(onArchiveResource).toHaveBeenCalledWith('r1')
  })

  it('hides the archive action for an already archived row', () => {
    const archived = { ...makeResource('r1', 'n1', 'Old paper'), lifecycle_status: 'archived' as const }
    render(
      <ResearchView
        dashboard={{ ...emptyDashboard, unlinked: [archived] }}
        resources={[archived]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
  })

  it('requires an explicit confirmation before hard-deleting a row', async () => {
    const paper = makeResource('r1', 'n1', 'A paper')
    const onDeleteResource = vi.fn().mockResolvedValue(undefined)
    render(
      <ResearchView
        dashboard={{ ...emptyDashboard, inbox: [paper] }}
        resources={[paper]}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onCreateResource={vi.fn()}
        onArchiveResource={vi.fn()}
        onDeleteResource={onDeleteResource}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Delete…' }))
    expect(onDeleteResource).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Delete forever' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Delete forever' }))
    await Promise.resolve()

    expect(onDeleteResource).toHaveBeenCalledWith('r1')
  })
})
