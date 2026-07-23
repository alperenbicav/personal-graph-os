import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { SchemaEditor } from './SchemaEditor'
import type { Workspace } from '../types'

vi.mock('../api/client')

const mockedApi = vi.mocked(api)

const workspace: Workspace = {
  id: 'ws-1',
  name: 'Personal',
  created_at: '2026-01-01T00:00:00Z',
  node_types: [
    {
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
      ],
      status_definitions: [
        { id: 'status-todo', name: 'Todo', color_hex: '#999', is_terminal: false, sort_order: 0 },
      ],
    },
  ],
  edge_types: [{ id: 'et-relates', name: 'relates_to', inverse_name: 'relates_to', color_hex: '#999' }],
}

function setup(overrides: { workspace?: Workspace } = {}) {
  const onWorkspaceChange = vi.fn()
  const onError = vi.fn()
  const onClose = vi.fn()
  render(
    <SchemaEditor
      workspace={overrides.workspace ?? workspace}
      onWorkspaceChange={onWorkspaceChange}
      onError={onError}
      onClose={onClose}
    />,
  )
  return { onWorkspaceChange, onError, onClose }
}

describe('SchemaEditor', () => {
  it('sets modal semantics, moves focus inside, and closes on Escape', () => {
    const { onClose } = setup()

    const dialog = screen.getByRole('dialog', { name: /edit schema/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toContainElement(document.activeElement as HTMLElement)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('creates a node type and refreshes the workspace', async () => {
    const createdNodeType = { ...workspace.node_types[0], id: 'nt-idea', name: 'Idea' }
    const refreshedWorkspace = { ...workspace, node_types: [...workspace.node_types, createdNodeType] }
    mockedApi.createNodeType.mockResolvedValue(createdNodeType)
    mockedApi.getWorkspace.mockResolvedValue(refreshedWorkspace)
    const { onWorkspaceChange } = setup()

    fireEvent.change(screen.getByPlaceholderText('New node type name'), {
      target: { value: 'Idea' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(mockedApi.createNodeType).toHaveBeenCalledWith('ws-1', 'Idea'))
    await waitFor(() => expect(onWorkspaceChange).toHaveBeenCalledWith(refreshedWorkspace))
  })

  it('surfaces a server error and keeps the input instead of refreshing on failure', async () => {
    mockedApi.createNodeType.mockRejectedValue(new Error('name already exists'))
    const { onError, onWorkspaceChange } = setup()

    fireEvent.change(screen.getByPlaceholderText('New node type name'), {
      target: { value: 'Task' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('name already exists'))
    expect(onWorkspaceChange).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText('New node type name')).toHaveValue('Task')
  })

  it('expands a node type to show its fields and statuses', () => {
    setup()
    fireEvent.click(screen.getByText('Task'))
    expect(screen.getByText(/priority/)).toBeInTheDocument()
    expect(screen.getByText('Todo')).toBeInTheDocument()
  })

  it('adds a field to an expanded node type', async () => {
    mockedApi.addFieldDefinition.mockResolvedValue({
      id: 'field-new',
      name: 'owner',
      field_type: 'text',
      is_required: false,
      select_options: [],
      description: null,
    })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.change(screen.getByPlaceholderText('Field name'), { target: { value: 'owner' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))

    await waitFor(() =>
      expect(mockedApi.addFieldDefinition).toHaveBeenCalledWith('ws-1', 'nt-task', {
        name: 'owner',
        field_type: 'text',
        is_required: false,
        select_options: [],
      }),
    )
  })

  it('keeps the new-field input when adding a field is rejected', async () => {
    mockedApi.addFieldDefinition.mockRejectedValue(new Error('field conflict'))
    const { onError } = setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.change(screen.getByPlaceholderText('Field name'), { target: { value: 'owner' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('field conflict'))
    expect(screen.getByPlaceholderText('Field name')).toHaveValue('owner')
  })

  it('removes a field', async () => {
    mockedApi.removeFieldDefinition.mockResolvedValue(undefined)
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0])

    await waitFor(() =>
      expect(mockedApi.removeFieldDefinition).toHaveBeenCalledWith('ws-1', 'nt-task', 'field-priority'),
    )
  })

  it('updates a node type name/icon/color', async () => {
    mockedApi.updateNodeType.mockResolvedValue({ ...workspace.node_types[0], name: 'Todo' })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.change(screen.getByLabelText('Task name'), { target: { value: 'Todo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(mockedApi.updateNodeType).toHaveBeenCalledWith('ws-1', 'nt-task', {
        name: 'Todo',
        icon: 'check',
        color_hex: '#2563eb',
      }),
    )
  })

  it('keeps the node type identity form editable when saving is rejected', async () => {
    mockedApi.updateNodeType.mockRejectedValue(new Error('name conflict'))
    const { onError } = setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.change(screen.getByLabelText('Task name'), { target: { value: 'Todo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('name conflict'))
    expect(screen.getByLabelText('Task name')).toHaveValue('Todo')
  })

  it('edits an existing field through its Edit control', async () => {
    mockedApi.updateFieldDefinition.mockResolvedValue({
      ...workspace.node_types[0].field_definitions[0],
      name: 'urgency',
    })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByText('Task'))
    const fieldRow = screen.getByText(/priority/).closest('.schema-row') as HTMLElement
    fireEvent.click(within(fieldRow).getByRole('button', { name: 'Edit' }))
    const nameInput = screen.getByLabelText('priority name')
    fireEvent.change(nameInput, { target: { value: 'urgency' } })
    const fieldForm = nameInput.closest('.schema-create-row') as HTMLElement
    fireEvent.click(within(fieldForm).getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(mockedApi.updateFieldDefinition).toHaveBeenCalledWith('ws-1', 'nt-task', 'field-priority', {
        name: 'urgency',
        field_type: 'text',
        is_required: false,
        select_options: [],
        description: null,
        clear_description: true,
      }),
    )
  })

  it('keeps a field edit form open with the draft when the update is rejected', async () => {
    mockedApi.updateFieldDefinition.mockRejectedValue(new Error('field update failed'))
    const { onError } = setup()

    fireEvent.click(screen.getByText('Task'))
    const fieldRow = screen.getByText(/priority/).closest('.schema-row') as HTMLElement
    fireEvent.click(within(fieldRow).getByRole('button', { name: 'Edit' }))
    const nameInput = screen.getByLabelText('priority name')
    fireEvent.change(nameInput, { target: { value: 'urgency' } })
    const fieldForm = nameInput.closest('.schema-create-row') as HTMLElement
    fireEvent.click(within(fieldForm).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('field update failed'))
    expect(screen.getByLabelText('priority name')).toHaveValue('urgency')
  })

  it('edits an existing status through its Edit control', async () => {
    mockedApi.updateStatusDefinition.mockResolvedValue({
      ...workspace.node_types[0].status_definitions[0],
      name: 'Done',
    })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByText('Task'))
    const statusRow = screen.getByText('Todo').closest('.schema-row') as HTMLElement
    fireEvent.click(within(statusRow).getByRole('button', { name: 'Edit' }))
    const nameInput = screen.getByLabelText('Todo name')
    fireEvent.change(nameInput, { target: { value: 'Done' } })
    const statusForm = nameInput.closest('.schema-create-row') as HTMLElement
    fireEvent.click(within(statusForm).getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(mockedApi.updateStatusDefinition).toHaveBeenCalledWith('ws-1', 'nt-task', 'status-todo', {
        name: 'Done',
        color_hex: '#999',
        is_terminal: false,
        sort_order: 0,
      }),
    )
  })

  it('keeps a status edit form open with the draft when the update is rejected', async () => {
    mockedApi.updateStatusDefinition.mockRejectedValue(new Error('status update failed'))
    const { onError } = setup()

    fireEvent.click(screen.getByText('Task'))
    const statusRow = screen.getByText('Todo').closest('.schema-row') as HTMLElement
    fireEvent.click(within(statusRow).getByRole('button', { name: 'Edit' }))
    const nameInput = screen.getByLabelText('Todo name')
    fireEvent.change(nameInput, { target: { value: 'Done' } })
    const statusForm = nameInput.closest('.schema-create-row') as HTMLElement
    fireEvent.click(within(statusForm).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('status update failed'))
    expect(screen.getByLabelText('Todo name')).toHaveValue('Done')
  })

  it('edits an existing edge type through its Edit control', async () => {
    mockedApi.updateEdgeType.mockResolvedValue({
      ...workspace.edge_types[0],
      name: 'connects_to',
    })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByRole('button', { name: 'Edge types' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('relates_to name'), { target: { value: 'connects_to' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(mockedApi.updateEdgeType).toHaveBeenCalledWith('ws-1', 'et-relates', {
        name: 'connects_to',
        color_hex: '#999',
        inverse_name: 'relates_to',
        clear_inverse_name: false,
      }),
    )
  })

  it('keeps an edge type edit form open with the draft when the update is rejected', async () => {
    mockedApi.updateEdgeType.mockRejectedValue(new Error('edge type update failed'))
    const { onError } = setup()

    fireEvent.click(screen.getByRole('button', { name: 'Edge types' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('relates_to name'), { target: { value: 'connects_to' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('edge type update failed'))
    expect(screen.getByLabelText('relates_to name')).toHaveValue('connects_to')
  })

  it('creates an edge type from the edge types tab', async () => {
    mockedApi.createEdgeType.mockResolvedValue({
      id: 'et-new',
      name: 'supports',
      inverse_name: null,
      color_hex: '#6b7280',
    })
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    setup()

    fireEvent.click(screen.getByRole('button', { name: 'Edge types' }))
    fireEvent.change(screen.getByPlaceholderText('New edge type name'), {
      target: { value: 'supports' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() =>
      expect(mockedApi.createEdgeType).toHaveBeenCalledWith('ws-1', 'supports', undefined),
    )
  })

  it('keeps the new-status input when adding a status is rejected', async () => {
    mockedApi.addStatusDefinition.mockRejectedValue(new Error('status conflict'))
    const { onError } = setup()

    fireEvent.click(screen.getByText('Task'))
    fireEvent.change(screen.getByPlaceholderText('Status name'), { target: { value: 'Blocked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add status' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('status conflict'))
    expect(screen.getByPlaceholderText('Status name')).toHaveValue('Blocked')
  })

  it('keeps the new edge type inputs when creation is rejected', async () => {
    mockedApi.createEdgeType.mockRejectedValue(new Error('edge type conflict'))
    const { onError } = setup()

    fireEvent.click(screen.getByRole('button', { name: 'Edge types' }))
    fireEvent.change(screen.getByPlaceholderText('New edge type name'), {
      target: { value: 'supports' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(onError).toHaveBeenCalledWith('edge type conflict'))
    expect(screen.getByPlaceholderText('New edge type name')).toHaveValue('supports')
  })

  it('calls onClose when the close button is clicked', () => {
    const { onClose } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
