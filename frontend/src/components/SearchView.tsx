import { Fragment, useState } from 'react'
import type { SearchResponse, SearchResult, SearchScope } from '../types'

const SCOPES: { value: SearchScope; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'wiki', label: 'Wiki' },
  { value: 'tasks', label: 'Tasks' },
  { value: 'research', label: 'Research' },
  { value: 'repositories', label: 'Repositories' },
  { value: 'graph', label: 'Graph' },
]

/** The backend's FTS5 `snippet()` wraps each match in literal `[`/`]` markers (see
 * `SqliteBm25SearchEngine`). Splitting on them and rendering each segment as plain text (never
 * `dangerouslySetInnerHTML`) means arbitrary characters a user typed into a title/body can never
 * be interpreted as markup. */
function renderSnippet(snippet: string) {
  return snippet.split(/(\[[^\]]*\])/g).map((segment, index) => {
    const isMatch = segment.startsWith('[') && segment.endsWith(']')
    return (
      <Fragment key={index}>
        {isMatch ? <mark>{segment.slice(1, -1)}</mark> : segment}
      </Fragment>
    )
  })
}

interface SearchViewProps {
  onSearch: (query: string, scope: SearchScope, offset: number) => Promise<SearchResponse>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

export function SearchView({ onSearch, selectedNodeId, onSelectNode }: SearchViewProps) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<SearchScope>('all')
  const [results, setResults] = useState<SearchResult[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [isSearching, setIsSearching] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [nextOffset, setNextOffset] = useState(0)
  const [total, setTotal] = useState(0)

  async function runSearch() {
    const trimmed = query.trim()
    if (!trimmed) return
    setIsSearching(true)
    try {
      const page = await onSearch(trimmed, scope, 0)
      setResults(page.results)
      setHasMore(page.has_more)
      setNextOffset(page.offset + page.results.length)
      setTotal(page.total)
      setHasSearched(true)
    } finally {
      setIsSearching(false)
    }
  }

  async function loadMore() {
    const trimmed = query.trim()
    if (!trimmed || isSearching) return
    setIsSearching(true)
    try {
      const page = await onSearch(trimmed, scope, nextOffset)
      setResults((prior) => [...prior, ...page.results])
      setHasMore(page.has_more)
      setNextOffset(page.offset + page.results.length)
      setTotal(page.total)
    } finally {
      setIsSearching(false)
    }
  }

  return (
    <div className="search-view" aria-label="Search view">
      <div className="search-view-bar">
        <label className="sr-only" htmlFor="search-view-input">
          Search
        </label>
        <input
          id="search-view-input"
          type="text"
          placeholder="Search titles, bodies, and research identity…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') runSearch()
          }}
        />
        <label className="sr-only" htmlFor="search-view-scope">
          Scope
        </label>
        <select
          id="search-view-scope"
          value={scope}
          onChange={(event) => setScope(event.target.value as SearchScope)}
        >
          {SCOPES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button type="button" onClick={runSearch} disabled={isSearching || !query.trim()}>
          Search
        </button>
      </div>

      {hasSearched && results.length === 0 && (
        <p className="view-empty" role="status">
          No matches.
        </p>
      )}

      {hasSearched && results.length > 0 && (
        <p className="search-count" role="status">
          {total} {total === 1 ? 'match' : 'matches'}
        </p>
      )}

      <div className="list-view">
        {results.map((result) => {
          const title = result.node?.title ?? result.document?.title ?? result.goto ?? ''
          const key = result.goto ?? title
          return (
            <button
              key={key}
              type="button"
              className="node-row search-result-row"
              aria-current={result.node?.id === selectedNodeId}
              onClick={() => {
                // Node-backed results open in the graph; a standalone wiki document has no node
                // projection to select (ST-07: documents never require a graph node).
                if (result.node?.id) onSelectNode(result.node.id)
              }}
            >
              <span className="node-row-title">{title}</span>
              <span className="search-meta">
                {result.scope !== 'all' ? result.scope : result.entity_type}
                {result.source ? ` · ${result.source}` : ''} · score {result.score.toFixed(2)}
              </span>
              <span className="search-snippet">{renderSnippet(result.snippet)}</span>
            </button>
          )
        })}
      </div>

      {hasMore && (
        <button
          type="button"
          className="view-more-button"
          onClick={loadMore}
          disabled={isSearching}
        >
          {isSearching ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}
