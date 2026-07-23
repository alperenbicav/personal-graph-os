import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TableView } from './TableView'
import type { GraphNode, NodeType, ProjectionItem, StatusDefinition } from '../types'

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

const nodeTypeById = new Map([[nodeType.id, nodeType]])
const statusById = new Map<string, StatusDefinition>()

describe('TableView', () => {
  it('shows an empty-state message with no rows', () => {
    render(
      <TableView
        rows={[]}
        nodeTypeById={nodeTypeById}
        statusById={statusById}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />,
    )
    expect(screen.getByText(/nothing here yet/i)).toBeInTheDocument()
  })

  it('renders a row per item and reports the clicked node id', () => {
    const onSelectNode = vi.fn()
    const rows: ProjectionItem[] = [
      { node: makeNode('n1', 'Write report'), resource: null },
      { node: makeNode('n2', 'Draft plan'), resource: null },
    ]
    render(
      <TableView
        rows={rows}
        nodeTypeById={nodeTypeById}
        statusById={statusById}
        selectedNodeId="n2"
        onSelectNode={onSelectNode}
      />,
    )

    expect(screen.getByRole('button', { name: /write report/i })).toHaveAttribute(
      'aria-current',
      'false',
    )
    expect(screen.getByRole('button', { name: /draft plan/i })).toHaveAttribute(
      'aria-current',
      'true',
    )

    fireEvent.click(screen.getByRole('button', { name: /write report/i }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })
})
