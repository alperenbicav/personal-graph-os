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
    onCreateWorkItem: vi.fn().mockResolvedValue(makeWorkItem({ id: 'wi-3', title: 'New epic' })),
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

  it('creates a work item through the modal with kind and title', async () => {
    const props = baseProps()
    render(<TasksView {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: '+ New work item' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New epic' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() =>
      expect(props.onCreateWorkItem).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_id: 'ws-1',
          title: 'New epic',
          kind: 'epic',
          work_type: 'feature',
        }),
      ),
    )
  })

  it('offers legal parents only for the child kind the service accepts', async () => {
    const props = baseProps()
    render(<TasksView {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: '+ New work item' }))
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'task' } })

    const parentSelect = screen.getByLabelText('Parent')
    const options = Array.from(parentSelect.querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    expect(options).toContain('A story')
    expect(options).not.toContain('An epic')
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

    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() =>
      expect(props.onUpdateChecklistItem).toHaveBeenCalledWith(
        'cli-1',
        expect.objectContaining({ is_completed: true }),
      ),
    )
  })

  it('links a Wiki page through the document selector', async () => {
    const props = baseProps()
    render(<StatefulTasksView {...props} />)

    fireEvent.click((await screen.findAllByText('An epic'))[0])
    await screen.findByLabelText('Work item title')

    fireEvent.change(screen.getByLabelText('Link a Wiki page'), { target: { value: 'doc-1' } })
    fireEvent.click(screen.getByRole('button', { name: /^add link$/i }))

    await waitFor(() => expect(props.onAttachDocument).toHaveBeenCalledWith('wi-1', 'doc-1'))
    await waitFor(() => expect(props.onLoadDetail).toHaveBeenCalledTimes(2))
  })

  it('assigns and clears a repository through the selector', async () => {
    const props = baseProps()
    render(<StatefulTasksView {...props} />)

    fireEvent.click((await screen.findAllByText('An epic'))[0])
    await screen.findByLabelText('Work item title')

    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'node-repo' } })
    await waitFor(() =>
      expect(props.onUpdateWorkItem).toHaveBeenCalledWith(
        'wi-1',
        expect.objectContaining({ repository_node_id: 'node-repo' }),
      ),
    )
  })

  it('shows the freshly selected item\'s assignee/blockers/progress', async () => {
    const props = baseProps()
    props.workItems = [
      makeWorkItem({ id: 'wi-1', node_id: 'node-1', title: 'Epic A', assignee: 'Ada', blockers: 'B1', progress_percent: 20 }),
      makeWorkItem({ id: 'wi-2', node_id: 'node-2', kind: 'story', parent_id: 'wi-1', title: 'Story B', assignee: 'Lin', blockers: 'B2', progress_percent: 60 }),
    ]
    render(<StatefulTasksView {...props} />)

    fireEvent.click((await screen.findAllByText('Epic A'))[0])
    await screen.findByLabelText('Work item title')
    expect(screen.getByLabelText('Assignee')).toHaveValue('Ada')
    expect(screen.getByLabelText('Blockers')).toHaveValue('B1')
    expect(screen.getByLabelText('Progress (%)')).toHaveValue(20)

    fireEvent.click(screen.getByRole('button', { name: '← Back' }))
    fireEvent.click((await screen.findAllByText('Story B'))[0])
    await screen.findByLabelText('Work item title')
    expect(screen.getByLabelText('Assignee')).toHaveValue('Lin')
    expect(screen.getByLabelText('Blockers')).toHaveValue('B2')
    expect(screen.getByLabelText('Progress (%)')).toHaveValue(60)
  })
})
