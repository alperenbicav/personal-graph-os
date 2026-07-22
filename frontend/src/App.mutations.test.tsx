import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as api from './api/client'
import type { Canvas, CanvasPlacement, GraphEdge, GraphNode, Workspace } from './types'

vi.mock('./api/client')

// Drives onMovePlacement/onRequestConnect directly instead of a real React Flow drag
// gesture, which jsdom cannot reproduce (see GraphCanvas.connectWiring.test.tsx). This
// file is only about App's own mutation error-handling contract, not GraphCanvas's
// rendering or wiring, which are covered separately.
vi.mock('./components/GraphCanvas', () => ({
  GraphCanvas: (props: {
    placements: CanvasPlacement[]
    onMovePlacement: (placementId: string, x: number, y: number) => void
    onRequestConnect: (source: string, target: string) => void
  }) => {
    const p1 = props.placements.find((p) => p.id === 'p1')
    return (
      <div data-testid="graph-canvas-stub">
        <span data-testid="p1-position">{p1 ? `${p1.position_x},${p1.position_y}` : 'none'}</span>
        <button type="button" onClick={() => props.onMovePlacement('p1', 999, 999)}>
          stub-move-p1
        </button>
        <button type="button" onClick={() => props.onMovePlacement('p1', 500, 500)}>
          stub-move-p1-to-b
        </button>
        <button type="button" onClick={() => props.onMovePlacement('p1', 700, 700)}>
          stub-move-p1-to-c
        </button>
        <button type="button" onClick={() => props.onRequestConnect('n1', 'n2')}>
          stub-request-connect
        </button>
      </div>
    )
  },
}))

const mockedApi = vi.mocked(api)

const workspace: Workspace = {
  id: 'ws-1',
  name: 'Personal',
  created_at: '2026-01-01T00:00:00Z',
  node_types: [
    {
      id: 'nt-task',
      name: 'Task',
      icon: 'task',
      color_hex: '#26c',
      field_definitions: [
        {
          id: 'field-notes',
          name: 'notes',
          field_type: 'text',
          is_required: false,
          select_options: [],
          description: null,
        },
      ],
      status_definitions: [
        { id: 'status-todo', name: 'Todo', color_hex: '#999', is_terminal: false, sort_order: 0 },
        { id: 'status-done', name: 'Done', color_hex: '#0a0', is_terminal: true, sort_order: 1 },
      ],
    },
  ],
  edge_types: [{ id: 'et-relates', name: 'relates_to', inverse_name: 'relates_to', color_hex: '#999' }],
}

function makeNode(id: string, title: string): GraphNode {
  return {
    id,
    workspace_id: 'ws-1',
    node_type_id: 'nt-task',
    title,
    body: '',
    status_id: 'status-todo',
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

const canvasA: Canvas = { id: 'canvas-a', workspace_id: 'ws-1', name: 'Main', created_at: '2026-01-01T00:00:00Z' }

function placementFor(nodeId: string, id: string, x = 10, y = 10): CanvasPlacement {
  return { id, canvas_id: canvasA.id, node_id: nodeId, position_x: x, position_y: y, width: 172, height: 100, is_collapsed: false }
}

async function captureAndSelectNode(title: string) {
  const node = makeNode('n1', title)
  mockedApi.captureNode.mockResolvedValue(node)
  mockedApi.placeNode.mockResolvedValue(placementFor('n1', 'p1'))

  fireEvent.change(screen.getByLabelText(/quick capture/i), { target: { value: title } })
  fireEvent.click(screen.getByRole('button', { name: /^add$/i }))
  await screen.findByText(title)
}

beforeEach(() => {
  mockedApi.getWorkspace.mockResolvedValue(workspace)
  mockedApi.listEdges.mockResolvedValue([])
  mockedApi.listCanvases.mockResolvedValue([canvasA])
  mockedApi.listNodes.mockResolvedValue([])
  mockedApi.listPlacements.mockResolvedValue([])
  mockedApi.listResources.mockResolvedValue([])
  mockedApi.listAttachments.mockResolvedValue([])
  mockedApi.listFileReferences.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('status change and archive failure handling', () => {
  it('surfaces a dismissible error and keeps the node unchanged when a status update is rejected', async () => {
    render(<App />)
    await screen.findByText('Personal Graph OS')
    await captureAndSelectNode('Write report')

    mockedApi.updateNode.mockRejectedValue(new Error('status update failed'))

    fireEvent.change(screen.getByLabelText(/^status$/i), { target: { value: 'status-done' } })

    await screen.findByText(/could not update status/i)
    expect(screen.getByLabelText(/^status$/i)).toHaveValue('status-todo')
  })

  it('surfaces an error and keeps the node present when archiving is rejected', async () => {
    render(<App />)
    await screen.findByText('Personal Graph OS')
    await captureAndSelectNode('Write report')

    mockedApi.archiveNode.mockRejectedValue(new Error('archive failed'))

    fireEvent.click(screen.getByRole('button', { name: /archive \(delete\)/i }))

    await screen.findByText(/could not archive that object/i)
    expect(screen.getByText('Write report')).toBeInTheDocument()
  })
})

describe('field update failure handling', () => {
  it('surfaces an error and reverts the control to the canonical value when a field save is rejected', async () => {
    render(<App />)
    await screen.findByText('Personal Graph OS')
    await captureAndSelectNode('Write report')

    mockedApi.updateNode.mockRejectedValue(new Error('field update failed'))

    const input = screen.getByLabelText('notes')
    fireEvent.change(input, { target: { value: 'a note that will not save' } })
    fireEvent.blur(input)

    await screen.findByText(/could not save that field/i)
    expect(screen.getByLabelText('notes')).toHaveValue('')
  })
})

describe('edge creation retry', () => {
  it('keeps the pending connection open on failure and lets the user retry to success', async () => {
    render(<App />)
    await screen.findByText('Personal Graph OS')

    mockedApi.connectEdge
      .mockRejectedValueOnce(new Error('backend down'))
      .mockResolvedValueOnce({
        id: 'e1',
        workspace_id: 'ws-1',
        edge_type_id: 'et-relates',
        source_node_id: 'n1',
        target_node_id: 'n2',
        field_values: {},
        created_at: '2026-01-01T00:00:00Z',
      } satisfies GraphEdge)

    fireEvent.click(screen.getByRole('button', { name: 'stub-request-connect' }))
    await screen.findByRole('dialog', { name: /connect two objects/i })

    fireEvent.click(screen.getByRole('button', { name: /^connect$/i }))
    await screen.findByText(/could not create that relationship/i)
    // The modal must still be open so the user can retry without redragging handles.
    expect(screen.getByRole('dialog', { name: /connect two objects/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^connect$/i }))
    await waitFor(() => expect(mockedApi.connectEdge).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog', { name: /connect two objects/i })).not.toBeInTheDocument()
  })
})

describe('placement move rollback', () => {
  it('reverts an optimistic move to its previous position when the API rejects it', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    mockedApi.listPlacements.mockResolvedValue([placementFor('n1', 'p1', 10, 10)])
    mockedApi.updatePlacement.mockRejectedValue(new Error('save failed'))

    render(<App />)
    await screen.findByText('Personal Graph OS')
    await screen.findByText('10,10')

    fireEvent.click(screen.getByRole('button', { name: 'stub-move-p1' }))
    expect(screen.getByTestId('p1-position').textContent).toBe('999,999')

    await screen.findByText(/could not save the new position/i)
    await waitFor(() => expect(screen.getByTestId('p1-position').textContent).toBe('10,10'))
  })

  it('does not let an older rejected move roll back a newer, already-successful move', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    mockedApi.listPlacements.mockResolvedValue([placementFor('n1', 'p1', 10, 10)])

    type Resolver = { resolve: (v: CanvasPlacement) => void; reject: (e: Error) => void }
    const pending: Resolver[] = []
    mockedApi.updatePlacement.mockImplementation(
      () =>
        new Promise<CanvasPlacement>((resolve, reject) => {
          pending.push({ resolve, reject })
        }),
    )

    render(<App />)
    await screen.findByText('Personal Graph OS')
    await screen.findByText('10,10')

    // Move to B (request 1, still in flight), then move to C (request 2, still in flight)
    // before request 1 settles — exactly the overlapping-save race the finding describes.
    fireEvent.click(screen.getByRole('button', { name: 'stub-move-p1-to-b' }))
    expect(screen.getByTestId('p1-position').textContent).toBe('500,500')

    fireEvent.click(screen.getByRole('button', { name: 'stub-move-p1-to-c' }))
    expect(screen.getByTestId('p1-position').textContent).toBe('700,700')

    expect(pending).toHaveLength(2)

    // Request 1 (the now-superseded move to B) rejects after request 2 already started.
    pending[0].reject(new Error('request 1 failed'))
    await Promise.resolve().then(() => Promise.resolve())
    // Must NOT roll back to B (or anything older) — request 2 owns the current position.
    expect(screen.getByTestId('p1-position').textContent).toBe('700,700')
    expect(screen.queryByText(/could not save the new position/i)).not.toBeInTheDocument()

    // Request 2 (the move to C) then succeeds.
    pending[1].resolve(placementFor('n1', 'p1', 700, 700))
    await Promise.resolve().then(() => Promise.resolve())
    expect(screen.getByTestId('p1-position').textContent).toBe('700,700')
  })

  it('reverts to the last actually-persisted position (A) when both overlapping moves reject', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    mockedApi.listPlacements.mockResolvedValue([placementFor('n1', 'p1', 10, 10)])

    type Resolver = { resolve: (v: CanvasPlacement) => void; reject: (e: Error) => void }
    const pending: Resolver[] = []
    mockedApi.updatePlacement.mockImplementation(
      () =>
        new Promise<CanvasPlacement>((resolve, reject) => {
          pending.push({ resolve, reject })
        }),
    )

    render(<App />)
    await screen.findByText('Personal Graph OS')
    await screen.findByText('10,10')

    // Persisted truth is A (10,10). Move to B (request 1), then to C (request 2), before
    // either settles. Neither B nor C has actually persisted anywhere yet.
    fireEvent.click(screen.getByRole('button', { name: 'stub-move-p1-to-b' }))
    fireEvent.click(screen.getByRole('button', { name: 'stub-move-p1-to-c' }))
    expect(screen.getByTestId('p1-position').textContent).toBe('700,700')
    expect(pending).toHaveLength(2)

    // Request 1 (superseded move to B) rejects — ignored, per the guard.
    pending[0].reject(new Error('request 1 failed'))
    await Promise.resolve().then(() => Promise.resolve())
    expect(screen.getByTestId('p1-position').textContent).toBe('700,700')

    // Request 2 (the latest move, to C) also rejects. B was never persisted (request 1
    // failed), so rolling back to B would leave the UI showing a position the database
    // never had. The only value known to have actually persisted is A.
    pending[1].reject(new Error('request 2 failed'))
    await screen.findByText(/could not save the new position/i)
    await waitFor(() => expect(screen.getByTestId('p1-position').textContent).toBe('10,10'))
  })
})
