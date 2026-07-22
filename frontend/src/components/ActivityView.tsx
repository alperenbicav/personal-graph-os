import { useEffect, useState } from 'react'
import type { ActivityEvent } from '../types'

interface ActivityViewProps {
  onLoadPage: (cursor: string | null) => Promise<{ events: ActivityEvent[]; next_cursor: string | null }>
}

/** Read-only activity/audit feed (ST-07.1). Filters, detail drill-in, and undo controls land
 * in ST-07.3; this view only proves the bounded, cursor-paginated read path end to end. */
export function ActivityView({ onLoadPage }: ActivityViewProps) {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  async function loadFirstPage() {
    setIsLoading(true)
    setLoadError(null)
    try {
      const page = await onLoadPage(null)
      setEvents(page.events)
      setCursor(page.next_cursor)
      setHasMore(page.next_cursor !== null)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsLoading(false)
    }
  }

  async function loadMore() {
    if (!cursor) return
    setIsLoading(true)
    setLoadError(null)
    try {
      const page = await onLoadPage(cursor)
      setEvents((previous) => [...previous, ...page.events])
      setCursor(page.next_cursor)
      setHasMore(page.next_cursor !== null)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadFirstPage()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="activity-view" aria-label="Activity view">
      {loadError && <p className="view-error">{loadError}</p>}
      {!loadError && events.length === 0 && !isLoading && (
        <p className="view-empty">No activity recorded yet.</p>
      )}

      <div className="list-view">
        {events.map((event) => (
          <div key={event.id} className="activity-row" data-undoable={event.is_undoable}>
            <span className="activity-row-actor">{event.actor_name}</span>
            <span className="activity-row-action">{event.action}</span>
            <span className="activity-row-entity">
              {event.entity_type} {event.entity_id}
            </span>
            <span className="activity-row-occurred-at">
              {new Date(event.occurred_at).toLocaleString()}
            </span>
          </div>
        ))}
      </div>

      {hasMore && (
        <button type="button" onClick={loadMore} disabled={isLoading}>
          Load more
        </button>
      )}
    </div>
  )
}
