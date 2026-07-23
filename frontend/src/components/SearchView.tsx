import { Fragment, useState } from 'react'
import type { SearchResult } from '../types'

/** The backend's FTS5 `snippet()` wraps each match in literal `[`/`]` markers (see
 * `SqliteSearchIndexRepository.search`). Splitting on them and rendering each segment as
 * plain text (never `dangerouslySetInnerHTML`) means arbitrary characters a user typed into
 * a title/body can never be interpreted as markup. */
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
  onSearch: (query: string) => Promise<SearchResult[]>
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

export function SearchView({ onSearch, selectedNodeId, onSelectNode }: SearchViewProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [isSearching, setIsSearching] = useState(false)

  async function runSearch() {
    const trimmed = query.trim()
    if (!trimmed) return
    setIsSearching(true)
    try {
      setResults(await onSearch(trimmed))
      setHasSearched(true)
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
        <button type="button" onClick={runSearch} disabled={isSearching || !query.trim()}>
          Search
        </button>
      </div>

      {hasSearched && results.length === 0 && (
        <p className="view-empty" role="status">
          No matches.
        </p>
      )}

      <div className="list-view">
        {results.map((result) => (
          <button
            key={result.node.id}
            type="button"
            className="node-row search-result-row"
            aria-current={result.node.id === selectedNodeId}
            onClick={() => onSelectNode(result.node.id)}
          >
            <span className="node-row-title">{result.node.title}</span>
            <span className="search-snippet">{renderSnippet(result.snippet)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
