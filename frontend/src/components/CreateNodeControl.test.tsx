import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CreateNodeControl } from './CreateNodeControl'
import type { NodeType } from '../types'

const nodeTypes: NodeType[] = [
  {
    id: 'nt-note',
    name: 'Note',
    icon: 'note',
    color_hex: '#888',
    field_definitions: [],
    status_definitions: [],
  },
  {
    id: 'nt-idea',
    name: 'Idea',
    icon: 'idea',
    color_hex: '#26c',
    field_definitions: [],
    status_definitions: [],
  },
]

describe('CreateNodeControl', () => {
  it('creates a node with the selected type and trimmed title', () => {
    const onCreate = vi.fn()
    render(<CreateNodeControl nodeTypes={nodeTypes} isDisabled={false} onCreate={onCreate} />)

    fireEvent.change(screen.getByLabelText('Node type'), { target: { value: 'nt-idea' } })
    fireEvent.change(screen.getByLabelText('New node title'), { target: { value: '  Idea one  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add node' }))

    expect(onCreate).toHaveBeenCalledWith('nt-idea', 'Idea one')
  })

  it('offers a custom Schema-Editor node type and does not require a global capture', () => {
    const onCreate = vi.fn()
    render(<CreateNodeControl nodeTypes={nodeTypes} isDisabled={false} onCreate={onCreate} />)

    const options = Array.from(screen.getByLabelText('Node type').querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    expect(options).toEqual(['Note', 'Idea'])
  })

  it('does not create a node without a title or while the canvas is unavailable', () => {
    const onCreate = vi.fn()
    render(<CreateNodeControl nodeTypes={nodeTypes} isDisabled onCreate={onCreate} />)

    expect(screen.getByRole('button', { name: 'Add node' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Add node' }))
    expect(onCreate).not.toHaveBeenCalled()
  })
})
