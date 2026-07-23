import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CanvasObjectList } from './CanvasObjectList'
import type { GraphNode, NodeType } from '../types'

const taskType: NodeType = {
  id: 'nt-task',
  name: 'Task',
  icon: 'task',
  color_hex: '#26c',
  field_definitions: [],
  status_definitions: [],
}

const nodeTypeById = new Map([[taskType.id, taskType]])

function makeNode(id: string, title: string): GraphNode {
  return {
    id,
    workspace_id: 'ws-1',
    node_type_id: taskType.id,
    title,
    body: '',
    status_id: null,
    field_values: {},
    is_archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

const nodes = [makeNode('n1', 'First object'), makeNode('n2', 'Second object'), makeNode('n3', 'Third object')]

describe('CanvasObjectList', () => {
  it('renders every node as a labeled, selectable option', () => {
    render(
      <CanvasObjectList nodes={nodes} nodeTypeById={nodeTypeById} selectedNodeId={null} onSelectNode={vi.fn()} />,
    )

    const listbox = screen.getByRole('listbox', { name: /canvas objects/i })
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(listbox).toContainElement(options[0])
    expect(options[0]).toHaveTextContent('Task: First object')
  })

  it('marks the selected node with aria-selected', () => {
    render(
      <CanvasObjectList nodes={nodes} nodeTypeById={nodeTypeById} selectedNodeId="n2" onSelectNode={vi.fn()} />,
    )

    const options = screen.getAllByRole('option')
    expect(options[1]).toHaveAttribute('aria-selected', 'true')
    expect(options[0]).toHaveAttribute('aria-selected', 'false')
  })

  it('moves roving focus with ArrowDown/ArrowUp and selects with Enter', () => {
    const onSelectNode = vi.fn()
    render(
      <CanvasObjectList nodes={nodes} nodeTypeById={nodeTypeById} selectedNodeId={null} onSelectNode={onSelectNode} />,
    )

    const options = screen.getAllByRole('option')
    options[0].focus()
    expect(document.activeElement).toBe(options[0])

    fireEvent.keyDown(options[0], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(options[1])

    fireEvent.keyDown(options[1], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(options[2])

    // Wraps back to the first option past the last.
    fireEvent.keyDown(options[2], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(options[0])

    fireEvent.keyDown(options[0], { key: 'ArrowUp' })
    expect(document.activeElement).toBe(options[2])

    fireEvent.keyDown(options[2], { key: 'Enter' })
    expect(onSelectNode).toHaveBeenCalledWith('n3')
  })

  it('only the roving-tabindex option is keyboard-tabbable at a time', () => {
    render(
      <CanvasObjectList nodes={nodes} nodeTypeById={nodeTypeById} selectedNodeId={null} onSelectNode={vi.fn()} />,
    )

    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveAttribute('tabindex', '0')
    expect(options[1]).toHaveAttribute('tabindex', '-1')
    expect(options[2]).toHaveAttribute('tabindex', '-1')
  })

  it('renders nothing when there are no nodes', () => {
    const { container } = render(
      <CanvasObjectList nodes={[]} nodeTypeById={nodeTypeById} selectedNodeId={null} onSelectNode={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
