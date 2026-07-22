import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as api from './api/client'
import type { Canvas, CanvasPlacement, GraphNode, Workspace } from './types'

vi.mock('./api/client')

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
      field_definitions: [],
      status_definitions: [
        { id: 'status-todo', name: 'Todo', color_hex: '#999', is_terminal: false, sort_order: 0 },
        { id: 'status-done', name: 'Done', color_hex: '#0a0', is_terminal: true, sort_order: 1 },
      ],
    },
    { id: 'nt-note', name: 'Note', icon: 'note', color_hex: '#888', field_definitions: [], status_definitions: [] },
    {
      id: 'nt-project',
      name: 'Project',
      icon: 'proj',
      color_hex: '#f80',
      field_definitions: [],
      status_definitions: [],
    },
    {
      id: 'nt-resource',
      name: 'Resource',
      icon: 'res',
      color_hex: '#0a0',
      field_definitions: [],
      status_definitions: [],
    },
    {
      id: 'nt-custom',
      name: 'Idea',
      icon: 'bulb',
      color_hex: '#c0c',
      field_definitions: [],
      status_definitions: [],
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
const canvasB: Canvas = { id: 'canvas-b', workspace_id: 'ws-1', name: 'Research', created_at: '2026-01-01T00:00:00Z' }

function placementFor(canvasId: string, nodeId: string, id: string): CanvasPlacement {
  return {
    id,
    canvas_id: canvasId,
    node_id: nodeId,
    position_x: 10,
    position_y: 10,
    width: 172,
    height: 100,
    is_collapsed: false,
  }
}

beforeEach(() => {
  mockedApi.getWorkspace.mockResolvedValue(workspace)
  mockedApi.listEdges.mockResolvedValue([])
  mockedApi.listCanvases.mockResolvedValue([canvasA, canvasB])
  mockedApi.listResources.mockResolvedValue([])
  mockedApi.listAttachments.mockResolvedValue([])
  mockedApi.listFileReferences.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('App bootstrap', () => {
  it('renders the top bar and active canvas caption once data loads', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    mockedApi.listPlacements.mockResolvedValue([placementFor('canvas-a', 'n1', 'p1')])

    render(<App />)

    await screen.findByText('Personal Graph OS')
    await waitFor(() => expect(screen.getAllByText(/main/i).length).toBeGreaterThan(0))
  })
})

describe('quick capture node types', () => {
  it('offers every workspace node type, including one outside the default preferred order', async () => {
    render(<App />)
    await screen.findByText('Personal Graph OS')

    const options = screen.getAllByRole('option').map((option) => option.textContent)
    expect(options).toContain('Idea')
  })
})

describe('non-destructive error handling', () => {
  it('surfaces a dismissible banner when placements fail to load, without crashing', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    mockedApi.listPlacements.mockRejectedValue(new Error('network down'))

    render(<App />)

    await screen.findByText(/could not load this canvas's placements/i)
    expect(screen.getByText('Personal Graph OS')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByText(/could not load this canvas's placements/i)).not.toBeInTheDocument()
  })

  it('surfaces an error and leaves canvases unchanged when creating a canvas fails', async () => {
    mockedApi.listNodes.mockResolvedValue([])
    mockedApi.listPlacements.mockResolvedValue([])
    mockedApi.createCanvas.mockRejectedValue(new Error('server exploded'))
    vi.spyOn(window, 'prompt').mockReturnValue('New canvas')

    render(<App />)
    await screen.findByText('Personal Graph OS')

    fireEvent.click(screen.getByRole('button', { name: /new canvas/i }))

    await screen.findByText(/could not create canvas/i)
    // Scoped to the canvas rail: "Research" is also this app's own nav tab label.
    const canvasRail = screen.getByRole('navigation', { name: /canvases/i })
    expect(within(canvasRail).getAllByRole('button', { name: /main|research/i })).toHaveLength(2)
  })

  it('never renders a prior canvas\'s node under the newly active canvas id after a failed switch', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Only on Main')])
    mockedApi.listPlacements.mockImplementation((canvasId: string) =>
      canvasId === 'canvas-a'
        ? Promise.resolve([placementFor('canvas-a', 'n1', 'p1')])
        : Promise.reject(new Error('canvas-b unreachable')),
    )

    render(<App />)
    await screen.findByText('Personal Graph OS')
    await screen.findByText('Only on Main')

    // Scoped to the canvas rail: the fixture's second canvas happens to be named "Research",
    // which now also collides with the app's own "Research" nav tab button.
    fireEvent.click(
      within(screen.getByRole('navigation', { name: /canvases/i })).getByRole('button', {
        name: /research/i,
      }),
    )

    await screen.findByText(/could not load this canvas's placements/i)
    // The node still exists in the workspace (and is offered by "Place existing" for this
    // canvas), but it must not be rendered as a placed card carried over from canvas A.
    expect(document.querySelectorAll('.pg-node')).toHaveLength(0)
  })

})

// Status change, archive, edge-creation-retry, and placement-move-rollback regressions
// live in App.mutations.test.tsx, which mocks GraphCanvas to drive
// onRequestConnect/onMovePlacement directly — the real pointer gestures that produce
// them are not reproducible in jsdom (see GraphCanvas.connectWiring.test.tsx).

describe('reusing an existing node across canvases', () => {
  it('places an already-captured node on a different canvas via "Place here"', async () => {
    mockedApi.listNodes.mockResolvedValue([makeNode('n1', 'Write report')])
    // Node n1 has no placement on either canvas yet.
    mockedApi.listPlacements.mockResolvedValue([])
    mockedApi.placeNode.mockResolvedValue(placementFor('canvas-b', 'n1', 'p-new'))

    render(<App />)
    await screen.findByText('Personal Graph OS')

    // Scoped to the canvas rail: the fixture's second canvas happens to be named "Research",
    // which now also collides with the app's own "Research" nav tab button.
    const canvasRail = screen.getByRole('navigation', { name: /canvases/i })
    fireEvent.click(within(canvasRail).getByRole('button', { name: /research/i }))
    await waitFor(() => expect(mockedApi.listPlacements).toHaveBeenCalledWith('canvas-b'))

    fireEvent.click(screen.getByRole('button', { name: /place here/i }))

    await waitFor(() =>
      expect(mockedApi.placeNode).toHaveBeenCalledWith('canvas-b', 'n1', expect.any(Number), expect.any(Number)),
    )
  })
})

// Node selection itself (clicking a React Flow node) is driven by React Flow's internal
// pointer-event/drag-threshold system, which a synthetic `fireEvent.click` in jsdom does not
// reproduce reliably. Status-change/archive-after-selection were covered end-to-end against
// the real backend and a real browser instead (see WORK.md ST-02 evidence: the initial ST-02
// live headless-browser smoke test — select, change status, drag, reload, then keyboard
// archive). The neighborhood-focus depth toggle itself has NOT been exercised in any live
// browser pass; only its pure filtering logic is covered, in isolation, by
// `graphProjection.test.ts` (`filterByFocus`/`restrictToPlacedNodes`). A later round's
// browser check covered connection-handle discoverability and drag-to-connect (see WORK.md
// ST-02 refactor-round-3 evidence), not the focus-depth slider.
