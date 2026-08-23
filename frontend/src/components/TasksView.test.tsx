import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { TasksView } from './TasksView'
import type {
  WikiDocument,
  WorkItem,
  WorkItemChecklistItem,
  WorkItemDetail,
} from '../types'

function makeWorkItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 'wi-1',
    workspace_id: 'ws-1',
    node_id: 'node-1',
    kind: 'epic',
    work_type: 'feature',
    status: 'backlog',
    parent_id: null,
    repository_node_id: null,
    priority: null,
    due_date: null,
    assignee: null,
    blockers: null,
    progress_percent: null,
    source: 'manual',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    title: 'An epic',
    body: 'Epic body',
    ...overrides,
  }
}

function makeDetail(overrides: Partial<WorkItemDetail> = {}): WorkItemDetail {
  return {
    work_item: makeWorkItem(),
    checklist_items: [],
    linked_documents: [],
    ...overrides,
  }
}

function makeDocument(overrides: Partial<WikiDocument> = {}): WikiDocument {
  return {
    id: 'doc-1',
    workspace_id: 'ws-1',
    kind: 'note',
    title: 'Plan page',
    collection_id: null,
    tag_ids: [],
    is_archived: false,
    source: 'manual',
    source_reference: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeChecklistItem(overrides: Partial<WorkItemChecklistItem> = {}): WorkItemChecklistItem {
  return {
    id: 'cli-1',
    work_item_id: 'wi-1',
    position: 0,
    label: 'First step',
    is_completed: false,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function baseProps() {
  const story = makeWorkItem({
    id: 'wi-2',
    node_id: 'node-2',
    kind: 'story',
    parent_id: 'wi-1',
    title: 'A story',
  })
  return {
    workspaceId: 'ws-1',
    workItems: [makeWorkItem(), story],
    documents: [makeDocument()],
    repositories: [{ node_id: 'node-repo', title: 'apilex-agent' }],
    selectedWorkItemId: null,
    onSelectWorkItem: vi.fn(),
    onCreateWorkItem: vi.fn().mockResolvedValue(makeWorkItem({ id: 'wi-3', title: 'New task' })),
    onUpdateWorkItem: vi.fn().mockResolvedValue(makeWorkItem({ status: 'in_progress' })),
    onLoadDetail: vi.fn().mockResolvedValue(makeDetail()),
    onEditBody: vi.fn().mockResolvedValue(undefined),
    onAddChecklistItem: vi
      .fn()
      .mockResolvedValue(makeChecklistItem({ id: 'cli-1', label: 'First step' })),
    onUpdateChecklistItem: vi.fn().mockResolvedValue(makeChecklistItem({ is_completed: true })),
    onRemoveChecklistItem: vi.fn().mockResolvedValue(undefined),
    onReorderChecklistItems: vi.fn().mockResolvedValue([]),
    onAttachDocument: vi.fn().mockResolvedValue(undefined),
    onDetachDocument: vi.fn().mockResolvedValue(undefined),
    onRenameWorkItem: vi.fn().mockResolvedValue(undefined),
  }
}

type TasksProps = ReturnType<typeof baseProps>

function StatefulTasksView(props: TasksProps) {
  const [selectedWorkItemId, setSelectedWorkItemId] = useState<string | null>(null)
  return (
    <TasksView
      {...props}
      selectedWorkItemId={selectedWorkItemId}
      onSelectWorkItem={(id) => {
        setSelectedWorkItemId(id)
        props.onSelectWorkItem(id)
      }}
    />
  )
}

describe('TasksView', () => {
  it('renders the work item tree and loads detail on selection', async () => {
    const props = baseProps()
    render(<StatefulTasksView {...props} />)

    const epicRow = (await screen.findAllByText('An epic'))[0]
    const storyRow = (await screen.findAllByText('A story'))[0]
    expect(storyRow).toBeInTheDocument()

    fireEvent.click(epicRow)
    await waitFor(() => expect(props.onLoadDetail).toHaveBeenCalledWith('wi-1'))
    expect(((await screen.findByLabelText('Work item title')) as HTMLInputElement).value).toBe(
      'An epic',
    )
  })

  it('creates a work item through column quick-add composer in board view', async () => {
    const props = baseProps()
    render(<TasksView {...props} />)

    const quickAddInput = screen.getByLabelText('Quick add to Backlog')
    fireEvent.change(quickAddInput, { target: { value: 'New task in backlog' } })
    fireEvent.keyDown(quickAddInput, { key: 'Enter', code: 'Enter' })

    await waitFor(() =>
      expect(props.onCreateWorkItem).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_id: 'ws-1',
          title: 'New task in backlog',
          status: 'backlog',
          kind: 'task',
          work_type: 'feature',
        }),
      ),
    )
  })

  it('creates a work item through top quick-add composer in list view', async () => {
    const props = baseProps()
    render(<TasksView {...props} />)

    // Switch to List mode
    fireEvent.click(screen.getByRole('radio', { name: 'List' }))

    const listQuickAdd = screen.getByLabelText('Add a work item to list')
    fireEvent.change(listQuickAdd, { target: { value: 'New task via list' } })
    fireEvent.keyDown(listQuickAdd, { key: 'Enter', code: 'Enter' })

    await waitFor(() =>
      expect(props.onCreateWorkItem).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_id: 'ws-1',
          title: 'New task via list',
          status: 'backlog',
          kind: 'task',
        }),
      ),
    )
  })

  it('commits a status change through the update handler', async () => {
    const props = baseProps()
    render(<StatefulTasksView {...props} />)

    fireEvent.click((await screen.findAllByText('An epic'))[0])
    await screen.findByLabelText('Work item title')

    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'in_progress' } })
    await waitFor(() =>
      expect(props.onUpdateWorkItem).toHaveBeenCalledWith(
        'wi-1',
        expect.objectContaining({ status: 'in_progress' }),
      ),
    )
  })

  it('adds and toggles checklist items', async () => {
    const props = baseProps()
    render(<StatefulTasksView {...props} />)

    fireEvent.click((await screen.findAllByText('An epic'))[0])
    await screen.findByLabelText('Work item title')

    const input = screen.getByLabelText('New checklist item')
    fireEvent.change(input, { target: { value: 'First step' } })
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }))

    await waitFor(() => expect(props.onAddChecklistItem).toHaveBeenCalledWith('wi-1', 'First step'))
    expect(await screen.findByText('First step')).toBeInTheDocument()
  })
})
