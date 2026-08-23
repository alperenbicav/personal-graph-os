import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KnowledgePulseBanner } from './KnowledgePulseBanner'
import type { GraphEdge, GraphNode, WorkItem } from '../types'

const mockNodes: GraphNode[] = [
  {
    id: 'n1',
    workspace_id: 'ws-1',
    node_type_id: 'nt1',
    title: 'Transformer Architecture',
    body: '',
    status_id: null,
    field_values: {},
    is_archived: false,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  },
  {
    id: 'n2',
    workspace_id: 'ws-1',
    node_type_id: 'nt1',
    title: 'Attention Mechanism',
    body: '',
    status_id: null,
    field_values: {},
    is_archived: false,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  },
]

const mockEdges: GraphEdge[] = [
  {
    id: 'e1',
    workspace_id: 'ws-1',
    edge_type_id: 'et1',
    source_node_id: 'n1',
    target_node_id: 'n2',
    field_values: {},
    created_at: '2026-08-20T00:00:00Z',
  },
]

const mockWorkItems: WorkItem[] = [
  {
    id: 'w1',
    workspace_id: 'ws-1',
    node_id: 'n1',
    kind: 'task',
    work_type: 'feature',
    title: 'Implement KV Cache',
    body: '',
    source: 'manual',
    status: 'done',
    priority: 'high',
    due_date: null,
    assignee: null,
    blockers: null,
    progress_percent: 100,
    repository_node_id: null,
    parent_id: null,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  },
]

describe('KnowledgePulseBanner', () => {
  it('renders all 4 pulse metric chips with labels and values', () => {
    render(
      <KnowledgePulseBanner
        nodes={mockNodes}
        edges={mockEdges}
        workItems={mockWorkItems}
      />,
    )

    expect(screen.getByLabelText(/knowledge pulse/i)).toBeInTheDocument()
    expect(screen.getByText(/graph nodes/i)).toBeInTheDocument()
    expect(screen.getByText(/relations/i)).toBeInTheDocument()
    expect(screen.getByText(/connectivity/i)).toBeInTheDocument()
    expect(screen.getByText(/tasks shipped/i)).toBeInTheDocument()
  })

  it('handles empty workspace with 0 metrics', () => {
    render(<KnowledgePulseBanner nodes={[]} edges={[]} workItems={[]} />)
    expect(screen.getByText(/graph nodes/i)).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})
