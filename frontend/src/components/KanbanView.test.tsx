import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { KanbanView } from './KanbanView'
import type { GraphNode, NodeType, ProjectionItem } from '../types'

const nodeType: NodeType = {
  id: 'nt-task',
  name: 'Task',
  icon: 'task',
  color_hex: '#26c',
  field_definitions: [],
  status_definitions: [],
}

function makeNode(id: string, title: string): GraphNode {
  return {
    id,
    workspace_id: 'ws-1',
    node_type_id: 'nt-task',
    title,
    body: '',
    status_id: null,
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('KanbanView', () => {
  it('shows an empty-state message with no columns', () => {
    render(
      <KanbanView
        columns={{}}
        nodeTypeById={new Map()}
        statusById={new Map()}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />,
    )
    expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument()
  })

  it('renders one column per group key, with an "Unassigned" label for the empty key', () => {
    const rows: ProjectionItem[] = [{ node: makeNode('n1', 'Write report'), resource: null }]
    render(
      <KanbanView
        columns={{ '': rows, done: [] }}
        nodeTypeById={new Map([[nodeType.id, nodeType]])}
        statusById={new Map()}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />,
    )
    expect(screen.getByText('Unassigned')).toBeInTheDocument()
    expect(screen.getByText('done')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /write report/i })).toBeInTheDocument()
  })
})
