import { Fragment, useEffect, useRef, useState } from 'react'
import type { SearchResponse, SearchResult, SearchScope } from '../types'
import { SegmentedControl } from './ui'

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
  /** Called when the user opens a result; lets the shell route to the right tab.
   * Falls back to onSelectNode(result.node.id) when absent. */
  onActivateResult?: (result: SearchResult) => void
}

export function SearchView({
  onSearch,
  selectedNodeId,
  onSelectNode,
  onActivateResult,
}: SearchViewProps) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<SearchScope>('all')
  const [results, setResults] = useState<SearchResult[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [isSearching, setIsSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [nextOffset, setNextOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [activeIndex, setActiveIndex] = useState(-1)
  const listRef = useRef<HTMLDivElement | null>(null)

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
      setActiveIndex(page.results.length > 0 ? 0 : -1)
      setError(null)
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : String(searchError))
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
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setIsSearching(false)
    }
  }

  function openResult(result: SearchResult) {
    if (onActivateResult) {
      onActivateResult(result)
      return
    }
    if (result.node?.id) onSelectNode(result.node.id)
  }

  function changeScope(next: SearchScope) {
    setScope(next)
    // Re-run immediately when a query is already present so scope feels live (ST-06 review).
    if (hasSearched && query.trim()) {
      void (async () => {
        setIsSearching(true)
        try {
          const page = await onSearch(query.trim(), next, 0)
          setResults(page.results)
          setHasMore(page.has_more)
          setNextOffset(page.offset + page.results.length)
          setTotal(page.total)
          setActiveIndex(page.results.length > 0 ? 0 : -1)
          setError(null)
        } catch (searchError) {
          setError(searchError instanceof Error ? searchError.message : String(searchError))
        } finally {
          setIsSearching(false)
        }
      })()
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      if (hasSearched && activeIndex >= 0 && results[activeIndex]) {
        openResult(results[activeIndex])
        return
      }
      void runSearch()
      return
    }
    if (!hasSearched || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((current) => Math.min(current + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((current) => Math.max(current - 1, 0))
    }
  }

  // Keep the highlighted row visible while arrowing through results.
  useEffect(() => {
    const container = listRef.current
    if (!container || activeIndex < 0) return
    const active = container.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)
    // jsdom (and some older engines) do not implement scrollIntoView.
    if (typeof active?.scrollIntoView === 'function') {
      active.scrollIntoView({ block: 'nearest' })
    }
  }, [activeIndex])

  const idle = !hasSearched

  return (
    <div className="search-view" aria-label="Search view">
      <div className={idle ? 'search-hero' : 'search-view-bar'}>
        <label className="sr-only" htmlFor="search-view-input">
          Search
        </label>
        {idle && (
          <>
            <p className="search-hero-title">Search your graph</p>
            <p className="search-hero-hint">
              Titles, bodies, and research identity across every domain.
            </p>
          </>
        )}
        <input
          id="search-view-input"
          type="text"
          placeholder={idle ? 'Type to search…' : 'Search titles, bodies, and research identity…'}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleInputKeyDown}
          autoFocus={idle}
          className={idle ? 'search-hero-input' : undefined}
        />
        {idle && (
          <div className="search-hero-scopes">
            <SegmentedControl
              ariaLabel="Search scope"
              options={SCOPES.map((option) => ({ value: option.value, label: option.label }))}
              value={scope}
              onChange={changeScope}
            />
            <button type="button" className="primary-action" onClick={runSearch} disabled={isSearching || !query.trim()}>
              Search
            </button>
          </div>
        )}
        {!idle && (
          <select
            id="search-view-scope"
            aria-label="Scope"
            className="wiki-chip-select"
            value={scope}
            onChange={(event) => changeScope(event.target.value as SearchScope)}
          >
            {SCOPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )}
        {!idle && (
          <button type="button" onClick={runSearch} disabled={isSearching || !query.trim()}>
            Search
          </button>
        )}
      </div>

      {error !== null && (
        <p className="view-empty" role="alert">
          Search failed: {error}
        </p>
      )}

      {!error && hasSearched && results.length === 0 && (
        <p className="view-empty" role="status">
          No matches.
        </p>
      )}

      {hasSearched && results.length > 0 && (
        <p className="search-count" role="status">
          {total} {total === 1 ? 'match' : 'matches'} · ↑↓ to navigate · Enter to open
        </p>
      )}

      <div className="list-view" ref={listRef}>
        {results.map((result, index) => {
          const title = result.node?.title ?? result.document?.title ?? result.goto ?? ''
          const key = result.goto ?? title
          const isActive = index === activeIndex
          return (
            <button
              key={key}
              data-index={index}
              type="button"
              className={
                isActive
                  ? 'node-row search-result-row search-result-active'
                  : 'node-row search-result-row'
              }
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
              </span>
              <span className="search-snippet">{renderSnippet(result.snippet)}</span>
            </button>
          )
        })}
      </div>

      {hasSearched && hasMore && results.length > 0 && (
        <button type="button" onClick={loadMore} disabled={isSearching}>
          {isSearching ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}
