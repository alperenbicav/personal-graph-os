import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Canvas, CanvasPlacement, EdgeType, GraphNode, NodeType, StatusDefinition } from '../types'

let capturedOnConnect: ((connection: { source: string | null; target: string | null }) => void) | null = null

vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>()
  return {
    ...actual,
    ReactFlow: (props: { onConnect?: (connection: { source: string | null; target: string | null }) => void }) => {
      capturedOnConnect = props.onConnect ?? null
      return null
    },
  }
})

const { GraphCanvas } = await import('./GraphCanvas')

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
const relatesTo: EdgeType = { id: 'et-relates', name: 'relates_to', inverse_name: 'relates_to', color_hex: '#999' }

const nodeTypeById = new Map([[taskType.id, taskType]])
const edgeTypeById = new Map([[relatesTo.id, relatesTo]])
const statusById = new Map([[statusTodo.id, statusTodo]])

function makeNode(id: string, title: string): GraphNode {
  return {
    id,
    workspace_id: 'ws-1',
    node_type_id: taskType.id,
    title,
    body: '',
    status_id: statusTodo.id,
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function placementFor(nodeId: string, id: string, x: number, y: number): CanvasPlacement {
  return { id, canvas_id: canvas.id, node_id: nodeId, position_x: x, position_y: y, width: 172, height: 100, is_collapsed: false }
}

describe('GraphCanvas connection wiring (React Flow onConnect mocked)', () => {
  it("forwards a completed React Flow connection to onRequestConnect(source, target)", () => {
    const onRequestConnect = vi.fn()
    render(
      <GraphCanvas
        canvas={canvas}
        nodes={[makeNode('n1', 'Write report'), makeNode('n2', 'Ship report')]}
        edges={[]}
        placements={[placementFor('n1', 'p1', 10, 10), placementFor('n2', 'p2', 260, 10)]}
        nodeTypeById={nodeTypeById}
        edgeTypeById={edgeTypeById}
        statusById={statusById}
        selectedNodeId={null}
        focusSet={null}
        onSelectNode={vi.fn()}
        onMovePlacement={vi.fn()}
        onRequestConnect={onRequestConnect}
      />,
    )

    expect(capturedOnConnect).toBeTypeOf('function')
    capturedOnConnect!({ source: 'n1', target: 'n2' })

    expect(onRequestConnect).toHaveBeenCalledWith('n1', 'n2')
  })

  it('ignores an incomplete connection missing a source or target', () => {
    const onRequestConnect = vi.fn()
    render(
      <GraphCanvas
        canvas={canvas}
        nodes={[makeNode('n1', 'Write report')]}
        edges={[]}
        placements={[placementFor('n1', 'p1', 10, 10)]}
        nodeTypeById={nodeTypeById}
        edgeTypeById={edgeTypeById}
        statusById={statusById}
        selectedNodeId={null}
        focusSet={null}
        onSelectNode={vi.fn()}
        onMovePlacement={vi.fn()}
        onRequestConnect={onRequestConnect}
      />,
    )

    capturedOnConnect!({ source: 'n1', target: null })
    expect(onRequestConnect).not.toHaveBeenCalled()
  })
})
