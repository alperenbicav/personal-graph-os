import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchView } from './SearchView'
import type { GraphNode, SearchResult } from '../types'

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

describe('SearchView', () => {
  it('runs the search on submit and renders a highlighted snippet', async () => {
    const results: SearchResult[] = [
      { node: makeNode('n1', 'Attention Is All You Need'), resource: null, snippet: '[Attention] is all you need' },
    ]
    const onSearch = vi.fn().mockResolvedValue(results)

    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/search titles/i), { target: { value: 'attention' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => expect(onSearch).toHaveBeenCalledWith('attention'))
    expect(await screen.findByText('Attention Is All You Need')).toBeInTheDocument()
    expect(screen.getByText('Attention', { selector: 'mark' })).toBeInTheDocument()
  })

  it('shows a no-matches message after a search returns nothing', async () => {
    const onSearch = vi.fn().mockResolvedValue([])
    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/search titles/i), { target: { value: 'nothing' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    expect(await screen.findByText(/no matches/i)).toBeInTheDocument()
  })

  it('reports the clicked result node id', async () => {
    const onSelectNode = vi.fn()
    const results: SearchResult[] = [
      { node: makeNode('n1', 'Attention Is All You Need'), resource: null, snippet: 'text' },
    ]
    render(
      <SearchView
        onSearch={vi.fn().mockResolvedValue(results)}
        selectedNodeId={null}
        onSelectNode={onSelectNode}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText(/search titles/i), { target: { value: 'attention' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    fireEvent.click(await screen.findByRole('button', { name: /attention is all you need/i }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })
})
