import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PlaceExistingNodeControl } from './PlaceExistingNodeControl'
import type { GraphNode } from '../types'

function makeNode(id: string, title: string): GraphNode {
  return {
    id,
    workspace_id: 'ws',
    node_type_id: 'nt',
    title,
    body: '',
    status_id: null,
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('PlaceExistingNodeControl', () => {
  it('renders nothing when there are no unplaced nodes', () => {
    const { container } = render(<PlaceExistingNodeControl unplacedNodes={[]} onPlace={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists unplaced nodes and places the selected one', () => {
    const onPlace = vi.fn()
    render(
      <PlaceExistingNodeControl
        unplacedNodes={[makeNode('n1', 'Reading list'), makeNode('n2', 'Backlog')]}
        onPlace={onPlace}
      />,
    )

    fireEvent.change(screen.getByLabelText(/existing object/i), { target: { value: 'n2' } })
    fireEvent.click(screen.getByRole('button', { name: /place here/i }))

    expect(onPlace).toHaveBeenCalledWith('n2')
  })
})
