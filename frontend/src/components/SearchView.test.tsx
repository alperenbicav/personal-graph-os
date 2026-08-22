import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchView } from './SearchView'
import type { GraphNode, SearchResponse, SearchResult } from '../types'

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

function makeResult(title: string, id = 'n1'): SearchResult {
  return {
    node: makeNode(id, title),
    resource: null,
    document: null,
    snippet: `[${title.split(' ')[0]}] is all you need`,
    score: 0.5,
    scope: 'all',
    entity_type: 'node',
    source: null,
    goto: id,
  }
}

function page(results: SearchResult[], total: number, offset: number): SearchResponse {
  return { results, total, has_more: offset + results.length < total, offset }
}

describe('SearchView', () => {
  it('runs the search on submit and renders a highlighted snippet', async () => {
    const onSearch = vi
      .fn()
      .mockResolvedValue(page([makeResult('Attention Is All You Need')], 1, 0))

    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'attention' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    await waitFor(() => expect(onSearch).toHaveBeenCalledWith('attention', 'all', 0))
    expect(await screen.findByText('Attention Is All You Need')).toBeInTheDocument()
    expect(screen.getByText('Attention', { selector: 'mark' })).toBeInTheDocument()
  })

  it('shows a no-matches message after a search returns nothing', async () => {
    const onSearch = vi.fn().mockResolvedValue(page([], 0, 0))
    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'nothing' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    expect(await screen.findByText(/no matches/i)).toBeInTheDocument()
  })

  it('reports the clicked result node id', async () => {
    const onSelectNode = vi.fn()
    const onSearch = vi.fn().mockResolvedValue(page([makeResult('Attention Is All You Need')], 1, 0))
    render(
      <SearchView
        onSearch={onSearch}
        selectedNodeId={null}
        onSelectNode={onSelectNode}
      />,
    )

    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'attention' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    fireEvent.click(await screen.findByRole('button', { name: /attention is all you need/i }))
    expect(onSelectNode).toHaveBeenCalledWith('n1')
  })

  it('loads the next page when has_more is set', async () => {
    const first = page([makeResult('First result', 'n1')], 2, 0)
    const second = page([makeResult('Second result', 'n2')], 2, 1)
    const onSearch = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)

    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'match' } })
    fireEvent.click(screen.getByRole('button', { name: /search/i }))

    const loadMore = await screen.findByRole('button', { name: /load more/i })
    fireEvent.click(loadMore)

    await waitFor(() => expect(onSearch).toHaveBeenLastCalledWith('match', 'all', 1))
    expect(await screen.findByText('Second result')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })
})

  it('renders an error state when the search request fails', async () => {
    const onSearch = vi.fn().mockRejectedValue(new Error('index offline'))
    render(<SearchView onSearch={onSearch} selectedNodeId={null} onSelectNode={() => {}} />)

    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'attention' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/search failed/i)
      expect(screen.getByRole('alert')).toHaveTextContent('index offline')
    })
  })
