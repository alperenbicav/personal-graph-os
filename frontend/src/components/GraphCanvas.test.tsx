import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GraphCanvas } from './GraphCanvas'
import type { Canvas, CanvasPlacement, EdgeType, GraphEdge, GraphNode, NodeType, StatusDefinition } from '../types'

const canvas: Canvas = { id: 'canvas-1', workspace_id: 'ws-1', name: 'Main', created_at: '2026-01-01T00:00:00Z' }

const taskType: NodeType = {
  id: 'nt-task',
  name: 'Task',
  icon: 'task',
  color_hex: '#26c',
  field_definitions: [],
  status_definitions: [],
}

const statusTodo: StatusDefinition = { id: 'status-todo', name: 'Todo', color_hex: '#999', is_terminal: false, sort_order: 0 }
const statusDone: StatusDefinition = { id: 'status-done', name: 'Done', color_hex: '#0a0', is_terminal: true, sort_order: 1 }

const relatesTo: EdgeType = { id: 'et-relates', name: 'relates_to', inverse_name: 'relates_to', color_hex: '#999' }

const nodeTypeById = new Map([[taskType.id, taskType]])
const edgeTypeById = new Map([[relatesTo.id, relatesTo]])
const statusById = new Map([
  [statusTodo.id, statusTodo],
  [statusDone.id, statusDone],
])

function makeNode(id: string, title: string, statusId: string | null): GraphNode {
  return {
    id,
    workspace_id: 'ws-1',
    node_type_id: taskType.id,
    title,
    body: '',
    status_id: statusId,
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function placementFor(nodeId: string, id: string, x: number, y: number): CanvasPlacement {
  return { id, canvas_id: canvas.id, node_id: nodeId, position_x: x, position_y: y, width: 172, height: 100, is_collapsed: false }
}

function baseProps() {
  return {
    canvas,
    nodeTypeById,
    edgeTypeById,
    statusById,
    selectedNodeId: null,
    focusSet: null,
    onSelectNode: vi.fn(),
    onMovePlacement: vi.fn(),
    onRequestConnect: vi.fn(),
  }
}

describe('same-id node projection', () => {
  it('re-renders the typed node card when a same-id node is title/status updated', async () => {
    const nodeA = makeNode('n1', 'Write report', statusTodo.id)
    const placements = [placementFor('n1', 'p1', 10, 10)]

    const { rerender } = render(
      <GraphCanvas {...baseProps()} nodes={[nodeA]} edges={[]} placements={placements} />,
    )

    await screen.findByText('Write report')
    expect(screen.getByText('Todo')).toBeInTheDocument()

    const updatedNode = makeNode('n1', 'Ship report', statusDone.id)
    rerender(<GraphCanvas {...baseProps()} nodes={[updatedNode]} edges={[]} placements={placements} />)

    await waitFor(() => expect(screen.getByText('Ship report')).toBeInTheDocument())
    expect(screen.getByText('Done')).toBeInTheDocument()
    expect(screen.queryByText('Write report')).not.toBeInTheDocument()
    expect(screen.queryByText('Todo')).not.toBeInTheDocument()
  })
})

describe('dangling edge exclusion', () => {
  it('does not hand React Flow an edge whose other endpoint is not placed on this canvas', async () => {
    const placedNode = makeNode('n1', 'Placed here', statusTodo.id)
    const unplacedNode = makeNode('n2', 'Not on this canvas', statusTodo.id)
    const danglingEdge: GraphEdge = {
      id: 'e1',
      workspace_id: 'ws-1',
      edge_type_id: relatesTo.id,
      source_node_id: 'n1',
      target_node_id: 'n2',
      field_values: {},
      created_at: '2026-01-01T00:00:00Z',
    }
    const placements = [placementFor('n1', 'p1', 10, 10)]

    render(
      <GraphCanvas
        {...baseProps()}
        nodes={[placedNode, unplacedNode]}
        edges={[danglingEdge]}
        placements={placements}
      />,
    )

    await screen.findByText('Placed here')
    expect(document.querySelectorAll('.react-flow__edge')).toHaveLength(0)
  })
})

describe('connection handles', () => {
  it('renders discoverable connection handles with no inline opacity override', async () => {
    const nodeA = makeNode('n1', 'Write report', statusTodo.id)
    const placements = [placementFor('n1', 'p1', 10, 10)]

    render(<GraphCanvas {...baseProps()} nodes={[nodeA]} edges={[]} placements={placements} />)

    await screen.findByText('Write report')
    const handles = document.querySelectorAll('.react-flow__handle')
    expect(handles.length).toBeGreaterThan(0)
    handles.forEach((handle) => {
      expect(handle.classList.contains('pg-handle')).toBe(true)
      expect((handle as HTMLElement).style.opacity).toBe('')
    })
  })

  it('has real, discoverable source and target handle DOM nodes on every rendered card', async () => {
    const nodeA = makeNode('n1', 'Write report', statusTodo.id)
    const nodeB = makeNode('n2', 'Ship report', statusTodo.id)
    const placements = [placementFor('n1', 'p1', 10, 10), placementFor('n2', 'p2', 260, 10)]

    render(<GraphCanvas {...baseProps()} nodes={[nodeA, nodeB]} edges={[]} placements={placements} />)

    await screen.findByText('Write report')

    const nodeAEl = document.querySelector('[data-id="n1"]') as HTMLElement
    const nodeBEl = document.querySelector('[data-id="n2"]') as HTMLElement
    expect(nodeAEl.querySelector('.react-flow__handle.source')).toBeTruthy()
    expect(nodeBEl.querySelector('.react-flow__handle.target')).toBeTruthy()
  })
})

// React Flow's connection-drag gesture is driven by its own internal pointer-tracking
// (mousedown/mousemove/mouseup on the document plus `document.elementFromPoint` hit
// testing against live layout), which jsdom does not reproduce — the same limitation
// already recorded for node-selection drag elsewhere in this suite (see App.test.tsx).
// `GraphCanvas.connectWiring.test.tsx` proves the wiring this component owns (a React
// Flow `Connection` is translated into the correct `onRequestConnect(source, target)`
// call) by mocking React Flow's `onConnect` entry point. The actual pointer gesture and
// visible-handle affordance were verified in a live browser (see WORK.md ST-02
// refactor-round-2 evidence).
