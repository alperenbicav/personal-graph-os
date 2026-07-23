import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { SavedViewsPanel } from './SavedViewsPanel'
import type { SavedView } from '../types'

vi.mock('../api/client')

const mockedApi = vi.mocked(api)

const tableView: SavedView = {
  id: 'view-1',
  workspace_id: 'ws-1',
  name: 'My table view',
  view_kind: 'table',
  filter_definition: {},
  sort_definition: {},
  created_at: '2026-01-01T00:00:00Z',
}

const kanbanView: SavedView = { ...tableView, id: 'view-2', view_kind: 'kanban', name: 'Kanban view' }

beforeEach(() => {
  vi.clearAllMocks()
  mockedApi.listSavedViews.mockResolvedValue([])
})

describe('SavedViewsPanel', () => {
  it('shows an empty state, then only this view kind once loaded', async () => {
    mockedApi.listSavedViews.mockResolvedValue([tableView, kanbanView])
    render(<SavedViewsPanel workspaceId="ws-1" viewKind="table" />)

    expect(await screen.findByText('My table view')).toBeInTheDocument()
    expect(screen.queryByText('Kanban view')).not.toBeInTheDocument()
  })

  it('shows an empty state for no saved views of this kind', async () => {
    render(<SavedViewsPanel workspaceId="ws-1" viewKind="table" />)
    expect(await screen.findByText(/no saved views yet/i)).toBeInTheDocument()
  })

  it('creates a saved view under the current view kind and appends it to the list', async () => {
    mockedApi.createSavedView.mockResolvedValue(tableView)
    render(<SavedViewsPanel workspaceId="ws-1" viewKind="table" />)
    await screen.findByText(/no saved views yet/i)

    fireEvent.change(screen.getByPlaceholderText(/saved view name/i), {
      target: { value: 'My table view' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^save view$/i }))

    expect(await screen.findByText('My table view')).toBeInTheDocument()
    expect(mockedApi.createSavedView).toHaveBeenCalledWith('ws-1', 'My table view', 'table')
    expect(screen.getByPlaceholderText(/saved view name/i)).toHaveValue('')
  })

  it('shows an error and keeps the draft name when creation is rejected', async () => {
    mockedApi.createSavedView.mockRejectedValue(new Error('name already exists'))
    render(<SavedViewsPanel workspaceId="ws-1" viewKind="table" />)
    await screen.findByText(/no saved views yet/i)

    fireEvent.change(screen.getByPlaceholderText(/saved view name/i), {
      target: { value: 'Dup view' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^save view$/i }))

    expect(await screen.findByText('name already exists')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/saved view name/i)).toHaveValue('Dup view')
  })

  it('disables Save view until a name is entered', async () => {
    render(<SavedViewsPanel workspaceId="ws-1" viewKind="table" />)
    await screen.findByText(/no saved views yet/i)

    expect(screen.getByRole('button', { name: /^save view$/i })).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText(/saved view name/i), {
      target: { value: 'x' },
    })
    expect(screen.getByRole('button', { name: /^save view$/i })).toBeEnabled()
  })
})
