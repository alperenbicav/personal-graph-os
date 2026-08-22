import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RepositoriesView } from './RepositoriesView'
import type { Resource } from '../types'

function makeRepository(
  id: string,
  nodeId: string,
  title: string,
  repositoryLabel: Resource['repository_label'] = null,
): Resource {
  return {
    id,
    workspace_id: 'ws-1',
    node_id: nodeId,
    kind: 'github_repository',
    canonical_identifier: `github:${id}`,
    source_url: `https://github.com/example/${id}`,
    lifecycle_status: 'inbox',
    next_action: null,
    next_action_dismissed: false,
    open_questions: [],
    takeaways: [],
    progress_percent: null,
    review_at: null,
    last_activity_at: '2026-01-01T00:00:00Z',
    repository_label: repositoryLabel,
    title,
    body: '',
  }
}

describe('RepositoriesView', () => {
  it('only shows github_repository resources', () => {
    const resources = [
      makeRepository('r1', 'n1', 'apilex-agent', 'apilex'),
      { ...makeRepository('r2', 'n2', 'A paper'), kind: 'paper' as const },
    ]
    render(
      <RepositoriesView
        resources={resources}
        selectedNodeId={null}
        onClearSelection={() => undefined}
        onUpdateDetail={vi.fn().mockResolvedValue(true)}
        onSelectNode={vi.fn()}
        onSetLabel={vi.fn()}
        onCreateResource={vi.fn()}
      />,
    )

    expect(screen.getByText('apilex-agent')).toBeInTheDocument()
    expect(screen.queryByText('A paper')).not.toBeInTheDocument()
  })

  it('filters by ownership label', () => {
    const resources = [
      makeRepository('r1', 'n1', 'apilex-agent', 'apilex'),
      makeRepository('r2', 'n2', 'my-side-project', 'personal'),
    ]
    render(
      <RepositoriesView
        resources={resources}
        selectedNodeId={null}
        onClearSelection={() => undefined}
        onUpdateDetail={vi.fn().mockResolvedValue(true)}
        onSelectNode={vi.fn()}
        onSetLabel={vi.fn()}
        onCreateResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Personal' }))
    expect(screen.getByText('my-side-project')).toBeInTheDocument()
    expect(screen.queryByText('apilex-agent')).not.toBeInTheDocument()
  })

  it('reports the backing node id when a repository row is clicked', () => {
    const onSelectNode = vi.fn()
    const resources = [makeRepository('r1', 'n1', 'apilex-agent')]
    render(
      <RepositoriesView
        resources={resources}
        selectedNodeId={null}
        onClearSelection={() => undefined}
        onUpdateDetail={vi.fn().mockResolvedValue(true)}
        onSelectNode={onSelectNode}
        onSetLabel={vi.fn()}
        onCreateResource={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'apilex-agent' }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })

  it('calls onSetLabel when the ownership label select changes', () => {
    const onSetLabel = vi.fn()
    const resources = [makeRepository('r1', 'n1', 'apilex-agent')]
    render(
      <RepositoriesView
        resources={resources}
        selectedNodeId={null}
        onClearSelection={() => undefined}
        onUpdateDetail={vi.fn().mockResolvedValue(true)}
        onSelectNode={vi.fn()}
        onSetLabel={onSetLabel}
        onCreateResource={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Ownership label for apilex-agent'), {
      target: { value: 'liked_external' },
    })
    expect(onSetLabel).toHaveBeenCalledWith('r1', 'liked_external')
  })

  it('creates a repository through its own typed create form', async () => {
    const onCreateResource = vi.fn().mockResolvedValue(makeRepository('r2', 'n2', 'new-repo'))
    render(
      <RepositoriesView
        resources={[]}
        selectedNodeId={null}
        onClearSelection={() => undefined}
        onUpdateDetail={vi.fn().mockResolvedValue(true)}
        onSelectNode={vi.fn()}
        onSetLabel={vi.fn()}
        onCreateResource={onCreateResource}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '+ New repository' }))
    fireEvent.change(screen.getByPlaceholderText('Title'), { target: { value: 'new-repo' } })
    fireEvent.change(screen.getByLabelText('GitHub URL'), {
      target: { value: 'https://github.com/acme/new-repo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    await Promise.resolve()
    expect(onCreateResource).toHaveBeenCalledWith('new-repo', 'https://github.com/acme/new-repo')
  })
})
