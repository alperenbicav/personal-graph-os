import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CommandPalette } from './CommandPalette'
import type { GraphNode, Resource, WikiDocument, WorkItem, Canvas } from '../types'

const mockDocuments: WikiDocument[] = [
  {
    id: 'doc-1',
    workspace_id: 'ws-1',
    title: 'Architecture Overview',
    kind: 'documentation',
    collection_id: null,
    tag_ids: [],
    source: 'manual',
    source_reference: null,
    is_archived: false,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  },
]

const mockWorkItems: WorkItem[] = [
  {
    id: 'work-1',
    workspace_id: 'ws-1',
    node_id: 'node-task-1',
    kind: 'task',
    work_type: 'feature',
    title: 'Implement slash commands',
    body: 'Add support for slash commands',
    source: 'manual',
    status: 'in_progress',
    priority: 'high',
    due_date: '2026-09-01',
    assignee: 'alperen',
    blockers: null,
    progress_percent: 50,
    repository_node_id: null,
    parent_id: null,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  },
]

const mockResources: Resource[] = [
  {
    id: 'res-1',
    workspace_id: 'ws-1',
    node_id: 'node-res-1',
    kind: 'paper',
    canonical_identifier: 'arxiv:1706.03762',
    source_url: 'https://arxiv.org',
    lifecycle_status: 'reading',
    title: 'Attention Is All You Need',
    body: 'Foundational paper',
    next_action: null,
    next_action_dismissed: false,
    open_questions: [],
    takeaways: [],
    progress_percent: 40,
    review_at: null,
    last_activity_at: '2026-08-23T00:00:00Z',
    repository_label: null,
  },
]

const mockNodes: GraphNode[] = []
const mockCanvases: Canvas[] = [
  {
    id: 'canvas-1',
    workspace_id: 'ws-1',
    name: 'Main Board',
    created_at: '2026-08-20T00:00:00Z',
  },
]

describe('CommandPalette', () => {
  it('does not render when isOpen is false', () => {
    render(
      <CommandPalette
        isOpen={false}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
        onSelectDocument={vi.fn()}
        onSelectWorkItem={vi.fn()}
        onSelectResourceNode={vi.fn()}
        onSelectNode={vi.fn()}
        onSelectCanvas={vi.fn()}
        documents={mockDocuments}
        workItems={mockWorkItems}
        resources={mockResources}
        nodes={mockNodes}
        canvases={mockCanvases}
      />,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders commands and items when isOpen is true', () => {
    render(
      <CommandPalette
        isOpen={true}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
        onSelectDocument={vi.fn()}
        onSelectWorkItem={vi.fn()}
        onSelectResourceNode={vi.fn()}
        onSelectNode={vi.fn()}
        onSelectCanvas={vi.fn()}
        documents={mockDocuments}
        workItems={mockWorkItems}
        resources={mockResources}
        nodes={mockNodes}
        canvases={mockCanvases}
      />,
    )

    expect(screen.getByRole('dialog', { name: /command palette/i })).toBeInTheDocument()
    expect(screen.getByText('Go to Graph')).toBeInTheDocument()
    expect(screen.getByText('Go to Tasks')).toBeInTheDocument()
    expect(screen.getByText('Architecture Overview')).toBeInTheDocument()
    expect(screen.getByText('Implement slash commands')).toBeInTheDocument()
    expect(screen.getByText('Attention Is All You Need')).toBeInTheDocument()
  })

  it('filters items by query and navigates on Enter', () => {
    const onNavigate = vi.fn()
    const onSelectWorkItem = vi.fn()
    const onClose = vi.fn()

    render(
      <CommandPalette
        isOpen={true}
        onClose={onClose}
        onNavigate={onNavigate}
        onSelectDocument={vi.fn()}
        onSelectWorkItem={onSelectWorkItem}
        onSelectResourceNode={vi.fn()}
        onSelectNode={vi.fn()}
        onSelectCanvas={vi.fn()}
        documents={mockDocuments}
        workItems={mockWorkItems}
        resources={mockResources}
        nodes={mockNodes}
        canvases={mockCanvases}
      />,
    )

    const input = screen.getByRole('textbox', { name: /command palette/i })
    fireEvent.change(input, { target: { value: 'slash commands' } })

    expect(screen.getByText('Implement slash commands')).toBeInTheDocument()
    expect(screen.queryByText('Architecture Overview')).not.toBeInTheDocument()

    // Press Enter to select
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledWith('tasks')
    expect(onSelectWorkItem).toHaveBeenCalledWith('work-1')
    expect(onClose).toHaveBeenCalled()
  })

  it('closes on Escape or backdrop click', () => {
    const onClose = vi.fn()
    render(
      <CommandPalette
        isOpen={true}
        onClose={onClose}
        onNavigate={vi.fn()}
        onSelectDocument={vi.fn()}
        onSelectWorkItem={vi.fn()}
        onSelectResourceNode={vi.fn()}
        onSelectNode={vi.fn()}
        onSelectCanvas={vi.fn()}
        documents={mockDocuments}
        workItems={mockWorkItems}
        resources={mockResources}
        nodes={mockNodes}
        canvases={mockCanvases}
      />,
    )

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})
