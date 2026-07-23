import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as api from './api/client'
import { clearSession, commitToken, getToken } from './api/session'
import type { Workspace } from './types'

vi.mock('./api/client', async (importOriginal) => {
  // Keeps the real `ApiError` class (so `instanceof` checks in UnlockScreen still work)
  // while every function export becomes a mock, matching the rest of this test suite.
  const actual = await importOriginal<typeof import('./api/client')>()
  return {
    ...actual,
    getWorkspace: vi.fn(),
    exportWorkspace: vi.fn(),
    listNodes: vi.fn(),
    captureNode: vi.fn(),
    updateNode: vi.fn(),
    archiveNode: vi.fn(),
    listEdges: vi.fn(),
    connectEdge: vi.fn(),
    listCanvases: vi.fn(),
    createCanvas: vi.fn(),
    listPlacements: vi.fn(),
    placeNode: vi.fn(),
    updatePlacement: vi.fn(),
    listResources: vi.fn(),
    listAttachments: vi.fn(),
    listFileReferences: vi.fn(),
  }
})

const mockedApi = vi.mocked(api)

const workspace: Workspace = {
  id: 'ws-1',
  name: 'Personal',
  created_at: '2026-01-01T00:00:00Z',
  node_types: [],
  edge_types: [],
}

function unlockWith(token: string) {
  fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: token } })
  fireEvent.click(screen.getByRole('button', { name: /unlock/i }))
}

beforeEach(() => {
  mockedApi.listEdges.mockResolvedValue([])
  mockedApi.listCanvases.mockResolvedValue([])
  mockedApi.listNodes.mockResolvedValue([])
  mockedApi.listPlacements.mockResolvedValue([])
  mockedApi.listResources.mockResolvedValue([])
  mockedApi.listAttachments.mockResolvedValue([])
  mockedApi.listFileReferences.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
  clearSession()
})

describe('unlock screen', () => {
  it('is shown instead of the dashboard when no tab session exists yet', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: /personal graph os/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
    expect(mockedApi.getWorkspace).not.toHaveBeenCalled()
  })

  it('rejects a wrong token, stays locked, and persists nothing', async () => {
    mockedApi.getWorkspace.mockRejectedValueOnce(new api.ApiError(401, 'Invalid bearer token'))
    render(<App />)

    unlockWith('wrong-token')

    await screen.findByText(/token was rejected/i)
    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
    expect(getToken()).toBeNull()
  })

  it('reports a network/backend failure distinctly from a rejected token, and persists nothing (ST08-F01)', async () => {
    mockedApi.getWorkspace.mockRejectedValueOnce(new Error('Failed to fetch'))
    render(<App />)

    unlockWith('some-token')

    await screen.findByText(/is it running/i)
    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
    // The candidate was only ever held in memory for the verification attempt; a network
    // failure must not leave it retained anywhere this tab could later restore from.
    expect(getToken()).toBeNull()
  })

  it('unlocks and shows the dashboard once the token verifies', async () => {
    mockedApi.getWorkspace.mockResolvedValue(workspace)
    render(<App />)

    unlockWith('correct-token')

    await waitFor(() => expect(screen.queryByLabelText(/access token/i)).not.toBeInTheDocument())
    expect(mockedApi.getWorkspace).toHaveBeenCalled()
  })

  it('restores an already-unlocked session after a reload in the same tab', async () => {
    commitToken('already-unlocked-token')
    mockedApi.getWorkspace.mockResolvedValue(workspace)

    render(<App />)

    expect(screen.queryByLabelText(/access token/i)).not.toBeInTheDocument()
    await waitFor(() => expect(mockedApi.getWorkspace).toHaveBeenCalled())
  })

  it('does not restore a token when this browsing context has no persisted history state', () => {
    // Real cross-tab isolation (an opener-created tab starting locked) needs a real second
    // browsing context and is covered by the Playwright acceptance suite (ST08-F02); this
    // confirms the jsdom-visible half of the contract: no history state, no restore.
    window.history.replaceState(null, '')
    render(<App />)

    expect(screen.getByLabelText(/access token/i)).toBeInTheDocument()
    expect(getToken()).toBeNull()
  })
})
