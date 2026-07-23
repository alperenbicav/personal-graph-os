import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { Inspector } from './Inspector'
import type { GraphNode, NodeType } from '../types'

vi.mock('../api/client')

const mockedApi = vi.mocked(api)

const nodeType: NodeType = {
  id: 'nt-task',
  name: 'Task',
  icon: 'check',
  color_hex: '#2563eb',
  field_definitions: [
    {
      id: 'field-priority',
      name: 'priority',
      field_type: 'text',
      is_required: false,
      select_options: [],
      description: null,
    },
    {
      id: 'field-points',
      name: 'points',
      field_type: 'number',
      is_required: true,
      select_options: [],
      description: null,
    },
    {
      id: 'field-blocked',
      name: 'blocked',
      field_type: 'boolean',
      is_required: false,
      select_options: [],
      description: null,
    },
    {
      id: 'field-size',
      name: 'size',
      field_type: 'select',
      is_required: false,
      select_options: ['small', 'medium', 'large'],
      description: null,
    },
    {
      id: 'field-source',
      name: 'source',
      field_type: 'url',
      is_required: false,
      select_options: [],
      description: null,
    },
    {
      id: 'field-repository',
      name: 'repository',
      field_type: 'object_reference',
      is_required: false,
      select_options: [],
      description: null,
    },
  ],
  status_definitions: [
    { id: 'status-todo', name: 'Todo', color_hex: '#999', is_terminal: false, sort_order: 0 },
    { id: 'status-done', name: 'Done', color_hex: '#0a0', is_terminal: true, sort_order: 1 },
  ],
}

const node: GraphNode = {
  id: 'node-1',
  workspace_id: 'ws-1',
  node_type_id: 'nt-task',
  title: 'Write report',
  body: 'Cover Q3 results.',
  status_id: 'status-todo',
  field_values: { 'field-priority': 'high', 'field-points': 3, 'field-blocked': true },
  is_archived: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const referenceableNodes = [
  { id: 'node-2', title: 'Target A' },
  { id: 'node-3', title: 'Target B' },
]

function renderInspector(overrides: Partial<Parameters<typeof Inspector>[0]> = {}) {
  return render(
    <Inspector
      node={node}
      nodeType={nodeType}
      relations={[]}
      referenceableNodes={referenceableNodes}
      onChangeStatus={vi.fn()}
      onChangeBody={vi.fn().mockResolvedValue(true)}
      onChangeField={vi.fn().mockResolvedValue(true)}
      onArchive={vi.fn()}
      {...overrides}
    />,
  )
}

describe('Inspector', () => {
  beforeEach(() => {
    mockedApi.listAttachments.mockResolvedValue([])
    mockedApi.listFileReferences.mockResolvedValue([])
  })

  it('shows an empty-state message when nothing is selected', () => {
    render(
      <Inspector
        node={null}
        nodeType={undefined}
        relations={[]}
        referenceableNodes={[]}
        onChangeStatus={vi.fn()}
        onChangeBody={vi.fn()}
        onChangeField={vi.fn()}
        onArchive={vi.fn()}
      />,
    )
    expect(screen.getByText(/select a node/i)).toBeInTheDocument()
  })

  it('renders the selected node title, status, description, and field values', () => {
    renderInspector()
    expect(screen.getByText('Write report')).toBeInTheDocument()
    expect(screen.getByLabelText('Description')).toHaveValue('Cover Q3 results.')
    expect(screen.getByDisplayValue('high')).toBeInTheDocument()
    expect(screen.getByDisplayValue('3')).toBeInTheDocument()
    expect(screen.getByLabelText('Status')).toHaveValue('status-todo')
  })

  it('saves the description on blur when it changed, and reverts on a rejected save', async () => {
    const onChangeBody = vi.fn().mockResolvedValue(true)
    renderInspector({ onChangeBody })
    const description = screen.getByLabelText('Description')

    fireEvent.change(description, { target: { value: 'Updated description.' } })
    fireEvent.blur(description)

    await vi.waitFor(() => expect(onChangeBody).toHaveBeenCalledWith('Updated description.'))
  })

  it('does not call onChangeBody on blur when the description is unchanged', () => {
    const onChangeBody = vi.fn()
    renderInspector({ onChangeBody })
    const description = screen.getByLabelText('Description')

    fireEvent.blur(description)

    expect(onChangeBody).not.toHaveBeenCalled()
  })

  it('calls onChangeStatus when the status select changes', () => {
    const onChangeStatus = vi.fn()
    renderInspector({ onChangeStatus })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'status-done' } })
    expect(onChangeStatus).toHaveBeenCalledWith('status-done')
  })

  it('calls onArchive when the archive button is clicked', () => {
    const onArchive = vi.fn()
    renderInspector({ onArchive })
    fireEvent.click(screen.getByRole('button', { name: /archive/i }))
    expect(onArchive).toHaveBeenCalledTimes(1)
  })

  it('renders relation rows', () => {
    renderInspector({
      relations: [{ edgeTypeName: 'implements', otherNodeTitle: 'Decision X' }],
    })
    expect(screen.getByText('implements')).toBeInTheDocument()
    expect(screen.getByText('Decision X')).toBeInTheDocument()
  })

  it('commits a text field on blur', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const input = screen.getByDisplayValue('high')
    fireEvent.change(input, { target: { value: 'low' } })
    fireEvent.blur(input)
    expect(onChangeField).toHaveBeenCalledWith('field-priority', 'low')
  })

  it('commits a number field as a parsed number', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const input = screen.getByDisplayValue('3')
    fireEvent.change(input, { target: { value: '5' } })
    fireEvent.blur(input)
    expect(onChangeField).toHaveBeenCalledWith('field-points', 5)
  })

  it('rejects a non-numeric value for a number field without committing', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const input = screen.getByDisplayValue('3')
    fireEvent.change(input, { target: { value: 'abc' } })
    fireEvent.blur(input)
    expect(onChangeField).not.toHaveBeenCalled()
    expect(screen.getByText(/must be a number/i)).toBeInTheDocument()
  })

  it('flags an explicit clear of a required field without committing', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const input = screen.getByDisplayValue('3')
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    expect(onChangeField).not.toHaveBeenCalled()
    expect(screen.getByText(/can't be cleared/i)).toBeInTheDocument()
  })

  it('commits a boolean field immediately on toggle', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)
    expect(onChangeField).toHaveBeenCalledWith('field-blocked', false)
  })

  it('commits a select field immediately on change', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const select = screen.getByLabelText('size')
    fireEvent.change(select, { target: { value: 'large' } })
    expect(onChangeField).toHaveBeenCalledWith('field-size', 'large')
  })

  it('rejects a non-absolute-URL value for a url field without committing', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    const input = screen.getByLabelText('source')
    fireEvent.change(input, { target: { value: 'definitely not a URL' } })
    fireEvent.blur(input)
    expect(onChangeField).not.toHaveBeenCalled()
    expect(screen.getByText(/absolute http/i)).toBeInTheDocument()
  })

  it('commits a valid absolute URL for a url field', () => {
    const onChangeField = vi.fn().mockResolvedValue(true)
    renderInspector({ onChangeField })
    const input = screen.getByLabelText('source')
    fireEvent.change(input, { target: { value: 'https://example.com/paper' } })
    fireEvent.blur(input)
    expect(onChangeField).toHaveBeenCalledWith('field-source', 'https://example.com/paper')
  })

  it('reverts a text field to the canonical value when the save is rejected', async () => {
    const onChangeField = vi.fn().mockResolvedValue(false)
    renderInspector({ onChangeField })
    const input = screen.getByDisplayValue('high')
    fireEvent.change(input, { target: { value: 'low' } })
    fireEvent.blur(input)
    expect(onChangeField).toHaveBeenCalledWith('field-priority', 'low')
    await screen.findByDisplayValue('high')
    expect(screen.queryByDisplayValue('low')).not.toBeInTheDocument()
  })

  it('renders an object_reference field as a node selector, not a raw id input', () => {
    renderInspector()
    const select = screen.getByLabelText('repository')
    expect(select.tagName).toBe('SELECT')
    expect(screen.getByRole('option', { name: 'Target A' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Target B' })).toBeInTheDocument()
  })

  it('commits the selected node id for an object_reference field', () => {
    const onChangeField = vi.fn()
    renderInspector({ onChangeField })
    fireEvent.change(screen.getByLabelText('repository'), { target: { value: 'node-3' } })
    expect(onChangeField).toHaveBeenCalledWith('field-repository', 'node-3')
  })

  it('flags a stale object_reference value that no longer matches a real node', () => {
    const staleNode: GraphNode = {
      ...node,
      field_values: { ...node.field_values, 'field-repository': 'node-deleted' },
    }
    renderInspector({ node: staleNode })
    expect(screen.getByLabelText('repository')).toHaveValue('node-deleted')
    expect(screen.getByText(/no longer exists/i)).toBeInTheDocument()
  })
})
